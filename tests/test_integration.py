"""Complete fictional workflow through rendered forms on one temporary SQLite file."""

import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from campusfind import create_app
from campusfind.admin import COMPETING_NOTE
from campusfind.db import get_db


PASSWORD = "fictional student password"
ADMIN_PASSWORD = "fictional administrator password"


class CsrfParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tokens = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name") == "csrf_token":
            self.tokens.append(values.get("value"))


class JourneyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = str(Path(directory.name) / "journey.sqlite")
        self.app = create_app({
            "TESTING": True, "APP_ENV": "testing", "DATABASE": self.database,
            "SECRET_KEY": "isolated-cf07-integration-secret",
        })
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)

    def token(self, client, path):
        response = client.get(path)
        self.assertEqual(response.status_code, 200, path)
        parser = CsrfParser()
        parser.feed(response.get_data(as_text=True))
        self.assertTrue(parser.tokens, path)
        self.assertEqual(len(set(parser.tokens)), 1, path)
        return parser.tokens[0]

    def register(self, client, name, email):
        response = client.post("/auth/register", data={
            "csrf_token": self.token(client, "/auth/register"),
            "name": name, "email": email, "password": PASSWORD,
            "confirm_password": PASSWORD,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/auth/login")

    def login(self, client, email, password=PASSWORD):
        response = client.post("/auth/login", data={
            "csrf_token": self.token(client, "/auth/login"),
            "email": email, "password": password,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def create_report(self, client, report_type, title):
        response = client.post("/items/new", data={
            "csrf_token": self.token(client, "/items/new"),
            "report_type": report_type, "title": title,
            "description": "A fictional notebook with identifying marks.",
            "category": "Books", "location": "Main library",
            "event_date": "2020-01-02",
        })
        self.assertEqual(response.status_code, 302)
        return int(response.headers["Location"].rsplit("/", 1)[1])

    def test_complete_journey_and_persistence(self):
        author = self.app.test_client()
        first = self.app.test_client()
        second = self.app.test_client()
        administrator = self.app.test_client()
        self.register(author, "Fictional Author", "author@example.edu")
        self.register(first, "Fictional First", "first@example.edu")
        self.register(second, "Fictional Second", "second@example.edu")
        admin_result = self.app.test_cli_runner().invoke(
            args=["create-admin"],
            input=(f"Fictional Admin\nadmin@example.edu\n"
                   f"{ADMIN_PASSWORD}\n{ADMIN_PASSWORD}\n"),
        )
        self.assertEqual(admin_result.exit_code, 0, admin_result.output)
        self.assertNotIn(ADMIN_PASSWORD, admin_result.output)

        self.login(author, "author@example.edu")
        found_id = self.create_report(author, "found", "Found blue notebook")
        self.login(first, "first@example.edu")
        self.login(second, "second@example.edu")
        first_proof = "My fictional initials are inside the blue cover."
        second_proof = "A fictional green ribbon is inside this notebook."
        for client, proof in ((first, first_proof), (second, second_proof)):
            response = client.post(f"/items/{found_id}/claims", data={
                "csrf_token": self.token(client, f"/items/{found_id}"),
                "proof_text": proof,
            })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["Location"], "/claims/mine")

        for client, own_proof, other_proof in (
            (first, first_proof, second_proof),
            (second, second_proof, first_proof),
        ):
            body = client.get("/claims/mine").data
            self.assertIn(own_proof.encode(), body)
            self.assertNotIn(other_proof.encode(), body)
            self.assertNotIn(b"admin@example.edu", body)
        for client in (author, self.app.test_client()):
            body = client.get(f"/items/{found_id}").data
            self.assertNotIn(first_proof.encode(), body)
            self.assertNotIn(second_proof.encode(), body)
            self.assertNotIn(b"first@example.edu", body)
        self.assertEqual(author.get(f"/items/{found_id}/edit").status_code, 409)
        self.assertEqual(author.post(f"/items/{found_id}/delete", data={
            "csrf_token": self.token(author, f"/items/{found_id}"),
            "confirm_delete": "yes",
        }).status_code, 409)

        with self.app.app_context():
            connection = get_db()
            claim_rows = connection.execute(
                """SELECT c.id, u.email FROM claims c JOIN users u ON u.id = c.claimant_id
                   WHERE c.item_id = ?""", (found_id,)
            ).fetchall()
            claim_ids = {row["email"]: row["id"] for row in claim_rows}
        self.assertEqual(len(claim_ids), 2)
        first_id = claim_ids["first@example.edu"]
        second_id = claim_ids["second@example.edu"]

        self.login(administrator, "admin@example.edu", ADMIN_PASSWORD)
        dashboard = administrator.get("/admin")
        self.assertIn(first_proof.encode(), dashboard.data)
        self.assertIn(second_proof.encode(), dashboard.data)
        approve_token = self.token(administrator, "/admin")
        decision = administrator.post(f"/admin/claims/{first_id}/approve", data={
            "csrf_token": approve_token,
            "explanation": "Identifying details match the report.",
        })
        self.assertEqual(decision.status_code, 302)
        with self.app.app_context():
            connection = get_db()
            approved = connection.execute("SELECT * FROM claims WHERE id = ?", (first_id,)).fetchone()
            rejected = connection.execute("SELECT * FROM claims WHERE id = ?", (second_id,)).fetchone()
            claimed = connection.execute("SELECT * FROM items WHERE id = ?", (found_id,)).fetchone()
            self.assertEqual(approved["status"], "approved")
            self.assertEqual(rejected["status"], "rejected")
            self.assertEqual(rejected["admin_note"], COMPETING_NOTE)
            self.assertNotIn(first_proof, rejected["admin_note"])
            self.assertEqual(claimed["status"], "claimed")
            claim_stamps = (approved["reviewed_at"], rejected["reviewed_at"])
            claimed_stamp = claimed["updated_at"]
        self.assertIn(b"Identifying details match", first.get("/claims/mine").data)
        second_body = second.get("/claims/mine").data
        self.assertIn(COMPETING_NOTE.encode(), second_body)
        self.assertNotIn(first_proof.encode(), second_body)
        self.assertNotIn(b"Identifying details match", second_body)
        self.assertEqual(administrator.post(
            f"/admin/claims/{first_id}/approve",
            data={"csrf_token": approve_token, "explanation": "Another decision"},
        ).status_code, 409)

        handover = administrator.post(f"/admin/items/{found_id}/return", data={
            "csrf_token": self.token(administrator, "/admin"),
            "confirm_handover": "yes",
        })
        self.assertEqual(handover.status_code, 302)
        with self.app.app_context():
            returned = get_db().execute("SELECT * FROM items WHERE id = ?", (found_id,)).fetchone()
            self.assertEqual(returned["status"], "returned")
            returned_stamp = returned["updated_at"]
        self.assertEqual(administrator.post(f"/admin/items/{found_id}/return", data={
            "csrf_token": self.token(administrator, "/admin"),
            "confirm_handover": "yes",
        }).status_code, 409)
        self.assertEqual(administrator.post(f"/admin/claims/{first_id}/approve", data={
            "csrf_token": self.token(administrator, "/admin"),
            "explanation": "Another decision",
        }).status_code, 409)
        with self.app.app_context():
            connection = get_db()
            self.assertEqual(connection.execute(
                "SELECT updated_at FROM items WHERE id = ?", (found_id,)
            ).fetchone()[0], returned_stamp)
            self.assertEqual(tuple(row[0] for row in connection.execute(
                "SELECT reviewed_at FROM claims WHERE id IN (?, ?) ORDER BY id",
                (first_id, second_id),
            )), claim_stamps)
            self.assertEqual(connection.execute(
                "SELECT updated_at FROM items WHERE id = ?", (found_id,)
            ).fetchone()[0], returned_stamp)
            self.assertNotEqual(claimed_stamp, "")

        lost_id = self.create_report(author, "lost", "Lost red notebook")
        edited = author.post(f"/items/{lost_id}/edit", data={
            "csrf_token": self.token(author, f"/items/{lost_id}/edit"),
            "report_type": "lost", "title": "Lost red course notebook",
            "description": "A fictional red notebook with course notes.",
            "category": "Books", "location": "North library",
            "event_date": "2020-01-02",
        })
        self.assertEqual(edited.status_code, 302)
        self.assertIn(b"Lost red course notebook", author.get(f"/items/{lost_id}").data)
        self.assertEqual(author.post(f"/items/{lost_id}/close", data={
            "csrf_token": self.token(author, f"/items/{lost_id}"),
        }).status_code, 302)

        second_app = create_app({
            "TESTING": True, "APP_ENV": "testing", "DATABASE": self.database,
        })
        with second_app.app_context():
            connection = get_db()
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 4)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT status FROM items WHERE id = ?", (found_id,)
            ).fetchone()[0], "returned")
            self.assertEqual(tuple(connection.execute(
                "SELECT title, status FROM items WHERE id = ?", (lost_id,)
            ).fetchone()), ("Lost red course notebook", "closed"))
            self.assertEqual(connection.execute(
                "SELECT status FROM claims WHERE id = ?", (first_id,)
            ).fetchone()[0], "approved")
        init_again = second_app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(init_again.exit_code, 0, init_again.output)
        with second_app.app_context():
            connection = get_db()
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 4)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
