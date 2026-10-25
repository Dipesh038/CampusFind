"""CF-05 administrator workflow, rollback, and controlled concurrency tests."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from campusfind import create_app
from campusfind import admin as admin_module
from campusfind.db import get_db


class AdminTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = create_app({
            "TESTING": True, "APP_ENV": "testing",
            "DATABASE": str(Path(directory.name) / "admin.sqlite"),
            "WTF_CSRF_ENABLED": False,
        })
        self.assertEqual(self.app.test_cli_runner().invoke(args=["init-db"]).exit_code, 0)
        self.client = self.app.test_client()
        with self.app.app_context():
            connection = get_db()
            with connection:
                self.owner = self._user(connection, "owner@example.edu")
                self.first = self._user(connection, "first@example.edu")
                self.second = self._user(connection, "second@example.edu")
                self.admin = self._user(connection, "admin@example.edu", "admin")
                self.item_id = self._item(connection, self.owner)

    @staticmethod
    def _user(connection, email, role="student"):
        return connection.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            ("Fictional Person", email, "test-hash", role),
        ).lastrowid

    @staticmethod
    def _item(connection, owner, kind="found"):
        return connection.execute(
            """INSERT INTO items (reporter_id, report_type, title, description,
               category, location, event_date) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (owner, kind, "Fictional book", "A book used for workflow testing.",
             "Books", "Library", "2020-01-02"),
        ).lastrowid

    def add_claim(self, claimant, item_id=None):
        with self.app.app_context():
            connection = get_db()
            with connection:
                return connection.execute(
                    "INSERT INTO claims (item_id, claimant_id, proof_text) VALUES (?, ?, ?)",
                    (item_id or self.item_id, claimant, f"Private evidence for {claimant}"),
                ).lastrowid

    @staticmethod
    def sign_in(client, user_id):
        with client.session_transaction() as user_session:
            user_session.clear()
            user_session["user_id"] = user_id

    def row(self, table, record_id):
        with self.app.app_context():
            return get_db().execute(
                f"SELECT * FROM {table} WHERE id = ?", (record_id,)
            ).fetchone()

    def test_admin_authorization_on_every_route(self):
        claim_id = self.add_claim(self.first)
        paths = (
            ("get", "/admin", None),
            ("post", f"/admin/claims/{claim_id}/approve", {"explanation": "Valid reason"}),
            ("post", f"/admin/claims/{claim_id}/reject", {"explanation": "Valid reason"}),
            ("post", f"/admin/items/{self.item_id}/return", {"confirm_handover": "yes"}),
        )
        for user_id, expected in ((None, 302), (self.owner, 403)):
            self.sign_in(self.client, user_id)
            for method, path, data in paths:
                with self.subTest(user_id=user_id, path=path):
                    response = getattr(self.client, method)(path, data=data) if data else self.client.get(path)
                    self.assertEqual(response.status_code, expected)

    def test_dashboard_counts_private_proof_and_pagination(self):
        self.add_claim(self.first)
        self.add_claim(self.second)
        self.sign_in(self.client, self.admin)
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Total reports", response.data)
        self.assertIn(b"Open found reports", response.data)
        self.assertIn(b"Pending claims", response.data)
        self.assertIn(b"Private evidence for", response.data)
        self.assertIn(b"Do not include other people", response.data)
        with self.app.app_context():
            connection = get_db()
            with connection:
                for number in range(11):
                    person = self._user(connection, f"more{number}@example.edu")
                    connection.execute(
                        "INSERT INTO claims (item_id, claimant_id, proof_text) VALUES (?, ?, ?)",
                        (self.item_id, person, f"Private proof {number}"),
                    )
        first_page = self.client.get("/admin")
        self.assertIn(b"Next pending claims", first_page.data)
        self.assertEqual(first_page.data.count(b"Approve claim"), 10)
        next_page = self.client.get("/admin?pending_page=2")
        self.assertEqual(next_page.data.count(b"Approve claim"), 3)
        self.assertIn(b"Previous pending claims", next_page.data)
        for page in ("0", "abc", "9" * 5000):
            self.assertEqual(self.client.get("/admin", query_string={"pending_page": page}).status_code, 400)

    def test_approval_updates_claims_and_item_atomically(self):
        first_claim = self.add_claim(self.first)
        second_claim = self.add_claim(self.second)
        self.sign_in(self.client, self.admin)
        response = self.client.post(
            f"/admin/claims/{first_claim}/approve", data={"explanation": "  Matches the identifying mark.  "}
        )
        self.assertEqual(response.status_code, 302)
        approved = self.row("claims", first_claim)
        rejected = self.row("claims", second_claim)
        item = self.row("items", self.item_id)
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["admin_note"], "Matches the identifying mark.")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["admin_note"], admin_module.COMPETING_NOTE)
        self.assertNotIn("first@example.edu", rejected["admin_note"])
        self.assertEqual(item["status"], "claimed")
        self.assertEqual(approved["reviewed_at"], rejected["reviewed_at"])
        self.assertEqual(item["updated_at"], approved["reviewed_at"])
        self.assertEqual(self.client.post(
            f"/admin/claims/{second_claim}/approve", data={"explanation": "Another reason"}
        ).status_code, 409)

    def test_rejection_leaves_item_open_and_cannot_be_repeated(self):
        claim_id = self.add_claim(self.first)
        self.sign_in(self.client, self.admin)
        response = self.client.post(
            f"/admin/claims/{claim_id}/reject", data={"explanation": "  Not enough identifying detail.  "}
        )
        self.assertEqual(response.status_code, 302)
        row = self.row("claims", claim_id)
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["admin_note"], "Not enough identifying detail.")
        self.assertEqual(self.row("items", self.item_id)["status"], "open")
        original_review = row["reviewed_at"]
        for action in ("approve", "reject"):
            self.assertEqual(self.client.post(
                f"/admin/claims/{claim_id}/{action}", data={"explanation": "New reason"}
            ).status_code, 409)
        self.assertEqual(self.row("claims", claim_id)["reviewed_at"], original_review)

    def test_invalid_decision_notes_do_not_change_state(self):
        claim_id = self.add_claim(self.first)
        self.sign_in(self.client, self.admin)
        for action in ("approve", "reject"):
            for explanation in ("", " x ", "x" * 501):
                with self.subTest(action=action, explanation=explanation[:10]):
                    response = self.client.post(
                        f"/admin/claims/{claim_id}/{action}", data={"explanation": explanation}
                    )
                    self.assertEqual(response.status_code, 400)
        self.assertEqual(self.row("claims", claim_id)["status"], "pending")
        self.assertEqual(self.row("items", self.item_id)["status"], "open")

    def test_return_requires_approved_claim_and_confirmation(self):
        claim_id = self.add_claim(self.first)
        self.sign_in(self.client, self.admin)
        with self.app.app_context():
            connection = get_db()
            with connection:
                connection.execute("UPDATE items SET status = 'claimed' WHERE id = ?", (self.item_id,))
        self.assertEqual(self.client.post(
            f"/admin/items/{self.item_id}/return", data={"confirm_handover": "yes"}
        ).status_code, 409)
        with self.app.app_context():
            connection = get_db()
            with connection:
                connection.execute("UPDATE items SET status = 'open' WHERE id = ?", (self.item_id,))
        self.assertEqual(self.client.post(
            f"/admin/claims/{claim_id}/approve", data={"explanation": "Evidence matches"}
        ).status_code, 302)
        for data in ({}, {"confirm_handover": "0"}, {"confirm_handover": "false"}):
            self.assertEqual(self.client.post(
                f"/admin/items/{self.item_id}/return", data=data
            ).status_code, 400)
        self.assertEqual(self.row("items", self.item_id)["status"], "claimed")
        response = self.client.post(
            f"/admin/items/{self.item_id}/return", data={"confirm_handover": "yes"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.row("items", self.item_id)["status"], "returned")
        self.assertEqual(self.row("claims", claim_id)["status"], "approved")
        stamp = self.row("items", self.item_id)["updated_at"]
        self.assertEqual(self.client.post(
            f"/admin/items/{self.item_id}/return", data={"confirm_handover": "yes"}
        ).status_code, 409)
        self.assertEqual(self.row("items", self.item_id)["updated_at"], stamp)

    def test_approval_failure_rolls_back_every_change(self):
        first_claim = self.add_claim(self.first)
        second_claim = self.add_claim(self.second)
        with self.app.app_context():
            connection = get_db()
            with connection:
                connection.execute(
                    """CREATE TRIGGER fail_claimed_update BEFORE UPDATE OF status ON items
                       WHEN NEW.status = 'claimed'
                       BEGIN SELECT RAISE(ABORT, 'test failure'); END"""
                )
        self.sign_in(self.client, self.admin)
        response = self.client.post(
            f"/admin/claims/{first_claim}/approve", data={"explanation": "Evidence matches"}
        )
        self.assertEqual(response.status_code, 409)
        for claim_id in (first_claim, second_claim):
            row = self.row("claims", claim_id)
            self.assertEqual(row["status"], "pending")
            self.assertIsNone(row["admin_note"])
            self.assertIsNone(row["reviewed_at"])
        self.assertEqual(self.row("items", self.item_id)["status"], "open")

    def test_competing_approvals_use_separate_connections_and_one_wins(self):
        first_claim = self.add_claim(self.first)
        second_claim = self.add_claim(self.second)
        first_client = self.app.test_client()
        second_client = self.app.test_client()
        self.sign_in(first_client, self.admin)
        self.sign_in(second_client, self.admin)
        first_locked = threading.Event()
        second_attempting = threading.Event()
        release_first = threading.Event()
        outcomes = {}
        original_begin = admin_module._begin_immediate

        def controlled_begin(connection):
            name = threading.current_thread().name
            if name == "approve-first":
                original_begin(connection)
                first_locked.set()
                if not release_first.wait(5):
                    raise RuntimeError("First approval was not released")
            elif name == "approve-second":
                second_attempting.set()
                original_begin(connection)
            else:
                original_begin(connection)

        def run(name, client, claim_id):
            try:
                outcomes[name] = client.post(
                    f"/admin/claims/{claim_id}/approve",
                    data={"explanation": "Evidence matches"},
                ).status_code
            except Exception as error:
                outcomes[name] = error

        first_thread = threading.Thread(
            target=run, args=("first", first_client, first_claim), name="approve-first"
        )
        second_thread = threading.Thread(
            target=run, args=("second", second_client, second_claim), name="approve-second"
        )
        with patch.object(admin_module, "_begin_immediate", side_effect=controlled_begin):
            first_thread.start()
            try:
                self.assertTrue(first_locked.wait(5), "First route did not acquire the SQLite write lock")
                second_thread.start()
                self.assertTrue(second_attempting.wait(5), "Second route did not attempt the write lock")
            finally:
                release_first.set()
                first_thread.join(10)
                if second_thread.ident is not None:
                    second_thread.join(10)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(outcomes.get("first"), 302)
        self.assertEqual(outcomes.get("second"), 409)
        self.assertEqual(self.row("claims", first_claim)["status"], "approved")
        self.assertEqual(self.row("claims", second_claim)["status"], "rejected")
        self.assertEqual(self.row("items", self.item_id)["status"], "claimed")

    def test_missing_resources_get_mutations_and_invalid_workflow(self):
        claim_id = self.add_claim(self.first)
        self.sign_in(self.client, self.admin)
        for action in ("approve", "reject"):
            self.assertEqual(self.client.get(f"/admin/claims/{claim_id}/{action}").status_code, 405)
            self.assertEqual(self.client.post(
                f"/admin/claims/999999/{action}", data={"explanation": "Valid reason"}
            ).status_code, 404)
        self.assertEqual(self.client.get(f"/admin/items/{self.item_id}/return").status_code, 405)
        self.assertEqual(self.client.post(
            "/admin/items/999999/return", data={"confirm_handover": "yes"}
        ).status_code, 404)
        self.assertEqual(self.client.post(
            f"/admin/items/{self.item_id}/return", data={"confirm_handover": "yes"}
        ).status_code, 409)
        self.assertEqual(self.row("claims", claim_id)["status"], "pending")

    def test_decisions_reject_lost_or_nonopen_reports(self):
        with self.app.app_context():
            connection = get_db()
            with connection:
                lost_item = self._item(connection, self.owner, "lost")
        lost_claim = self.add_claim(self.first, lost_item)
        found_claim = self.add_claim(self.second)
        with self.app.app_context():
            connection = get_db()
            with connection:
                connection.execute(
                    "UPDATE items SET status = 'closed' WHERE id = ?", (self.item_id,)
                )
        self.sign_in(self.client, self.admin)
        for claim_id in (lost_claim, found_claim):
            for action in ("approve", "reject"):
                self.assertEqual(self.client.post(
                    f"/admin/claims/{claim_id}/{action}",
                    data={"explanation": "Valid reason"},
                ).status_code, 409)
            self.assertEqual(self.row("claims", claim_id)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
