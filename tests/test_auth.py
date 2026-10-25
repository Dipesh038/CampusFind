"""CF-03 authentication tests; no test touches the local database."""

import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

from campusfind import create_app
from campusfind.auth import admin_required, login_required
from campusfind.db import get_db


PASSWORD = "correct horse battery staple"


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database_path = str(Path(self.directory.name) / "auth.sqlite")
        self.app = create_app(
            {
                "TESTING": True,
                "APP_ENV": "testing",
                "DATABASE": self.database_path,
                "WTF_CSRF_ENABLED": False,
            }
        )
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)

        @self.app.get("/test-login-only")
        @login_required
        def login_only():
            return "member access"

        @self.app.get("/test-admin-only")
        @admin_required
        def admin_only():
            return "admin access"

        self.client = self.app.test_client()

    def registration_data(self, **changes):
        data = {
            "name": "Test Student",
            "email": "student@example.edu",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        }
        data.update(changes)
        return data

    def register(self, **changes):
        return self.client.post("/auth/register", data=self.registration_data(**changes))

    def add_user(self, email="student@example.edu", role="student", password=PASSWORD):
        with self.app.app_context():
            connection = get_db()
            with connection:
                return connection.execute(
                    """INSERT INTO users (name, email, password_hash, role)
                       VALUES (?, ?, ?, ?)""",
                    ("Test Student", email, generate_password_hash(password), role),
                ).lastrowid

    def test_registration_normalizes_email_and_hashes_password(self):
        response = self.register(name="  Test Student  ", email="  Student@Example.EDU  ")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/auth/login")
        with self.app.app_context():
            user = get_db().execute(
                "SELECT name, email, password_hash, role FROM users"
            ).fetchone()
            self.assertEqual(user["name"], "Test Student")
            self.assertEqual(user["email"], "student@example.edu")
            self.assertEqual(user["role"], "student")
            self.assertNotEqual(user["password_hash"], PASSWORD)
            self.assertTrue(check_password_hash(user["password_hash"], PASSWORD))

    def test_registration_does_not_trim_password(self):
        entered_password = "  keep spaces exactly  "
        response = self.register(
            password=entered_password, confirm_password=entered_password
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            stored_hash = get_db().execute(
                "SELECT password_hash FROM users"
            ).fetchone()["password_hash"]
            self.assertTrue(check_password_hash(stored_hash, entered_password))
            self.assertFalse(check_password_hash(stored_hash, entered_password.strip()))

    def test_duplicate_email_returns_field_error(self):
        self.assertEqual(self.register().status_code, 302)
        response = self.register(email=" STUDENT@EXAMPLE.EDU ")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Email is already registered.", response.data)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_registration_rejects_invalid_fields_and_never_renders_password(self):
        cases = (
            {"name": "   "},
            {"name": "A"},
            {"name": "A" * 101},
            {"email": "   "},
            {"email": "invalid-address"},
            {"email": "a" * 250 + "@x.edu"},
            {"password": "", "confirm_password": ""},
            {"password": "too short", "confirm_password": "too short"},
            {"password": "x" * 129, "confirm_password": "x" * 129},
            {"confirm_password": "different confirmation"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                response = self.register(**changes)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'role="alert"', response.data)
                self.assertNotIn(PASSWORD.encode(), response.data)
                if changes.get("confirm_password") == "different confirmation":
                    self.assertIn(b"Passwords must match.", response.data)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

    def test_client_supplied_role_and_id_are_ignored(self):
        response = self.client.post(
            "/auth/register",
            data={**self.registration_data(), "role": "admin", "id": "999"},
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            user = get_db().execute("SELECT id, role FROM users").fetchone()
            self.assertEqual(user["role"], "student")
            self.assertNotEqual(user["id"], 999)

    def test_login_succeeds_and_uses_only_user_id_in_identity_session(self):
        user_id = self.add_user()
        response = self.client.post(
            "/auth/login", data={"email": " STUDENT@EXAMPLE.EDU ", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        with self.client.session_transaction() as user_session:
            self.assertEqual(user_session["user_id"], user_id)
            self.assertTrue(user_session.permanent)
            self.assertNotIn("password_hash", user_session)
            self.assertNotIn("role", user_session)
        self.assertIn(b"Signed in as Test Student", self.client.get("/").data)

    def test_unknown_email_and_wrong_password_share_generic_error(self):
        self.add_user()
        responses = (
            self.client.post(
                "/auth/login", data={"email": "unknown@example.edu", "password": PASSWORD}
            ),
            self.client.post(
                "/auth/login",
                data={"email": "student@example.edu", "password": "incorrect password"},
            ),
        )
        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Invalid email or password.", response.data)
        with self.client.session_transaction() as user_session:
            self.assertNotIn("user_id", user_session)

    def test_login_replaces_old_session_and_logout_clears_it(self):
        old_id = self.add_user("old@example.edu")
        new_id = self.add_user("new@example.edu")
        with self.client.session_transaction() as user_session:
            user_session["user_id"] = old_id
            user_session["legacy_marker"] = "remove-me"
        response = self.client.post(
            "/auth/login", data={"email": "new@example.edu", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as user_session:
            self.assertEqual(user_session["user_id"], new_id)
            self.assertNotIn("legacy_marker", user_session)
        self.assertEqual(self.client.get("/auth/logout").status_code, 405)
        with self.client.session_transaction() as user_session:
            self.assertEqual(user_session["user_id"], new_id)
        self.assertEqual(self.client.post("/auth/logout").status_code, 302)
        with self.client.session_transaction() as user_session:
            self.assertNotIn("user_id", user_session)

    def test_authentication_and_admin_helpers_use_current_database_role(self):
        self.assertEqual(self.client.get("/test-login-only").headers["Location"], "/auth/login")
        self.assertEqual(self.client.get("/test-admin-only").headers["Location"], "/auth/login")
        user_id = self.add_user()
        with self.client.session_transaction() as user_session:
            user_session["user_id"] = user_id
        self.assertEqual(self.client.get("/test-login-only").status_code, 200)
        self.assertEqual(self.client.get("/test-admin-only").status_code, 403)
        with self.app.app_context():
            connection = get_db()
            with connection:
                connection.execute("UPDATE users SET role = ? WHERE id = ?", ("admin", user_id))
        self.assertEqual(self.client.get("/test-admin-only").status_code, 200)

    def test_stale_session_is_cleared(self):
        user_id = self.add_user()
        with self.client.session_transaction() as user_session:
            user_session["user_id"] = user_id
        with self.app.app_context():
            connection = get_db()
            with connection:
                connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.assertEqual(self.client.get("/").status_code, 200)
        with self.client.session_transaction() as user_session:
            self.assertNotIn("user_id", user_session)

    def test_create_admin_cli_hashes_password_and_sets_admin_role(self):
        cli_password = "private admin password"
        result = self.app.test_cli_runner().invoke(
            args=["create-admin"],
            input=f"  Campus Admin  \n Admin@Example.EDU \n{cli_password}\n{cli_password}\n",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Administrator created.", result.output)
        self.assertNotIn(cli_password, result.output)
        with self.app.app_context():
            admin = get_db().execute(
                "SELECT name, email, role, password_hash FROM users"
            ).fetchone()
            self.assertEqual(admin["name"], "Campus Admin")
            self.assertEqual(admin["email"], "admin@example.edu")
            self.assertEqual(admin["role"], "admin")
            self.assertTrue(check_password_hash(admin["password_hash"], cli_password))

    def test_create_admin_rejects_duplicate_without_promotion(self):
        user_id = self.add_user()
        cli_password = "private admin password"
        result = self.app.test_cli_runner().invoke(
            args=["create-admin"],
            input=f"Admin\n STUDENT@EXAMPLE.EDU \n{cli_password}\n{cli_password}\n",
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("already registered", result.output)
        with self.app.app_context():
            users = get_db().execute("SELECT id, role FROM users").fetchall()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0]["id"], user_id)
            self.assertEqual(users[0]["role"], "student")

    def test_create_admin_rejects_invalid_input(self):
        cli_password = "short"
        result = self.app.test_cli_runner().invoke(
            args=["create-admin"],
            input=f"A\ninvalid-email\n{cli_password}\n{cli_password}\n",
        )
        self.assertNotEqual(result.exit_code, 0)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

    def test_production_cookie_configuration(self):
        production_app = create_app(
            {
                "TESTING": True,
                "APP_ENV": "production",
                "SECRET_KEY": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
                "DATABASE": self.database_path,
            }
        )
        self.assertTrue(production_app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(production_app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(production_app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(production_app.config["PERMANENT_SESSION_LIFETIME"], timedelta(hours=2))

        @production_app.get("/test-cookie")
        def test_cookie():
            session["probe"] = "set"
            session.permanent = True
            return "ok"

        cookie = production_app.test_client().get("/test-cookie").headers["Set-Cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Expires=", cookie)

    def test_login_does_not_follow_client_next_url(self):
        self.add_user()
        response = self.client.post(
            "/auth/login?next=https://example.invalid/",
            data={"email": "student@example.edu", "password": PASSWORD, "next": "//example.invalid"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")


if __name__ == "__main__":
    unittest.main()
