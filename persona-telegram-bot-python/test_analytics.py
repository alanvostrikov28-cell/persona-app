import tempfile
import unittest
from pathlib import Path

from bot import Database


class AnalyticsDatabaseTests(unittest.TestCase):
    def test_app_open_is_unique_per_session_and_updates_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "analytics.sqlite3")
            database.initialize()
            database.upsert_telegram_profile(
                {
                    "id": 12345,
                    "username": "persona_user",
                    "first_name": "Persona",
                    "last_name": "User",
                }
            )

            first = database.record_app_open(
                12345,
                session_id="session-one",
                completed_tests=2,
                profile_completion=25,
                platform="android",
                app_version="test",
            )
            duplicate = database.record_app_open(
                12345,
                session_id="session-one",
                completed_tests=3,
                profile_completion=38,
                platform="android",
                app_version="test",
            )

            users = database.analytics_users()
            self.assertTrue(first)
            self.assertFalse(duplicate)
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0]["app_open_count"], 1)
            self.assertEqual(users[0]["completed_tests"], 3)
            self.assertEqual(users[0]["profile_completion"], 38)
            self.assertEqual(users[0]["username"], "persona_user")
            self.assertEqual(users[0]["full_name"], "Persona User")

    def test_missing_identity_users_are_available_for_backfill(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "analytics.sqlite3")
            database.initialize()
            database.ensure_user(777)
            database.upsert_telegram_profile(
                {
                    "id": 888,
                    "username": "known_user",
                    "first_name": "Known",
                    "last_name": "",
                }
            )

            self.assertEqual(database.users_missing_identity(), [777])

    def test_analytics_reflect_channel_and_subscription(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "analytics.sqlite3")
            database.initialize()
            database.ensure_user(777)
            database.mark_channel_status(777, True)
            database.grant_subscription(
                777,
                days=30,
                source="test",
                payment_id="payment:analytics",
                amount=199,
            )

            user = database.analytics_users()[0]
            self.assertTrue(user["channel_subscribed"])
            self.assertTrue(user["plus_active"])
            self.assertEqual(user["payment_count"], 1)
            self.assertEqual(user["total_paid"], 199)


if __name__ == "__main__":
    unittest.main()
