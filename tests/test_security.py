"""CSRF checks with real tokens and isolated temporary databases."""

import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from campusfind import create_app
from campusfind.db import get_db


PASSWORD = "correct horse battery staple"


class TokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tokens = []

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            attributes = dict(attrs)
            if attributes.get("name") == "csrf_token":
                self.tokens.append(attributes.get("value"))


class CsrfTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        database_path = str(Path(self.directory.name) / "security.sqlite")
        self.app = create_app(
            {
                "TESTING": True,
                "APP_ENV": "testing",
                "DATABASE": database_path,
                "SECRET_KEY": "isolated-security-test-secret",
            }
        )
        self.assertTrue(self.app.config["WTF_CSRF_ENABLED"])
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client = self.app.test_client()

    def token_from(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        parser = TokenParser()
        parser.feed(response.data.decode("utf-8"))
        self.assertGreaterEqual(len(parser.tokens), 1)
        self.assertEqual(len(set(parser.tokens)), 1)
        self.assertTrue(parser.tokens[0])
        return parser.tokens[0]

    def registration_data(self, token=None):
        data = {
            "name": "Test Student",
            "email": "student@example.edu",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        }
        if token is not None:
            data["csrf_token"] = token
        return data

    def test_valid_token_permits_registration(self):
        token = self.token_from("/auth/register")
        response = self.client.post("/auth/register", data=self.registration_data(token))
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_missing_and_invalid_tokens_return_clear_400(self):
        self.token_from("/auth/register")
        for data in (self.registration_data(), self.registration_data("not-a-valid-token")):
            with self.subTest(data=data):
                response = self.client.post("/auth/register", data=data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"Form security check failed", response.data)
                self.assertNotIn(b"Traceback", response.data)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

    def test_login_and_logout_require_real_tokens(self):
        register_token = self.token_from("/auth/register")
        self.assertEqual(
            self.client.post("/auth/register", data=self.registration_data(register_token)).status_code,
            302,
        )

        login_token = self.token_from("/auth/login")
        login_data = {"email": "student@example.edu", "password": PASSWORD}
        self.assertEqual(self.client.post("/auth/login", data=login_data).status_code, 400)
        self.assertEqual(
            self.client.post(
                "/auth/login", data={**login_data, "csrf_token": login_token}
            ).status_code,
            302,
        )

        logout_token = self.token_from("/")
        for data in ({}, {"csrf_token": "not-a-valid-token"}):
            with self.subTest(data=data):
                response = self.client.post("/auth/logout", data=data)
                self.assertEqual(response.status_code, 400)
                with self.client.session_transaction() as user_session:
                    self.assertIn("user_id", user_session)
        self.assertEqual(
            self.client.post("/auth/logout", data={"csrf_token": logout_token}).status_code,
            302,
        )
        with self.client.session_transaction() as user_session:
            self.assertNotIn("user_id", user_session)

    def test_item_mutations_reject_missing_csrf_tokens(self):
        register_token = self.token_from("/auth/register")
        self.assertEqual(
            self.client.post("/auth/register", data=self.registration_data(register_token)).status_code,
            302,
        )
        login_token = self.token_from("/auth/login")
        self.assertEqual(self.client.post("/auth/login", data={
            "email": "student@example.edu", "password": PASSWORD,
            "csrf_token": login_token,
        }).status_code, 302)
        item_data = {
            "report_type": "lost", "title": "Notebook", "description": "A blue class notebook",
            "category": "Books", "location": "Library", "event_date": "2020-01-02",
        }
        token = self.token_from("/items/new")
        self.assertEqual(self.client.post("/items/new", data=item_data).status_code, 400)
        response = self.client.post("/items/new", data={**item_data, "csrf_token": token})
        self.assertEqual(response.status_code, 302)
        item_id = int(response.headers["Location"].split("/")[-1])
        for path, data in (
            (f"/items/{item_id}/edit", item_data),
            (f"/items/{item_id}/delete", {"confirm_delete": "yes"}),
            (f"/items/{item_id}/close", {}),
        ):
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"Form security check failed", response.data)
        with self.app.app_context():
            row = get_db().execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
            self.assertEqual(row["status"], "open")

    def test_claim_and_admin_mutations_require_real_csrf_tokens(self):
        with self.app.app_context():
            connection = get_db()
            with connection:
                owner = connection.execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                    ("Fictional Owner", "owner@example.edu", "test-hash"),
                ).lastrowid
                claimant = connection.execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                    ("Fictional Claimant", "claimant@example.edu", "test-hash"),
                ).lastrowid
                admin = connection.execute(
                    "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("Fictional Admin", "admin@example.edu", "test-hash", "admin"),
                ).lastrowid
                item = connection.execute(
                    """INSERT INTO items (reporter_id, report_type, title, description,
                       category, location, event_date) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (owner, "found", "Found notebook", "A fictional blue notebook.",
                     "Books", "Library", "2020-01-02"),
                ).lastrowid
        with self.client.session_transaction() as user_session:
            user_session["user_id"] = claimant
        claim_token = self.token_from(f"/items/{item}")
        proof = "The inside cover contains my fictional initials."
        self.assertEqual(self.client.post(
            f"/items/{item}/claims", data={"proof_text": proof}
        ).status_code, 400)
        self.assertEqual(self.client.post(
            f"/items/{item}/claims", data={"proof_text": proof, "csrf_token": claim_token}
        ).status_code, 302)
        with self.app.app_context():
            claim_id = get_db().execute(
                "SELECT id FROM claims WHERE item_id = ? AND claimant_id = ?", (item, claimant)
            ).fetchone()[0]
        with self.client.session_transaction() as user_session:
            user_session.clear()
            user_session["user_id"] = admin
        admin_token = self.token_from("/admin")
        for path, data in (
            (f"/admin/claims/{claim_id}/approve", {"explanation": "Evidence matches"}),
            (f"/admin/claims/{claim_id}/reject", {"explanation": "Insufficient detail"}),
            (f"/admin/items/{item}/return", {"confirm_handover": "yes"}),
        ):
            with self.subTest(path=path):
                for supplied in ({}, {"csrf_token": "invalid-token"}):
                    response = self.client.post(path, data={**data, **supplied})
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(b"Form security check failed", response.data)
        with self.app.app_context():
            self.assertEqual(get_db().execute(
                "SELECT status FROM claims WHERE id = ?", (claim_id,)
            ).fetchone()[0], "pending")
            self.assertEqual(get_db().execute(
                "SELECT status FROM items WHERE id = ?", (item,)
            ).fetchone()[0], "open")
        self.assertEqual(self.client.post(
            f"/admin/claims/{claim_id}/approve",
            data={"explanation": "Evidence matches", "csrf_token": admin_token},
        ).status_code, 302)
        self.assertEqual(self.client.post(
            f"/admin/items/{item}/return",
            data={"confirm_handover": "yes", "csrf_token": admin_token},
        ).status_code, 302)


if __name__ == "__main__":
    unittest.main()
