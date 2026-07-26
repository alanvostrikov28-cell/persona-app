import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot as bot_module


class BotAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_opens_persona_without_channel_membership_check(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=123456))
        telegram_bot = object()

        with (
            patch.object(bot_module.database, "upsert_user") as upsert_user,
            patch.object(bot_module.database, "log_event") as log_event,
            patch.object(
                bot_module,
                "is_channel_member",
                new=AsyncMock(side_effect=AssertionError("membership check must not run")),
            ),
            patch.object(bot_module, "send_welcome", new=AsyncMock()) as send_welcome,
        ):
            await bot_module.start_handler(message, telegram_bot)

        upsert_user.assert_called_once_with(message.from_user)
        log_event.assert_called_once_with(message.from_user.id, "start")
        send_welcome.assert_awaited_once_with(message, telegram_bot)

    async def test_open_persona_button_does_not_require_channel_subscription(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=123456),
            answer=AsyncMock(),
        )
        telegram_bot = object()

        with (
            patch.object(bot_module.database, "upsert_user"),
            patch.object(
                bot_module,
                "is_channel_member",
                new=AsyncMock(side_effect=AssertionError("membership check must not run")),
            ),
        ):
            await bot_module.open_persona_handler(message, telegram_bot)

        message.answer.assert_awaited_once()
        self.assertEqual(
            message.answer.await_args.args[0],
            "Persona готова. Нажми кнопку ниже, чтобы открыть приложение.",
        )


if __name__ == "__main__":
    unittest.main()
