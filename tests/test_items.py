"""CF-04 report tests, each backed by a disposable SQLite database."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from campusfind import create_app
from campusfind.db import get_db


class ItemTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = create_app({
            "TESTING": True, "APP_ENV": "testing",
            "DATABASE": str(Path(directory.name) / "items.sqlite"),
            "WTF_CSRF_ENABLED": False,
        })
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client = self.app.test_client()
        with self.app.app_context():
            connection = get_db()
            with connection:
                self.owner = connection.execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                    ("Owner Student", "owner@example.edu", "fictional-hash"),
                ).lastrowid
                self.other = connection.execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                    ("Other Student", "other@example.edu", "fictional-hash"),
                ).lastrowid

    def sign_in(self, user_id):
        with self.client.session_transaction() as user_session:
            user_session.clear()
            user_session["user_id"] = user_id

    @staticmethod
    def form_data(**changes):
        data = {
            "report_type": "lost", "title": "Blue notebook",
            "description": "A blue notebook with class notes.",
            "category": "Books", "location": "Main library",
            "event_date": "2020-01-02",
        }
        data.update(changes)
        return data

    def add_item(self, reporter=None, **changes):
        values = self.form_data(**changes)
        with self.app.app_context():
            connection = get_db()
            with connection:
                return connection.execute(
                    """INSERT INTO items (reporter_id, report_type, title, description,
                       category, location, event_date) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (reporter or self.owner, values["report_type"], values["title"],
                     values["description"], values["category"], values["location"],
                     values["event_date"]),
                ).lastrowid

    def add_claim(self, item_id):
        with self.app.app_context():
            connection = get_db()
            with connection:
                connection.execute(
                    """INSERT INTO claims (item_id, claimant_id, proof_text, status,
                       reviewed_at) VALUES (?, ?, ?, ?, ?)""",
                    (item_id, self.other, "private ownership evidence", "rejected",
                     "2020-01-03T00:00:00Z"),
                )

    def item(self, item_id):
        with self.app.app_context():
            return get_db().execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    def test_public_list_detail_and_missing(self):
        item_id = self.add_item()
        self.assertEqual(self.client.get("/items").status_code, 200)
        self.assertIn(b"Blue notebook", self.client.get("/items").data)
        response = self.client.get(f"/items/{item_id}")
        self.assertEqual(response.status_code, 200)
        for value in (b"Books", b"Main library", b"2020-01-02", b"Open", b"Reported"):
            self.assertIn(value, response.data)
        self.assertEqual(self.client.get("/items/999999").status_code, 404)

    def test_authentication_required_for_creation_and_mutations(self):
        item_id = self.add_item()
        for path, method in (
            ("/items/new", "get"), ("/items/new", "post"),
            ("/items/mine", "get"), (f"/items/{item_id}/edit", "get"),
            (f"/items/{item_id}/edit", "post"),
            (f"/items/{item_id}/delete", "post"),
            (f"/items/{item_id}/close", "post"),
        ):
            with self.subTest(path=path, method=method):
                response = getattr(self.client, method)(path, data=self.form_data()) if method == "post" else self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/auth/login", response.headers["Location"])

    def test_valid_lost_and_found_creation_uses_server_identity_and_status(self):
        self.sign_in(self.owner)
        for report_type in ("lost", "found"):
            with self.subTest(report_type=report_type):
                response = self.client.post("/items/new", data=self.form_data(
                    report_type=report_type, title="  Blue notebook  ",
                    description="  A blue notebook with class notes.  ",
                    location="  Main library  ", reporter_id=self.other,
                    status="returned", created_at="1900-01-01", updated_at="1900-01-01",
                ))
                self.assertEqual(response.status_code, 302)
                item_id = int(response.headers["Location"].split("/")[-1])
                row = self.item(item_id)
                self.assertEqual(row["reporter_id"], self.owner)
                self.assertEqual(row["status"], "open")
                self.assertEqual(row["report_type"], report_type)
                self.assertEqual(row["title"], "Blue notebook")
                self.assertEqual(row["description"], "A blue notebook with class notes.")
                self.assertEqual(row["location"], "Main library")
                self.assertTrue(row["created_at"].endswith("Z"))
                self.assertEqual(row["created_at"], row["updated_at"])

    def test_invalid_form_values_do_not_create_items(self):
        self.sign_in(self.owner)
        future = (datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=1)).isoformat()
        bad_values = (
            {"report_type": "borrowed"}, {"category": "Secret"},
            {"title": "  x  "}, {"title": "x" * 121},
            {"description": "  short  "}, {"description": "x" * 2001},
            {"location": " x "}, {"location": "x" * 151},
            {"event_date": ""}, {"event_date": "2020-02-30"},
            {"event_date": future}, {"title": "   "},
        )
        for bad in bad_values:
            with self.subTest(bad=bad):
                response = self.client.post("/items/new", data=self.form_data(**bad))
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'role="alert"', response.data)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)

    def test_owner_edit_and_delete_when_open_without_claims(self):
        self.sign_in(self.owner)
        item_id = self.add_item()
        self.assertEqual(self.client.get(f"/items/{item_id}/edit").status_code, 200)
        response = self.client.post(f"/items/{item_id}/edit", data=self.form_data(
            title="Green notebook", status="claimed", reporter_id=self.other,
        ))
        self.assertEqual(response.status_code, 302)
        row = self.item(item_id)
        self.assertEqual(row["title"], "Green notebook")
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["reporter_id"], self.owner)
        self.assertEqual(self.client.post(f"/items/{item_id}/delete", data={}).status_code, 400)
        self.assertIsNotNone(self.item(item_id))
        response = self.client.post(f"/items/{item_id}/delete", data={"confirm_delete": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.item(item_id))

    def test_cross_user_mutations_are_forbidden(self):
        item_id = self.add_item()
        self.sign_in(self.other)
        for path, method, data in (
            (f"/items/{item_id}/edit", "get", None),
            (f"/items/{item_id}/edit", "post", self.form_data()),
            (f"/items/{item_id}/delete", "post", {"confirm_delete": "yes"}),
            (f"/items/{item_id}/close", "post", {}),
        ):
            with self.subTest(path=path, method=method):
                response = getattr(self.client, method)(path, data=data) if method == "post" else self.client.get(path)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.item(item_id)["status"], "open")
        self.assertEqual(self.client.post(f"/items/{item_id}/delete", data={}).status_code, 403)

    def test_claim_blocks_edit_delete_even_when_rejected(self):
        item_id = self.add_item()
        self.add_claim(item_id)
        self.sign_in(self.owner)
        self.assertEqual(self.client.get(f"/items/{item_id}/edit").status_code, 409)
        self.assertEqual(self.client.post(f"/items/{item_id}/edit", data=self.form_data()).status_code, 409)
        self.assertEqual(self.client.post(f"/items/{item_id}/delete", data={"confirm_delete": "yes"}).status_code, 409)
        self.assertIsNotNone(self.item(item_id))

    def test_late_claim_is_rechecked_under_write_lock(self):
        from campusfind import items
        item_id = self.add_item()
        self.sign_in(self.owner)
        original = items._editable_or_409
        calls = 0

        def add_after_initial_check(item):
            nonlocal calls
            calls += 1
            result = original(item)
            if calls == 1:
                self.add_claim(item_id)
            return result

        with patch.object(items, "_editable_or_409", side_effect=add_after_initial_check):
            response = self.client.post(f"/items/{item_id}/edit", data=self.form_data(title="Changed title"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.item(item_id)["title"], "Blue notebook")

    def test_non_open_reports_cannot_be_edited_or_deleted(self):
        self.sign_in(self.owner)
        for status in ("claimed", "returned", "closed"):
            with self.subTest(status=status):
                item_id = self.add_item()
                with self.app.app_context():
                    connection = get_db()
                    with connection:
                        connection.execute("UPDATE items SET status = ? WHERE id = ?", (status, item_id))
                self.assertEqual(self.client.get(f"/items/{item_id}/edit").status_code, 409)
                self.assertEqual(self.client.post(f"/items/{item_id}/edit", data=self.form_data()).status_code, 409)
                self.assertEqual(self.client.post(f"/items/{item_id}/delete", data={"confirm_delete": "yes"}).status_code, 409)

    def test_close_lost_and_reject_invalid_transitions(self):
        lost_id = self.add_item()
        found_id = self.add_item(report_type="found")
        self.sign_in(self.owner)
        self.assertEqual(self.client.post(f"/items/{found_id}/close").status_code, 409)
        self.assertEqual(self.item(found_id)["status"], "open")
        response = self.client.post(f"/items/{lost_id}/close")
        self.assertEqual(response.status_code, 302)
        row = self.item(lost_id)
        self.assertEqual(row["status"], "closed")
        self.assertGreaterEqual(row["updated_at"], row["created_at"])
        stamp = row["updated_at"]
        self.assertEqual(self.client.post(f"/items/{lost_id}/close").status_code, 409)
        self.assertEqual(self.item(lost_id)["updated_at"], stamp)

    def test_get_cannot_mutate_and_missing_mutations_are_404(self):
        item_id = self.add_item()
        self.sign_in(self.owner)
        for action in ("delete", "close"):
            self.assertEqual(self.client.get(f"/items/{item_id}/{action}").status_code, 405)
        self.assertIsNotNone(self.item(item_id))
        for action, data in (("edit", self.form_data()), ("delete", {"confirm_delete": "yes"}), ("close", {})):
            self.assertEqual(self.client.get(f"/items/999999/edit").status_code, 404)
            self.assertEqual(self.client.post(f"/items/999999/{action}", data=data).status_code, 404)

    def test_search_matches_three_fields_and_literal_wildcards(self):
        title_id = self.add_item(title="Camera % case")
        desc_id = self.add_item(title="Pen case", description="Stored near _marker desk")
        loc_id = self.add_item(title="Wallet", location="South\\wing")
        for query, expected in (("Camera", title_id), ("_marker", desc_id), ("South\\wing", loc_id)):
            response = self.client.get("/items", query_string={"q": query})
            self.assertEqual(response.status_code, 200)
            self.assertIn(f"/items/{expected}".encode(), response.data)
            self.assertIn(b"1 report found", response.data)
        for query in ("%", "_", "' OR 1=1 --"):
            response = self.client.get("/items", query_string={"q": query})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data.count(b"<h2><a href="), 1 if query in ("%", "_") else 0)

    def test_combined_filters_empty_results_and_bad_parameters(self):
        self.add_item(title="Camera", category="Electronics", report_type="found")
        self.add_item(title="Camera case", category="Bags", report_type="lost")
        response = self.client.get("/items", query_string={
            "q": "Camera", "category": "Electronics", "type": "found", "status": "open"
        })
        self.assertIn(b"1 report found", response.data)
        self.assertIn(b"Clear filters", response.data)
        response = self.client.get("/items", query_string={"q": "Camera", "status": "closed"})
        self.assertIn(b"No reports match these filters", response.data)
        for params in ({"category": "Invalid"}, {"type": "neither"}, {"status": "bad"},
                       {"page": "0"}, {"page": "abc"}, {"page": "-1"},
                       {"page": "9" * 5000}):
            self.assertEqual(self.client.get("/items", query_string=params).status_code, 400)

    def test_pagination_stable_order_and_preserved_filters(self):
        ids = [self.add_item(title=f"Book {number}") for number in range(14)]
        first = self.client.get("/items", query_string={"q": "Book", "category": "Books", "type": "lost", "status": "open", "page": 1})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data.count(b"<h2><a href="), 12)
        self.assertIn(f"/items/{ids[-1]}".encode(), first.data)
        self.assertNotIn(f'<a href="/items/{ids[0]}"'.encode(), first.data)
        self.assertIn(b"q=Book", first.data)
        self.assertIn(b"category=Books", first.data)
        self.assertIn(b"type=lost", first.data)
        self.assertIn(b"status=open", first.data)
        second = self.client.get("/items", query_string={"q": "Book", "category": "Books", "type": "lost", "status": "open", "page": 2})
        self.assertEqual(second.data.count(b"<h2><a href="), 2)
        self.assertIn(f"/items/{ids[0]}".encode(), second.data)
        self.assertIn(b"Previous", second.data)
        beyond = self.client.get("/items", query_string={"page": 3})
        self.assertEqual(beyond.status_code, 200)
        self.assertIn(b"No reports match", beyond.data)

    def test_my_reports_isolation_and_permitted_actions(self):
        mine = self.add_item()
        others = self.add_item(reporter=self.other, title="Other report")
        self.sign_in(self.owner)
        response = self.client.get("/items/mine")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"/items/{mine}".encode(), response.data)
        self.assertNotIn(f"/items/{others}".encode(), response.data)
        self.assertIn(b"Edit", response.data)
        self.add_claim(mine)
        response = self.client.get("/items/mine")
        self.assertNotIn(f"/items/{mine}/edit".encode(), response.data)

    def test_public_pages_escape_text_and_hide_private_fields(self):
        item_id = self.add_item(title="<script>alert(1)</script>", description="Private? <b>not HTML</b>")
        self.add_claim(item_id)
        for path in ("/items", f"/items/{item_id}"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b"<script>", response.data)
            self.assertIn(b"&lt;script&gt;", response.data)
            self.assertNotIn(b"<b>not HTML</b>", response.data)
            for private in (b"owner@example.edu", b"other@example.edu", b"private ownership evidence", b"fictional-hash"):
                self.assertNotIn(private, response.data)


if __name__ == "__main__":
    unittest.main()
