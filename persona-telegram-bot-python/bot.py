import asyncio
import hashlib
import hmac
import json
import math
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)


BASE_DIR = Path(__file__).resolve().parent


def load_env(path: Path = BASE_DIR / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEB_APP_URL = os.getenv(
    "WEB_APP_URL", "https://alanvostrikov28-cell.github.io/persona-app/"
).strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "personamirrornews").strip().lstrip("@")
CHANNEL_ID = f"@{CHANNEL_USERNAME}"
CHANNEL_URL = f"https://t.me/{CHANNEL_USERNAME}"
DATABASE_PATH = BASE_DIR / os.getenv("DATABASE_PATH", "persona_bot.sqlite3")
WELCOME_IMAGE = BASE_DIR / os.getenv("WELCOME_IMAGE", "assets/persona-welcome.png")
ACCESS_DAYS = int(os.getenv("ACCESS_DAYS", "30"))
PRICE_RUB = int(os.getenv("PRICE_RUB", "199"))
ROBOKASSA_MERCHANT_LOGIN = os.getenv("ROBOKASSA_MERCHANT_LOGIN", "").strip()
ROBOKASSA_PASSWORD_1 = os.getenv("ROBOKASSA_PASSWORD_1", "").strip()
ROBOKASSA_TEST_PASSWORD_1 = os.getenv("ROBOKASSA_TEST_PASSWORD_1", "").strip()
ROBOKASSA_PASSWORD_2 = os.getenv("ROBOKASSA_PASSWORD_2", "").strip()
ROBOKASSA_TEST_PASSWORD_2 = os.getenv("ROBOKASSA_TEST_PASSWORD_2", "").strip()
ROBOKASSA_HASH_ALGORITHM = os.getenv("ROBOKASSA_HASH_ALGORITHM", "md5").strip().lower()
ROBOKASSA_TEST_MODE = os.getenv("ROBOKASSA_TEST_MODE", "true").lower() == "true"
ROBOKASSA_PAYMENT_URL = os.getenv(
    "ROBOKASSA_PAYMENT_URL", "https://auth.robokassa.ru/Merchant/Index.aspx"
).strip()
ROBOKASSA_EXPECTED_AMOUNT = Decimal(
    os.getenv("ROBOKASSA_EXPECTED_AMOUNT", str(PRICE_RUB)).strip()
)
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8080"))
ACCESS_API_SECRET = os.getenv("ACCESS_API_SECRET", "").strip()
ANALYTICS_API_SECRET = os.getenv("ANALYTICS_API_SECRET", ACCESS_API_SECRET).strip()
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*").strip()
DROP_PENDING_UPDATES = os.getenv("DROP_PENDING_UPDATES", "false").lower() == "true"
ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip().isdigit()
}

router = Router()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def persona_id(telegram_id: int) -> str:
    return f"TG-{telegram_id}"


def robokassa_signature(
    out_sum: str,
    invoice_id: str,
    password_2: str,
    custom_parameters: dict[str, str] | None = None,
    algorithm: str = ROBOKASSA_HASH_ALGORITHM,
) -> str:
    algorithms = {
        "md5": "md5",
        "sha1": "sha1",
        "sha256": "sha256",
        "sha384": "sha384",
        "sha512": "sha512",
    }
    hash_name = algorithms.get(algorithm.lower())
    if not hash_name:
        raise ValueError(f"Unsupported Robokassa hash algorithm: {algorithm}")
    signature_parts = [out_sum, invoice_id, password_2]
    for key, value in sorted((custom_parameters or {}).items()):
        signature_parts.append(f"{key}={value}")
    source = ":".join(signature_parts)
    return hashlib.new(hash_name, source.encode("utf-8")).hexdigest()


def robokassa_payment_signature(
    merchant_login: str,
    out_sum: str,
    invoice_id: str,
    password_1: str,
    custom_parameters: dict[str, str] | None = None,
    algorithm: str = ROBOKASSA_HASH_ALGORITHM,
) -> str:
    signature_parts = [merchant_login, out_sum, invoice_id, password_1]
    for key, value in sorted((custom_parameters or {}).items()):
        signature_parts.append(f"{key}={value}")
    hash_name = algorithm.lower()
    if hash_name not in {"md5", "sha1", "sha256", "sha384", "sha512"}:
        raise ValueError(f"Unsupported Robokassa hash algorithm: {algorithm}")
    return hashlib.new(hash_name, ":".join(signature_parts).encode("utf-8")).hexdigest()


