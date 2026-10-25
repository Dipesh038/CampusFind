"""Focused checks for CF-06 navigation and shared error responses."""

import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from flask import abort

from campusfind import create_app
from campusfind.db import get_db


class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_navigation = False
        self.links = {}
        self.actions = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "nav" and values.get("aria-label") == "Main navigation":
            self.in_navigation = True
        if self.in_navigation and tag == "a":
            self.links[values.get("href")] = values.get("aria-current")
        if self.in_navigation and tag == "form":
            self.actions.append((values.get("action"), values.get("method")))

    def handle_endtag(self, tag):
        if tag == "nav":
            self.in_navigation = False


class UiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = create_app({
            "TESTING": True,
            "PROPAGATE_EXCEPTIONS": False,
            "APP_ENV": "testing",
            "SECRET_KEY": "isolated-cf06-test-secret",
            "DATABASE": str(Path(directory.name) / "ui.sqlite"),
        })
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client = self.app.test_client()

    def _navigation(self, path="/"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        parser = NavigationParser()
        parser.feed(response.data.decode("utf-8"))
        return parser, response

    def test_navigation_respects_current_database_role(self):
        visitor, response = self._navigation("/items")
        self.assertEqual(visitor.links["/items"], "page")
        self.assertIn("/auth/login", visitor.links)
        self.assertIn("/auth/register", visitor.links)
        self.assertNotIn("/items/new", visitor.links)
        self.assertIn(b'Skip to content', response.data)
        self.assertIn(b'id="main-content"', response.data)

        with self.app.app_context():
            connection = get_db()
            with connection:
                student_id = connection.execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                    ("Fictional Student", "student@example.edu", "test-hash"),
                ).lastrowid
                admin_id = connection.execute(
                    "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("Fictional Admin", "admin@example.edu", "test-hash", "admin"),
                ).lastrowid

        with self.client.session_transaction() as session:
            session["user_id"] = student_id
        student, _ = self._navigation("/items/mine")
        self.assertEqual(student.links["/items/mine"], "page")
        for path in ("/items/new", "/items/mine", "/claims/mine"):
            self.assertIn(path, student.links)
        self.assertNotIn("/admin", student.links)
        self.assertNotIn("/auth/login", student.links)
        self.assertIn(("/auth/logout", "post"), student.actions)

        with self.client.session_transaction() as session:
            session.clear()
            session["user_id"] = admin_id
        admin, _ = self._navigation("/admin")
        self.assertEqual(admin.links["/admin"], "page")
        self.assertNotIn("/items/new", admin.links)
        self.assertNotIn("/items/mine", admin.links)
        self.assertNotIn("/claims/mine", admin.links)
        self.assertIn(("/auth/logout", "post"), admin.actions)

    def test_error_pages_preserve_status_and_escape_messages(self):
        for status in (400, 403, 404, 409, 503):
            path = f"/cf06-error-{status}"
            self.app.add_url_rule(
                path, f"cf06_error_{status}",
                lambda code=status: abort(code, description="Safe <notice>"),
            )
        for status in (400, 403, 404, 409, 503):
            path = f"/cf06-error-{status}"
            response = self.client.get(path)
            self.assertEqual(response.status_code, status)
            self.assertIn(b"CampusFind", response.data)
            self.assertIn(b"Safe &lt;notice&gt;", response.data)
            self.assertNotIn(b"Safe <notice>", response.data)

    def test_server_error_never_discloses_exception(self):
        @self.app.get("/cf06-error-500")
        def secret_failure():
            raise RuntimeError("private exception detail")

        self.app.logger.disabled = True
        response = self.client.get("/cf06-error-500")
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Something went wrong", response.data)
        self.assertNotIn(b"private exception detail", response.data)

    def test_health_is_still_json(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
