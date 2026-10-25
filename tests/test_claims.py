"""CF-05 student claim behavior on isolated SQLite databases."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from campusfind import create_app
from campusfind.db import get_db


PROOF = "The inside cover has my fictional initials and a blue ribbon."


class ClaimTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = create_app({
            "TESTING": True, "APP_ENV": "testing",
            "DATABASE": str(Path(directory.name) / "claims.sqlite"),
            "WTF_CSRF_ENABLED": False,
        })
        self.assertEqual(self.app.test_cli_runner().invoke(args=["init-db"]).exit_code, 0)
        self.client = self.app.test_client()
        with self.app.app_context():
            connection = get_db()
            with connection:
                self.owner = self._user(connection, "owner@example.edu")
                self.claimant = self._user(connection, "claimant@example.edu")
                self.other = self._user(connection, "other@example.edu")
                self.admin = self._user(connection, "admin@example.edu", "admin")
                self.found = self._item(connection, self.owner, "found")
                self.lost = self._item(connection, self.owner, "lost")

    @staticmethod
    def _user(connection, email, role="student"):
        return connection.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            ("Fictional Person", email, "test-hash", role),
        ).lastrowid

    @staticmethod
    def _item(connection, reporter, kind):
        return connection.execute(
            """INSERT INTO items (reporter_id, report_type, title, description,
               category, location, event_date) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (reporter, kind, f"{kind} notebook", "A fictional notebook with a label.",
             "Books", "Library", "2020-01-02"),
        ).lastrowid

    def sign_in(self, user_id):
        with self.client.session_transaction() as user_session:
            user_session.clear()
            user_session["user_id"] = user_id

    def claim(self, item_id=None, **changes):
        data = {"proof_text": PROOF}
        data.update(changes)
        return self.client.post(f"/items/{item_id or self.found}/claims", data=data)

    def row(self, item_id=None, claimant_id=None):
        with self.app.app_context():
            return get_db().execute(
                "SELECT * FROM claims WHERE item_id = ? AND claimant_id = ?",
                (item_id or self.found, claimant_id or self.claimant),
            ).fetchone()

    def test_valid_claim_uses_server_identity_status_and_timestamp(self):
        self.sign_in(self.claimant)
        response = self.claim(
            proof_text=f"  {PROOF}  ", claimant_id=self.other, status="approved",
            admin_note="fake", created_at="1900-01-01", reviewed_at="1900-01-01",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/claims/mine", response.headers["Location"])
        row = self.row()
        self.assertEqual(row["claimant_id"], self.claimant)
        self.assertEqual(row["proof_text"], PROOF)
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["admin_note"])
        self.assertIsNone(row["reviewed_at"])
        self.assertTrue(row["created_at"].endswith("Z"))

    def test_login_role_and_self_claim_checks(self):
        self.assertEqual(self.claim().status_code, 302)
        self.assertIn("/auth/login", self.claim().headers["Location"])
        self.sign_in(self.admin)
        self.assertEqual(self.claim().status_code, 403)
        self.sign_in(self.owner)
        self.assertEqual(self.claim().status_code, 403)
        self.assertEqual(self.client.get("/items/999999").status_code, 404)

    def test_duplicate_even_after_rejection(self):
        self.sign_in(self.claimant)
        self.assertEqual(self.claim().status_code, 302)
        self.assertEqual(self.claim().status_code, 409)
        with self.app.app_context():
            connection = get_db()
            with connection:
                connection.execute(
                    "UPDATE claims SET status = 'rejected', reviewed_at = ? WHERE item_id = ?",
                    ("2020-01-03T00:00:00Z", self.found),
                )
        self.assertEqual(self.claim().status_code, 409)

    def test_lost_and_nonopen_reports_cannot_receive_claims(self):
        self.sign_in(self.claimant)
        self.assertEqual(self.claim(self.lost).status_code, 409)
        for status in ("claimed", "returned", "closed"):
            with self.subTest(status=status):
                with self.app.app_context():
                    connection = get_db()
                    with connection:
                        connection.execute("UPDATE items SET status = ? WHERE id = ?", (status, self.found))
                self.assertEqual(self.claim().status_code, 409)
        self.assertIsNone(self.row())

    def test_invalid_proof_does_not_insert(self):
        self.sign_in(self.claimant)
        for value in ("", "  short  ", "x" * 2001, " " * 30):
            with self.subTest(value=value[:25]):
                response = self.claim(proof_text=value)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b'role="alert"', response.data)
        self.assertIsNone(self.row())

    def test_my_claims_isolation_and_private_notes(self):
        self.sign_in(self.claimant)
        self.claim(proof_text=PROOF)
        with self.app.app_context():
            connection = get_db()
            with connection:
                connection.execute(
                    "UPDATE claims SET status = 'rejected', admin_note = ?, reviewed_at = ? WHERE item_id = ?",
                    ("Claimant-only explanation", "2020-01-03T00:00:00Z", self.found),
                )
        response = self.client.get("/claims/mine")
        self.assertIn(PROOF.encode(), response.data)
        self.assertIn(b"Claimant-only explanation", response.data)
        self.sign_in(self.other)
        response = self.client.get("/claims/mine")
        self.assertNotIn(PROOF.encode(), response.data)
        self.assertNotIn(b"Claimant-only explanation", response.data)
        self.sign_in(self.owner)
        self.assertNotIn(PROOF.encode(), self.client.get("/claims/mine").data)
        self.sign_in(self.admin)
        self.assertEqual(self.client.get("/claims/mine").status_code, 403)

    def test_public_and_author_pages_do_not_expose_claim_data(self):
        self.sign_in(self.claimant)
        self.claim(proof_text=PROOF)
        with self.app.app_context():
            connection = get_db()
            with connection:
                connection.execute(
                    "UPDATE claims SET status = 'rejected', admin_note = ?, reviewed_at = ? WHERE item_id = ?",
                    ("Private decision words", "2020-01-03T00:00:00Z", self.found),
                )
        for identity in (None, self.owner, self.other):
            with self.subTest(identity=identity):
                self.sign_in(identity)
                for path in ("/items", f"/items/{self.found}", "/items/mine" if identity else "/items"):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    for private in (PROOF.encode(), b"Private decision words", b"claimant@example.edu"):
                        self.assertNotIn(private, response.data)

    def test_detail_shows_form_only_to_eligible_student(self):
        self.assertIn(b"Sign in as a student", self.client.get(f"/items/{self.found}").data)
        self.sign_in(self.owner)
        self.assertIn(b"cannot claim your own", self.client.get(f"/items/{self.found}").data)
        self.sign_in(self.admin)
        self.assertIn(b"Administrators cannot", self.client.get(f"/items/{self.found}").data)
        self.sign_in(self.claimant)
        self.assertIn(b"Submit claim", self.client.get(f"/items/{self.found}").data)
        self.claim()
        self.assertIn(b"already submitted", self.client.get(f"/items/{self.found}").data)
        self.assertNotIn(b"Submit claim", self.client.get(f"/items/{self.found}").data)

    def test_missing_and_get_mutation_attempts(self):
        self.sign_in(self.claimant)
        self.assertEqual(self.claim(999999).status_code, 404)
        self.assertEqual(self.client.get(f"/items/{self.found}/claims").status_code, 405)
        self.assertIsNone(self.row())

    def test_item_change_between_checks_blocks_claim(self):
        from campusfind import claims as claim_module
        self.sign_in(self.claimant)
        original = claim_module._eligible_or_error
        calls = 0

        def close_after_first_check(item, claimant_id):
            nonlocal calls
            calls += 1
            result = original(item, claimant_id)
            if calls == 1:
                with self.app.app_context():
                    connection = get_db()
                    with connection:
                        connection.execute(
                            "UPDATE items SET status = 'closed' WHERE id = ?", (self.found,)
                        )
            return result

        with patch.object(claim_module, "_eligible_or_error", side_effect=close_after_first_check):
            response = self.claim()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(calls, 2)
        self.assertIsNone(self.row())

    def test_private_text_is_escaped_in_authorized_views(self):
        self.sign_in(self.claimant)
        proof = "My fictional initials are <script>alert(1)</script> inside."
        self.assertEqual(self.claim(proof_text=proof).status_code, 302)
        claim = self.row()
        with self.app.app_context():
            connection = get_db()
            with connection:
                connection.execute(
                    "UPDATE claims SET status = 'rejected', admin_note = ?, reviewed_at = ? WHERE id = ?",
                    ("<b>Decision details</b>", "2020-01-03T00:00:00Z", claim["id"]),
                )
        body = self.client.get("/claims/mine").data
        self.assertNotIn(b"<script>", body)
        self.assertNotIn(b"<b>Decision details</b>", body)
        self.assertIn(b"&lt;script&gt;", body)
        self.assertIn(b"&lt;b&gt;Decision details&lt;/b&gt;", body)


if __name__ == "__main__":
    unittest.main()
