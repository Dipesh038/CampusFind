"""Public health and production configuration checks on disposable paths."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from campusfind import create_app


STRONG_SECRET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


class HealthTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "health.sqlite"
        self.app = create_app({
            "TESTING": True, "APP_ENV": "testing", "DATABASE": str(self.database),
            "RELEASE_SHA": None,
        })
        self.client = self.app.test_client()

    def test_homepage(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CampusFind", response.data)

    def test_health_checks_database_without_initializing_schema(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0], 0)

    def test_health_stays_json_with_a_session(self):
        with self.client.session_transaction() as user_session:
            user_session["user_id"] = 12345
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_health_includes_only_configured_release(self):
        self.app.config["RELEASE_SHA"] = "test-commit-123"
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "status": "ok", "release": "test-commit-123"
        })

    def test_health_connection_and_query_failures_are_generic_json(self):
        from campusfind import db

        for failure in (
            sqlite3.OperationalError("private database path"),
            sqlite3.DatabaseError("private SQL failure"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(db, "get_db", side_effect=failure):
                    response = self.client.get("/health")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.get_json(), {"status": "unavailable"})
                self.assertEqual(response.mimetype, "application/json")
                self.assertNotIn(b"private", response.data)

    def test_health_unavailable_database_does_not_create_parent(self):
        database = self.database.parent / "missing" / "health.sqlite"
        self.app.config["DATABASE"] = str(database)
        with self.client.session_transaction() as user_session:
            user_session["user_id"] = 12345
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {"status": "unavailable"})
        self.assertFalse(database.parent.exists())

    def test_production_rejects_missing_or_weak_secret(self):
        for secret in (None, "", "weak", "a" * 64):
            with self.subTest(secret_present=bool(secret)):
                with self.assertRaisesRegex(RuntimeError, "strong SECRET_KEY"):
                    create_app({
                        "APP_ENV": "production", "SECRET_KEY": secret,
                        "DATABASE": str(self.database),
                    })

    def test_production_requires_absolute_database_and_disables_debug(self):
        with self.assertRaisesRegex(RuntimeError, "absolute path"):
            create_app({
                "APP_ENV": "production", "SECRET_KEY": STRONG_SECRET,
                "DATABASE": "relative.sqlite",
            })
        with self.assertRaisesRegex(RuntimeError, "Debug mode"):
            create_app({
                "APP_ENV": "production", "SECRET_KEY": STRONG_SECRET,
                "DATABASE": str(self.database), "DEBUG": True,
            })
        app = create_app({
            "APP_ENV": "production", "SECRET_KEY": STRONG_SECRET,
            "DATABASE": str(self.database),
        })
        self.assertFalse(app.debug)
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")


if __name__ == "__main__":
    unittest.main()
