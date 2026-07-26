import hashlib
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bot import (
    Database,
    robokassa_payment_signature,
    robokassa_payment_url,
    robokassa_signature,
    robokassa_telegram_id,
)


class RobokassaTests(unittest.TestCase):
    def test_signature_sorts_custom_parameters(self):
        expected_source = "199.00:42:secret:Shp_order=persona:Shp_tg_id=123456"
        expected = hashlib.md5(expected_source.encode("utf-8")).hexdigest()
        actual = robokassa_signature(
            "199.00",
            "42",
            "secret",
            {"Shp_tg_id": "123456", "Shp_order": "persona"},
            "md5",
        )
        self.assertEqual(actual, expected)

    def test_telegram_id_accepts_persona_format(self):
        self.assertEqual(robokassa_telegram_id({"Shp_tg_id": "TG-6639901180"}), 6639901180)

    def test_telegram_id_rejects_missing_value(self):
        with self.assertRaises(ValueError):
            robokassa_telegram_id({})

    def test_payment_signature_includes_user_id(self):
        expected_source = "persona:199.00:42:secret:Shp_tg_id=123456"
        expected = hashlib.md5(expected_source.encode("utf-8")).hexdigest()
        actual = robokassa_payment_signature(
            "persona", "199.00", "42", "secret", {"Shp_tg_id": "123456"}
        )
        self.assertEqual(actual, expected)

    def test_payment_url_contains_signed_order(self):
        url = robokassa_payment_url(
            "persona", "199.00", 42, "secret", 123456, test_mode=True
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["InvId"], ["42"])
        self.assertEqual(query["Shp_tg_id"], ["123456"])
        self.assertEqual(query["IsTest"], ["1"])

    def test_duplicate_invoice_does_not_extend_subscription_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            database.ensure_user(123456)
            first, first_created = database.grant_subscription(
                123456,
                payment_id="robokassa:42",
                source="robokassa",
                amount=199,
            )
            second, second_created = database.grant_subscription(
                123456,
                payment_id="robokassa:42",
                source="robokassa",
                amount=199,
            )
            self.assertTrue(first_created)
            self.assertFalse(second_created)
            self.assertEqual(first["expires_at"], second["expires_at"])

    def test_payment_order_is_bound_to_user_and_amount(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            invoice_id = database.create_payment_order(123456, 199)
            order = database.get_payment_order(invoice_id)
            self.assertEqual(order["tg_id"], 123456)
            self.assertEqual(order["amount"], 199)
            self.assertEqual(order["status"], "pending")
            database.mark_payment_order_paid(invoice_id)
            self.assertEqual(database.get_payment_order(invoice_id)["status"], "paid")


if __name__ == "__main__":
    unittest.main()
