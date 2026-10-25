"""CF-02 SQLite tests; every test uses its own temporary database."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from campusfind import create_app
from campusfind.db import get_db


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database_path = str(Path(self.directory.name) / "campusfind.sqlite")
        self.app = create_app(
            {"TESTING": True, "APP_ENV": "testing", "DATABASE": self.database_path}
        )
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)

    @staticmethod
    def add_user(connection, email):
        return connection.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Test Student", email, "test-hash-placeholder"),
        ).lastrowid

    @staticmethod
    def add_item(connection, reporter_id):
        return connection.execute(
            """INSERT INTO items
               (reporter_id, report_type, title, description, category,
                location, event_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                reporter_id,
                "found",
                "Test item",
                "A test description",
                "Other",
                "Library",
                "2026-10-25",
            ),
        ).lastrowid

    @staticmethod
    def add_claim(connection, item_id, claimant_id):
        return connection.execute(
            "INSERT INTO claims (item_id, claimant_id, proof_text) VALUES (?, ?, ?)",
            (item_id, claimant_id, "private test evidence"),
        ).lastrowid

    def test_tables_and_indexes_exist(self):
        with self.app.app_context():
            rows = get_db().execute(
                "SELECT type, name FROM sqlite_master WHERE type IN (?, ?)",
                ("table", "index"),
            ).fetchall()
            names = {(row["type"], row["name"]) for row in rows}
            for table in ("users", "items", "claims"):
                self.assertIn(("table", table), names)
            for index in (
                "idx_items_reporter_id",
                "idx_items_public",
                "idx_claims_claimant_id",
                "idx_claims_status_created_at",
                "uq_claims_one_approved_per_item",
            ):
                self.assertIn(("index", index), names)
            triggers = {
                row["name"]
                for row in get_db().execute(
                    "SELECT name FROM sqlite_master WHERE type = ?", ("trigger",)
                )
            }
            self.assertIn("trg_users_email_normalized_insert", triggers)
            self.assertIn("trg_users_email_normalized_update", triggers)

    def test_connection_is_scoped_and_foreign_keys_are_enforced(self):
        with self.app.app_context():
            connection = get_db()
            self.assertIs(connection, get_db())
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            self.assertIs(connection.row_factory, sqlite3.Row)
            with self.assertRaises(sqlite3.IntegrityError):
                self.add_item(connection, 999999)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_duplicate_and_unnormalized_emails_are_rejected(self):
        with self.app.app_context():
            connection = get_db()
            self.add_user(connection, "student@example.edu")
            with self.assertRaises(sqlite3.IntegrityError):
                self.add_user(connection, "student@example.edu")
            with self.assertRaises(sqlite3.IntegrityError):
                self.add_user(connection, " Student@Example.edu ")
            with self.assertRaises(sqlite3.IntegrityError):
                self.add_user(connection, "UPPER@EXAMPLE.EDU")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE users SET email = ? WHERE email = ?",
                    ("UPPER@EXAMPLE.EDU", "student@example.edu"),
                )

    def test_duplicate_claims_are_rejected(self):
        with self.app.app_context():
            connection = get_db()
            reporter = self.add_user(connection, "reporter@example.edu")
            claimant = self.add_user(connection, "claimant@example.edu")
            item = self.add_item(connection, reporter)
            self.add_claim(connection, item, claimant)
            with self.assertRaises(sqlite3.IntegrityError):
                self.add_claim(connection, item, claimant)

    def test_referenced_users_and_items_cannot_be_deleted(self):
        with self.app.app_context():
            connection = get_db()
            reporter = self.add_user(connection, "reporter@example.edu")
            claimant = self.add_user(connection, "claimant@example.edu")
            item = self.add_item(connection, reporter)
            self.add_claim(connection, item, claimant)
            for statement, record_id in (
                ("DELETE FROM users WHERE id = ?", reporter),
                ("DELETE FROM users WHERE id = ?", claimant),
                ("DELETE FROM items WHERE id = ?", item),
            ):
                with self.subTest(statement=statement, record_id=record_id):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, (record_id,))

    def test_invalid_enum_values_are_rejected(self):
        with self.app.app_context():
            connection = get_db()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("Test", "badrole@example.edu", "test-hash-placeholder", "owner"),
                )
            reporter = self.add_user(connection, "reporter@example.edu")
            claimant = self.add_user(connection, "claimant@example.edu")
            item = self.add_item(connection, reporter)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE items SET report_type = ? WHERE id = ?", ("borrowed", item)
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE items SET status = ? WHERE id = ?", ("missing", item))
            claim = self.add_claim(connection, item, claimant)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE claims SET status = ? WHERE id = ?", ("unknown", claim)
                )

    def test_only_one_approved_claim_per_item(self):
        with self.app.app_context():
            connection = get_db()
            reporter = self.add_user(connection, "reporter@example.edu")
            claimant_one = self.add_user(connection, "one@example.edu")
            claimant_two = self.add_user(connection, "two@example.edu")
            item = self.add_item(connection, reporter)
            claim_one = self.add_claim(connection, item, claimant_one)
            claim_two = self.add_claim(connection, item, claimant_two)
            connection.execute(
                "UPDATE claims SET status = ?, reviewed_at = ? WHERE id = ?",
                ("approved", "2026-10-25T00:00:00Z", claim_one),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE claims SET status = ?, reviewed_at = ? WHERE id = ?",
                    ("approved", "2026-10-25T00:00:00Z", claim_two),
                )

    def test_reinitialization_preserves_existing_record(self):
        with self.app.app_context():
            connection = get_db()
            self.add_user(connection, "keep@example.edu")
            connection.commit()
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            row = get_db().execute(
                "SELECT email FROM users WHERE email = ?", ("keep@example.edu",)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["email"], "keep@example.edu")

    def test_separate_apps_read_same_persistent_database(self):
        with self.app.app_context():
            connection = get_db()
            self.add_user(connection, "shared@example.edu")
            connection.commit()
        second_app = create_app(
            {"TESTING": True, "APP_ENV": "testing", "DATABASE": self.database_path}
        )
        with second_app.app_context():
            connection = get_db()
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            row = connection.execute(
                "SELECT email FROM users WHERE email = ?", ("shared@example.edu",)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["email"], "shared@example.edu")


if __name__ == "__main__":
    unittest.main()
