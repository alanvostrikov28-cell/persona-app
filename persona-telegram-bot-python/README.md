# PersonaMirror Bot

Автономный Telegram-бот Persona на Python 3.11+, `aiogram 3` и SQLite.
Веб-приложение остаётся отдельным проектом и открывается из постоянного нижнего меню.

## Что реализовано

- свободный вход в Persona без обязательной подписки на канал;
- приветствие с фирменным изображением сразу после команды `/start`;
- постоянная кнопка `🧠 Начать исследование`, открывающая Mini App;
- постоянная кнопка `⚡ Подписка` со сроком доступа и количеством оставшихся дней;
- кнопки `← Назад` во вложенных экранах;
- SQLite-база пользователей, подписок, платежей и событий;
- миграция доступа из старой таблицы `access` без потери существующих сроков;
- защита от повторного зачисления одного платежа по уникальному `payment_id`;
- API синхронизации подписки с Mini App;
- защищённый API активации после будущего webhook платёжной системы;
- прямой ResultURL webhook Robokassa с проверкой подписи, суммы и `Shp_tg_id`;
- админ-команды `/grant` и `/revoke`.

## Первый запуск

1. Создай локальный `.env`:

```powershell
cd persona-telegram-bot-python
.\setup_env.ps1
```

2. Установи зависимости:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

3. Запусти без изменения политики PowerShell:

```powershell
.\run_bot.cmd
```

После настройки для обычного запуска нужна только последняя команда.

## SQLite

База создаётся рядом с ботом:

```text
persona-telegram-bot-python/persona_bot.sqlite3
```

Основные таблицы:

- `users` — Telegram-профиль и Persona ID;
- `subscriptions` — статус и срок Persona Plus;
- `payment_events` — обработанные платежи;
- `events` — технический журнал.

Повторный запуск не обнуляет базу и не сокращает оплаченный срок. Новые 30 дней
добавляются к текущей дате окончания, если подписка ещё активна.

## API синхронизации

Бот поднимает локальный API на `127.0.0.1:8080`.

```text
GET  /health
GET  /api/access/full-access
POST /api/payments/activate
GET|POST /api/payments/robokassa/result
```

Mini App отправляет Telegram `initData` в заголовке `X-Telegram-Init-Data` и получает
актуальный срок из SQLite через `/api/access/full-access`.

Платёжный webhook после проверки подписи вызывает `/api/payments/activate`:

```json
{
  "telegram_id": 123456789,
  "payment_id": "payment-unique-id",
  "days": 30,
  "amount": 199,
  "currency": "RUB",
  "source": "robokassa"
}
```

Для вызова требуется заголовок `X-API-Key`, равный `ACCESS_API_SECRET` из `.env`.
Сам API необходимо разместить на сервере с HTTPS, чтобы GitHub Pages и платёжный
webhook могли обращаться к нему из интернета.

### Robokassa ResultURL

В личном кабинете Robokassa укажи публичный HTTPS-адрес:

```text
https://YOUR-DOMAIN/api/payments/robokassa/result
```

Метод можно выбрать `POST` (рекомендуется). В `.env` должны быть настроены:

```text
ROBOKASSA_PASSWORD_2=...
ROBOKASSA_TEST_PASSWORD_2=...
ROBOKASSA_HASH_ALGORITHM=md5
ROBOKASSA_EXPECTED_AMOUNT=199
```

Пароль №2 должен совпадать с настройками магазина, а алгоритм — с выбранным
алгоритмом ResultURL. Платёж обязан содержать пользовательский параметр
`Shp_tg_id=TELEGRAM_ID`. Этот параметр входит в проверяемую подпись и связывает
оплату с конкретным профилем Persona. Общий платёжный виджет без `Shp_tg_id`
намеренно не выдаёт доступ: иначе невозможно безопасно определить покупателя.

Повторное уведомление с тем же `InvId` получает ответ `OK{InvId}`, но срок
подписки второй раз не увеличивается.

## Админ-команды

```text
/grant TG-123456789
/grant 123456789 30
/revoke TG-123456789
```

Доступ к командам есть только у Telegram ID из `ADMIN_IDS`.

## Аналитика пользователей

Mini App сохраняет в SQLite запуски, заполнение профиля, подписку на канал и
статус Persona Plus. Локальная Excel-книга обновляется командой:

```powershell
.\sync_analytics_excel.ps1
```

Подробная настройка и автоматическое обновление описаны в
[`ANALYTICS.md`](ANALYTICS.md).

## Важно

Токен хранится только в `.env`; файл уже исключён из Git. Не добавляй токен в
`bot.py`, README или публичный репозиторий.