def robokassa_payment_url(
    merchant_login: str,
    out_sum: str,
    invoice_id: int,
    password_1: str,
    telegram_id: int,
    *,
    test_mode: bool,
) -> str:
    custom_parameters = {"Shp_tg_id": str(telegram_id)}
    signature = robokassa_payment_signature(
        merchant_login,
        out_sum,
        str(invoice_id),
        password_1,
        custom_parameters,
    )
    parameters = {
        "MerchantLogin": merchant_login,
        "OutSum": out_sum,
        "InvId": str(invoice_id),
        "Description": "Persona Plus - 30 days",
        "SignatureValue": signature,
        "Culture": "ru",
        "Encoding": "utf-8",
        **custom_parameters,
    }
    if test_mode:
        parameters["IsTest"] = "1"
    return f"{ROBOKASSA_PAYMENT_URL}?{urlencode(parameters)}"


def robokassa_telegram_id(parameters: dict[str, str]) -> int:
    raw_value = parameters.get("Shp_tg_id", "").strip().replace("TG-", "")
    if not raw_value.isdigit() or int(raw_value) <= 0:
        raise ValueError("Signed Shp_tg_id is missing or invalid")
    return int(raw_value)


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    tg_id INTEGER PRIMARY KEY,
                    persona_id TEXT UNIQUE NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    channel_verified_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    tg_id INTEGER PRIMARY KEY,
                    persona_id TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL DEFAULT 'inactive',
                    starts_at TEXT,
                    expires_at TEXT,
                    source TEXT,
                    payment_id TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS payment_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_id TEXT UNIQUE NOT NULL,
                    tg_id INTEGER NOT NULL,
                    amount INTEGER,
                    currency TEXT,
                    source TEXT NOT NULL,
                    payload TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS payment_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    paid_at TEXT,
                    FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER,
                    event_type TEXT NOT NULL,
                    payload TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    tg_id INTEGER NOT NULL,
                    opened_at TEXT NOT NULL,
                    completed_tests INTEGER NOT NULL DEFAULT 0,
                    profile_completion INTEGER NOT NULL DEFAULT 0,
                    platform TEXT,
                    app_version TEXT,
                    UNIQUE(tg_id, session_id),
                    FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_subscriptions_expires
                    ON subscriptions(expires_at);
                CREATE INDEX IF NOT EXISTS idx_payment_orders_user
                    ON payment_orders(tg_id, status);
                CREATE INDEX IF NOT EXISTS idx_app_sessions_opened
                    ON app_sessions(opened_at);
                """
            )
            self._add_missing_user_columns(connection)
            self._migrate_legacy_access(connection)
            connection.commit()

    @staticmethod
    def _add_missing_user_columns(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        if "channel_verified_at" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN channel_verified_at TEXT")
        additions = {
            "channel_subscribed": "INTEGER",
            "channel_checked_at": "TEXT",
            "first_app_open_at": "TEXT",
            "last_app_open_at": "TEXT",
            "app_open_count": "INTEGER NOT NULL DEFAULT 0",
            "completed_tests": "INTEGER NOT NULL DEFAULT 0",
            "profile_completion": "INTEGER NOT NULL DEFAULT 0",
            "app_version": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")

    @staticmethod
    def _migrate_legacy_access(connection: sqlite3.Connection) -> None:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='access'"
        ).fetchone()
        if not table:
            return
        connection.execute(
            """
            INSERT OR IGNORE INTO subscriptions (
                tg_id, persona_id, status, starts_at, expires_at, source, updated_at
            )
            SELECT
                tg_id,
                persona_id,
                CASE WHEN active = 1 THEN 'active' ELSE 'inactive' END,
                starts_at,
                expires_at,
                COALESCE(source, 'legacy'),
                updated_at
            FROM access
            """
        )

    def upsert_user(self, user) -> None:
        now = to_iso()
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT INTO users (
                    tg_id, persona_id, username, first_name, last_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    updated_at = excluded.updated_at
                """,
                (
                    user.id,
                    persona_id(user.id),
                    user.username or "",
                    user.first_name or "",
                    user.last_name or "",
                    now,
                    now,
                ),
            )
            connection.commit()

    def upsert_telegram_profile(self, user: dict) -> None:
        telegram_id = int(user["id"])
        now = to_iso()
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT INTO users (
                    tg_id, persona_id, username, first_name, last_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    updated_at = excluded.updated_at
                """,
                (
                    telegram_id,
                    persona_id(telegram_id),
                    str(user.get("username") or ""),
                    str(user.get("first_name") or ""),
                    str(user.get("last_name") or ""),
                    now,
                    now,
                ),
            )
            connection.commit()

    def ensure_user(self, telegram_id: int) -> None:
        now = to_iso()
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO users (
                    tg_id, persona_id, username, first_name, last_name, created_at, updated_at
                ) VALUES (?, ?, '', '', '', ?, ?)
                """,
                (telegram_id, persona_id(telegram_id), now, now),
            )
            connection.commit()

    def users_missing_identity(self) -> list[int]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT tg_id
                FROM users
                WHERE COALESCE(username, '') = ''
                   OR COALESCE(first_name, '') = ''
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [int(row["tg_id"]) for row in rows]

    def create_payment_order(self, telegram_id: int, amount: int) -> int:
        self.ensure_user(telegram_id)
        with closing(self.connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO payment_orders (tg_id, amount, status, created_at)
                VALUES (?, ?, 'pending', ?)
                """,
                (telegram_id, amount, to_iso()),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def get_payment_order(self, invoice_id: int) -> dict | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM payment_orders WHERE id = ?", (invoice_id,)
            ).fetchone()
        return dict(row) if row else None

    def mark_payment_order_paid(self, invoice_id: int) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """
                UPDATE payment_orders
                SET status = 'paid', paid_at = COALESCE(paid_at, ?)
                WHERE id = ?
                """,
                (to_iso(), invoice_id),
            )
            connection.commit()

    def mark_channel_verified(self, telegram_id: int) -> None:
        self.mark_channel_status(telegram_id, True)

    def mark_channel_status(self, telegram_id: int, subscribed: bool) -> None:
        now = to_iso()
        with closing(self.connect()) as connection:
            connection.execute(
                """
                UPDATE users
                SET channel_subscribed = ?,
                    channel_checked_at = ?,
                    channel_verified_at = CASE WHEN ? = 1 THEN ? ELSE channel_verified_at END,
                    updated_at = ?
                WHERE tg_id = ?
                """,
                (int(subscribed), now, int(subscribed), now, now, telegram_id),
            )
            connection.commit()

    def record_app_open(
        self,
        telegram_id: int,
        *,
        session_id: str,
        completed_tests: int,
        profile_completion: int,
        platform: str,
        app_version: str,
    ) -> bool:
        now = to_iso()
        completed_tests = max(0, min(100, int(completed_tests)))
        profile_completion = max(0, min(100, int(profile_completion)))
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO app_sessions (
                    session_id, tg_id, opened_at, completed_tests,
                    profile_completion, platform, app_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    telegram_id,
                    now,
                    completed_tests,
                    profile_completion,
                    platform,
                    app_version,
                ),
            )
            is_new_session = cursor.rowcount > 0
            connection.execute(
                """
                UPDATE users
                SET first_app_open_at = COALESCE(first_app_open_at, ?),
                    last_app_open_at = ?,
                    app_open_count = app_open_count + ?,
                    completed_tests = ?,
                    profile_completion = ?,
                    app_version = ?,
                    updated_at = ?
                WHERE tg_id = ?
                """,
                (
                    now,
                    now,
                    int(is_new_session),
                    completed_tests,
                    profile_completion,
                    app_version,
                    now,
                    telegram_id,
                ),
            )
            connection.commit()
        return is_new_session

    def analytics_users(self) -> list[dict]:
        now = utc_now()
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                    u.tg_id,
                    u.persona_id,
                    u.username,
                    u.first_name,
                    u.last_name,
                    u.channel_subscribed,
                    u.channel_checked_at,
                    u.created_at,
                    u.updated_at,
                    u.first_app_open_at,
                    u.last_app_open_at,
                    u.app_open_count,
                    u.completed_tests,
                    u.profile_completion,
                    u.app_version,
                    s.status AS subscription_status,
                    s.starts_at,
                    s.expires_at,
                    s.source AS subscription_source,
                    COALESCE(p.payment_count, 0) AS payment_count,
                    COALESCE(p.total_paid, 0) AS total_paid,
                    p.last_payment_at
                FROM users u
                LEFT JOIN subscriptions s ON s.tg_id = u.tg_id
                LEFT JOIN (
                    SELECT
                        tg_id,
                        COUNT(*) AS payment_count,
                        COALESCE(SUM(amount), 0) AS total_paid,
                        MAX(created_at) AS last_payment_at
                    FROM payment_events
                    GROUP BY tg_id
                ) p ON p.tg_id = u.tg_id
                ORDER BY COALESCE(u.last_app_open_at, u.updated_at) DESC
                """
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            expires_at = parse_datetime(item.get("expires_at"))
            plus_active = (
                item.get("subscription_status") == "active"
                and bool(expires_at and expires_at > now)
            )
            seconds_left = max(0, (expires_at - now).total_seconds()) if expires_at else 0
            item["plus_active"] = plus_active
            item["days_left"] = math.ceil(seconds_left / 86400) if plus_active else 0
            item["channel_subscribed"] = (
                None
                if item.get("channel_subscribed") is None
                else bool(item["channel_subscribed"])
            )
            item["full_name"] = " ".join(
                part for part in [item.get("first_name"), item.get("last_name")] if part
            )
            result.append(item)
        return result

    def get_subscription(self, telegram_id: int) -> dict:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM subscriptions WHERE tg_id = ?", (telegram_id,)
            ).fetchone()
        if not row:
            return {
                "active": False,
                "persona_id": persona_id(telegram_id),
                "expires_at": None,
                "days_left": 0,
            }
        expires_at = parse_datetime(row["expires_at"])
        active = row["status"] == "active" and bool(expires_at and expires_at > utc_now())
        seconds_left = max(0, (expires_at - utc_now()).total_seconds()) if expires_at else 0
        return {
            "active": active,
            "persona_id": row["persona_id"],
            "starts_at": row["starts_at"],
            "expires_at": row["expires_at"],
            "days_left": math.ceil(seconds_left / 86400) if active else 0,
            "source": row["source"],
        }

    def grant_subscription(
        self,
        telegram_id: int,
        *,
        days: int = ACCESS_DAYS,
        source: str = "payment",
        payment_id: str | None = None,
        amount: int | None = None,
        currency: str = "RUB",
        payload: dict | None = None,
    ) -> tuple[dict, bool]:
        now = utc_now()
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if payment_id:
                duplicate = connection.execute(
                    "SELECT id FROM payment_events WHERE payment_id = ?", (payment_id,)
                ).fetchone()
                if duplicate:
                    connection.rollback()
                    return self.get_subscription(telegram_id), False

            current = connection.execute(
                "SELECT expires_at FROM subscriptions WHERE tg_id = ?", (telegram_id,)
            ).fetchone()
            current_expires = parse_datetime(current["expires_at"]) if current else None
            starts_at = current_expires if current_expires and current_expires > now else now
            expires_at = starts_at + timedelta(days=days)
            connection.execute(
                """
                INSERT INTO subscriptions (
                    tg_id, persona_id, status, starts_at, expires_at,
                    source, payment_id, updated_at
                ) VALUES (?, ?, 'active', ?, ?, ?, ?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET
                    status = 'active',
                    starts_at = excluded.starts_at,
                    expires_at = excluded.expires_at,
                    source = excluded.source,
                    payment_id = excluded.payment_id,
                    updated_at = excluded.updated_at
                """,
                (
                    telegram_id,
                    persona_id(telegram_id),
                    to_iso(starts_at),
                    to_iso(expires_at),
                    source,
                    payment_id,
                    to_iso(now),
                ),
            )
            if payment_id:
                connection.execute(
                    """
                    INSERT INTO payment_events (
                        payment_id, tg_id, amount, currency, source, payload, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payment_id,
                        telegram_id,
                        amount,
                        currency,
                        source,
                        json.dumps(payload or {}, ensure_ascii=False),
                        to_iso(now),
                    ),
                )
            connection.commit()
        return self.get_subscription(telegram_id), True

    def revoke_subscription(self, telegram_id: int) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                "UPDATE subscriptions SET status = 'inactive', updated_at = ? WHERE tg_id = ?",
                (to_iso(), telegram_id),
            )
            connection.commit()

    def log_event(self, telegram_id: int | None, event_type: str, payload: dict | None = None) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                "INSERT INTO events (tg_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (
                    telegram_id,
                    event_type,
                    json.dumps(payload or {}, ensure_ascii=False),
                    to_iso(),
                ),
            )
            connection.commit()


database = Database(DATABASE_PATH)


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🧠 Начать исследование")
            ],
            [KeyboardButton(text="⚡ Подписка")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выбери раздел Persona",
    )


def open_persona_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧠 Открыть Persona",
                    web_app=WebAppInfo(url=WEB_APP_URL),
                )
            ]
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")]
        ]
    )


WELCOME_CAPTION = (
    "<b>Добро пожаловать в Persona!</b>\n\n"
    "Экосистема, которая <b>ПОЛНОСТЬЮ</b> отображает твой психологический портрет!\n\n"
    "Проходи тесты → Заполняй свой профиль\n\n"
    "Что у нас есть?\n"
    "<blockquote>📊 Психологический профиль\n\n"
    "❤️ Совместимость\n\n"
    "🛡 Парные тесты <i>(в разработке)</i>\n\n"
    "⚔️ Дуэли <i>(в разработке)</i>\n\n"
    "✨ Ежедневные инсайты</blockquote>"
)


async def is_channel_member(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        print(f"Channel membership check failed: {error}")
        return False
    if member.status in {
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.MEMBER,
    }:
        return True
    return member.status == ChatMemberStatus.RESTRICTED and bool(
        getattr(member, "is_member", False)
    )


async def send_welcome(message: Message, bot: Bot) -> None:
    if WELCOME_IMAGE.exists():
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=FSInputFile(WELCOME_IMAGE),
            caption=WELCOME_CAPTION,
            reply_markup=main_menu(),
        )
    else:
        await message.answer(
            WELCOME_CAPTION, reply_markup=main_menu()
        )


async def ensure_access_to_menu(message: Message, bot: Bot) -> bool:
    # Channel membership is collected separately for analytics and never blocks Persona.
    database.upsert_user(message.from_user)
    return True


@router.message(CommandStart())
async def start_handler(message: Message, bot: Bot) -> None:
    if await ensure_access_to_menu(message, bot):
        database.log_event(message.from_user.id, "start")
        await send_welcome(message, bot)


@router.message(F.text == "⚡ Подписка")
async def subscription_handler(message: Message, bot: Bot) -> None:
    if not await ensure_access_to_menu(message, bot):
        return
    subscription = database.get_subscription(message.from_user.id)
    if subscription["active"]:
        expires_at = parse_datetime(subscription["expires_at"])
        text = (
            "<b>⚡ Persona Plus активна</b>\n\n"
            f"Осталось: <b>{subscription['days_left']} дн.</b>\n"
            f"Доступ до: <b>{expires_at.astimezone().strftime('%d.%m.%Y')}</b>\n\n"
            "После успешного продления новый срок появится здесь автоматически."
        )
    else:
        text = (
            "<b>⚡ Persona Plus не активна</b>\n\n"
            f"Полный профиль, совместимость и закрытые инсайты — {PRICE_RUB} ₽ за {ACCESS_DAYS} дней.\n\n"
            "Оформить доступ можно внутри приложения Persona."
        )
    await message.answer(text, reply_markup=back_keyboard())


@router.message(F.text == "🧠 Начать исследование")
async def open_persona_handler(message: Message, bot: Bot) -> None:
    if not await ensure_access_to_menu(message, bot):
        return
    await message.answer(
        "Persona готова. Нажми кнопку ниже, чтобы открыть приложение.",
        reply_markup=open_persona_keyboard(),
    )


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        await callback.message.answer(
            "Главное меню Persona", reply_markup=main_menu()
        )


@router.message(Command("grant"))
async def grant_handler(message: Message, bot: Bot) -> None:
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда доступна только администратору.")
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].replace("TG-", "").isdigit():
        await message.answer("Формат: /grant TG_ID [дней]")
        return
    telegram_id = int(parts[1].replace("TG-", ""))
    days = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else ACCESS_DAYS
    subscription, _ = database.grant_subscription(
        telegram_id, days=days, source="telegram_admin"
    )
    await message.answer(
        f"Доступ {subscription['persona_id']} продлён на {days} дней."
    )
    try:
        await bot.send_message(
            telegram_id,
            "<b>Оплата подтверждена</b>\n\n"
            f"Persona Plus активна. Осталось: <b>{subscription['days_left']} дн.</b>",
            reply_markup=main_menu(),
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer("Доступ сохранён, но уведомление пользователю не доставлено.")


@router.message(Command("revoke"))
async def revoke_handler(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда доступна только администратору.")
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].replace("TG-", "").isdigit():
        await message.answer("Формат: /revoke TG_ID")
        return
    telegram_id = int(parts[1].replace("TG-", ""))
    database.revoke_subscription(telegram_id)
    await message.answer(f"Доступ TG-{telegram_id} отключён.")


@router.message()
async def fallback_handler(message: Message, bot: Bot) -> None:
    if await ensure_access_to_menu(message, bot):
        await message.answer(
            "Выбери нужный раздел в меню ниже.",
            reply_markup=main_menu(),
        )


def validate_telegram_init_data(init_data: str) -> dict:
    if not init_data:
        raise ValueError("Telegram initData is missing")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    auth_date = int(values.get("auth_date", "0"))
    if not received_hash or abs(int(utc_now().timestamp()) - auth_date) > 86400:
        raise ValueError("Telegram initData is expired or invalid")
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated = hmac.new(
        secret, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise ValueError("Telegram initData signature is invalid")
    return json.loads(values["user"])


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-API-Key, X-Telegram-Init-Data"
    )
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Cache-Control"] = "no-store"
    return response


async def health_handler(_request: web.Request) -> web.Response:
    checkout_password = (
        ROBOKASSA_TEST_PASSWORD_1 if ROBOKASSA_TEST_MODE else ROBOKASSA_PASSWORD_1
    )
    return web.json_response(
        {
            "ok": True,
            "service": "persona-telegram-bot",
            "robokassaConfigured": bool(ROBOKASSA_PASSWORD_2),
            "robokassaCheckoutConfigured": bool(
                ROBOKASSA_MERCHANT_LOGIN and checkout_password
            ),
            "robokassaTestMode": ROBOKASSA_TEST_MODE,
        }
    )


async def access_handler(request: web.Request) -> web.Response:
    try:
        user = validate_telegram_init_data(
            request.headers.get("X-Telegram-Init-Data", "")
        )
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        return web.json_response({"ok": False, "error": str(error)}, status=401)
    subscription = database.get_subscription(int(user["id"]))
    return web.json_response(
        {
            "ok": True,
            "active": subscription["active"],
            "personaId": subscription["persona_id"],
            "expiresAt": subscription["expires_at"],
            "daysLeft": subscription["days_left"],
            "source": subscription.get("source"),
            "serverNow": to_iso(),
        }
    )


async def analytics_app_open_handler(request: web.Request) -> web.Response:
    try:
        user = validate_telegram_init_data(
            request.headers.get("X-Telegram-Init-Data", "")
        )
        payload = await request.json()
        telegram_id = int(user["id"])
        session_id = str(payload["sessionId"]).strip()
        if not session_id or len(session_id) > 80:
            raise ValueError("Invalid sessionId")
        completed_tests = int(payload.get("completedTests", 0))
        profile_completion = int(payload.get("profileCompletion", 0))
        platform = str(payload.get("platform", ""))[:40]
        app_version = str(payload.get("appVersion", ""))[:40]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return web.json_response({"ok": False, "error": str(error)}, status=400)

    database.upsert_telegram_profile(user)
    bot: Bot = request.app["bot"]
    subscribed = await is_channel_member(bot, telegram_id)
    database.mark_channel_status(telegram_id, subscribed)
    created = database.record_app_open(
        telegram_id,
        session_id=session_id,
        completed_tests=completed_tests,
        profile_completion=profile_completion,
        platform=platform,
        app_version=app_version,
    )
    return web.json_response(
        {
            "ok": True,
            "newSession": created,
            "channelSubscribed": subscribed,
            "serverNow": to_iso(),
        }
    )


async def analytics_users_handler(request: web.Request) -> web.Response:
    if not ANALYTICS_API_SECRET or not hmac.compare_digest(
        request.headers.get("X-API-Key", ""), ANALYTICS_API_SECRET
    ):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)
    users = database.analytics_users()
    return web.json_response(
        {
            "ok": True,
            "generatedAt": to_iso(),
            "summary": {
                "users": len(users),
                "channelSubscribers": sum(
                    1 for user in users if user["channel_subscribed"] is True
                ),
                "plusActive": sum(1 for user in users if user["plus_active"]),
                "paidUsers": sum(1 for user in users if user["payment_count"] > 0),
                "revenueRub": sum(int(user["total_paid"]) for user in users),
                "appOpens": sum(int(user["app_open_count"]) for user in users),
            },
            "users": users,
        }
    )


async def activate_payment_handler(request: web.Request) -> web.Response:
    if not ACCESS_API_SECRET or not hmac.compare_digest(
        request.headers.get("X-API-Key", ""), ACCESS_API_SECRET
    ):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)
    try:
        payload = await request.json()
        telegram_id = int(str(payload["telegram_id"]).replace("TG-", ""))
        payment_id = str(payload["payment_id"]).strip()
        days = int(payload.get("days", ACCESS_DAYS))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return web.json_response({"ok": False, "error": "Invalid payload"}, status=400)
    subscription, created = database.grant_subscription(
        telegram_id,
        days=days,
        source=str(payload.get("source", "payment_webhook")),
        payment_id=payment_id,
        amount=payload.get("amount"),
        currency=str(payload.get("currency", "RUB")),
        payload=payload,
    )
    if created:
        bot: Bot = request.app["bot"]
        try:
            await bot.send_message(
                telegram_id,
                "<b>Оплата прошла успешно</b>\n\n"
                f"Persona Plus активна. Осталось: <b>{subscription['days_left']} дн.</b>",
                reply_markup=main_menu(),
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
    return web.json_response(
        {"ok": True, "created": created, "subscription": subscription}
    )


async def create_robokassa_payment_handler(request: web.Request) -> web.Response:
    try:
        user = validate_telegram_init_data(
            request.headers.get("X-Telegram-Init-Data", "")
        )
        telegram_id = int(user["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return web.json_response({"ok": False, "error": str(error)}, status=401)

    password_1 = (
        ROBOKASSA_TEST_PASSWORD_1 if ROBOKASSA_TEST_MODE else ROBOKASSA_PASSWORD_1
    )
    if not ROBOKASSA_MERCHANT_LOGIN or not password_1:
        return web.json_response(
            {"ok": False, "error": "Robokassa checkout is not configured"},
            status=503,
        )

    amount = f"{ROBOKASSA_EXPECTED_AMOUNT.quantize(Decimal('0.01')):.2f}"
    invoice_id = database.create_payment_order(telegram_id, int(Decimal(amount)))
    payment_url = robokassa_payment_url(
        ROBOKASSA_MERCHANT_LOGIN,
        amount,
        invoice_id,
        password_1,
        telegram_id,
        test_mode=ROBOKASSA_TEST_MODE,
    )
    return web.json_response(
        {
            "ok": True,
            "paymentUrl": payment_url,
            "invoiceId": invoice_id,
            "amount": amount,
            "testMode": ROBOKASSA_TEST_MODE,
        }
    )


async def robokassa_result_handler(request: web.Request) -> web.Response:
    if request.method == "GET":
        parameters = {key: value for key, value in request.query.items()}
    else:
        form = await request.post()
        parameters = {key: str(value) for key, value in form.items()}

    out_sum = parameters.get("OutSum", "").strip()
    invoice_id = parameters.get("InvId", parameters.get("InvID", "")).strip()
    received_signature = parameters.get("SignatureValue", "").strip().lower()
    is_test = parameters.get("IsTest", "") == "1"
    password_2 = ROBOKASSA_TEST_PASSWORD_2 if is_test else ROBOKASSA_PASSWORD_2
    if not password_2:
        return web.Response(text="Robokassa webhook is not configured", status=503)
    if not out_sum or not invoice_id or not received_signature:
        return web.Response(text="Required Robokassa fields are missing", status=400)

    custom_parameters = {
        key: value for key, value in parameters.items() if key.startswith("Shp_")
    }
    expected_signature = robokassa_signature(
        out_sum,
        invoice_id,
        password_2,
        custom_parameters,
    )
    if not hmac.compare_digest(received_signature, expected_signature.lower()):
        return web.Response(text="Invalid Robokassa signature", status=400)

    try:
        amount = Decimal(out_sum).quantize(Decimal("0.01"))
        telegram_id = robokassa_telegram_id(custom_parameters)
        numeric_invoice_id = int(invoice_id)
    except (InvalidOperation, ValueError):
        return web.Response(text="Invalid payment parameters", status=400)
    if amount != ROBOKASSA_EXPECTED_AMOUNT.quantize(Decimal("0.01")):
        return web.Response(text="Unexpected payment amount", status=400)

    order = database.get_payment_order(numeric_invoice_id)
    if not order:
        return web.Response(text="Unknown payment order", status=400)
    if int(order["tg_id"]) != telegram_id or int(order["amount"]) != int(amount):
        return web.Response(text="Payment order does not match", status=400)

    safe_payload = {
        key: value
        for key, value in parameters.items()
        if key in {"OutSum", "InvId", "InvID", "IsTest", "PaymentMethod", "IncCurrLabel"}
        or key.startswith("Shp_")
    }
    database.ensure_user(telegram_id)
    subscription, created = database.grant_subscription(
        telegram_id,
        days=ACCESS_DAYS,
        source="robokassa_test" if is_test else "robokassa",
        payment_id=f"robokassa:{invoice_id}",
        amount=int(amount),
        currency="RUB",
        payload=safe_payload,
    )
    if created:
        database.mark_payment_order_paid(numeric_invoice_id)
        bot: Bot = request.app["bot"]
        try:
            await bot.send_message(
                telegram_id,
                "<b>Оплата прошла успешно</b>\n\n"
                f"Persona Plus активна. Осталось: <b>{subscription['days_left']} дн.</b>",
                reply_markup=main_menu(),
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
    return web.Response(text=f"OK{invoice_id}", content_type="text/plain")


async def start_api(bot: Bot) -> web.AppRunner:
    app = web.Application(middlewares=[cors_middleware])
    app["bot"] = bot
    app.router.add_get("/health", health_handler)
    app.router.add_get("/api/access/full-access", access_handler)
    app.router.add_post("/api/analytics/app-open", analytics_app_open_handler)
    app.router.add_get("/api/admin/analytics/users", analytics_users_handler)
    app.router.add_post("/api/payments/activate", activate_payment_handler)
    app.router.add_post(
        "/api/payments/robokassa/create", create_robokassa_payment_handler
    )
    app.router.add_get("/api/payments/robokassa/result", robokassa_result_handler)
    app.router.add_post("/api/payments/robokassa/result", robokassa_result_handler)
    app.router.add_options("/{path:.*}", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, API_HOST, API_PORT).start()
    print(f"Access API: http://{API_HOST}:{API_PORT}")
    return runner


async def backfill_telegram_profiles(bot: Bot) -> None:
    for telegram_id in database.users_missing_identity():
        try:
            chat = await bot.get_chat(telegram_id)
            database.upsert_telegram_profile(
                {
                    "id": chat.id,
                    "username": chat.username,
                    "first_name": chat.first_name,
                    "last_name": chat.last_name,
                }
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            continue


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Run setup_env.ps1 first.")
    if not WEB_APP_URL.startswith("https://"):
        raise RuntimeError("WEB_APP_URL must use HTTPS for Telegram Web App buttons.")
    database.initialize()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await bot.delete_webhook(drop_pending_updates=DROP_PENDING_UPDATES)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть Persona"),
        ]
    )
    api_runner = await start_api(bot)
    me = await bot.get_me()
    await backfill_telegram_profiles(bot)
    print(f"Persona bot @{me.username} is running. Database: {DATABASE_PATH}")
    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        await api_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
