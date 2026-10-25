"""Public report browsing and reporter-owned report changes."""

import sqlite3
from datetime import date, datetime, timezone

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .db import get_db
from .forms import ITEM_CATEGORIES, ClaimForm, ItemForm


bp = Blueprint("items", __name__)
PAGE_SIZE = 12
REPORT_TYPES = {"lost", "found"}
STATUSES = {"open", "claimed", "returned", "closed"}


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _item_or_404(item_id):
    item = get_db().execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        abort(404)
    return item


def _owner_or_403(item):
    if item["reporter_id"] != g.user["id"]:
        abort(403)


def _editable_or_409(item):
    if item["status"] != "open" or get_db().execute(
        "SELECT 1 FROM claims WHERE item_id = ? LIMIT 1", (item["id"],)
    ).fetchone():
        abort(409, description="This report can no longer be edited or deleted.")


def _like_literal(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _busy_or_raise(error):
    if getattr(error, "sqlite_errorcode", None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        abort(503, description="The database is busy. Please try again shortly.")
    raise error


@bp.get("/items")
def browse():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    report_type = request.args.get("type", "")
    status = request.args.get("status", "")
    page_text = request.args.get("page", "1")
    if (
        len(q) > 200
        or (category and category not in ITEM_CATEGORIES)
        or (report_type and report_type not in REPORT_TYPES)
        or (status and status not in STATUSES)
        or len(page_text) > 6
        or not page_text.isascii()
        or not page_text.isdecimal()
        or int(page_text) < 1
    ):
        abort(400, description="Invalid report filter or page.")
    page = int(page_text)

    clauses = []
    parameters = []
    if q:
        pattern = "%" + _like_literal(q) + "%"
        clauses.append(
            "(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' "
            "OR location LIKE ? ESCAPE '\\')"
        )
        parameters.extend((pattern, pattern, pattern))
    if category:
        clauses.append("category = ?")
        parameters.append(category)
    if report_type:
        clauses.append("report_type = ?")
        parameters.append(report_type)
    if status:
        clauses.append("status = ?")
        parameters.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    connection = get_db()
    total = connection.execute("SELECT COUNT(*) FROM items" + where, parameters).fetchone()[0]
    reports = connection.execute(
        "SELECT id, title, description, category, location, report_type, event_date, "
        "status, created_at FROM items" + where
        + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (*parameters, PAGE_SIZE, (page - 1) * PAGE_SIZE),
    ).fetchall()
    filters = {key: value for key, value in (
        ("q", q), ("category", category), ("type", report_type), ("status", status)
    ) if value}
    return render_template(
        "items/list.html", reports=reports, q=q, category=category,
        report_type=report_type, status=status, categories=ITEM_CATEGORIES,
        page=page, total=total, page_size=PAGE_SIZE, filters=filters,
    )


@bp.get("/items/mine")
@login_required
def mine():
    connection = get_db()
    reports = connection.execute(
        """SELECT i.id, i.title, i.report_type, i.category, i.location, i.event_date,
                  i.status, i.created_at,
                  EXISTS (SELECT 1 FROM claims c WHERE c.item_id = i.id) AS has_claims
           FROM items i WHERE i.reporter_id = ?
           ORDER BY i.created_at DESC, i.id DESC""",
        (g.user["id"],),
    ).fetchall()
    received_claims = connection.execute(
        """SELECT c.id, c.item_id, c.claimant_id, c.proof_text, c.status,
                  c.admin_note, c.created_at, c.reviewed_at,
                  i.title AS item_title, i.status AS item_status,
                  u.name AS claimant_name, u.email AS claimant_email
           FROM claims c
           JOIN items i ON i.id = c.item_id
           JOIN users u ON u.id = c.claimant_id
           WHERE i.reporter_id = ?
           ORDER BY CASE WHEN c.status = 'pending' THEN 0 ELSE 1 END,
                    c.created_at DESC, c.id DESC""",
        (g.user["id"],),
    ).fetchall()
    from .forms import FinderDecisionForm
    return render_template(
        "items/mine.html",
        reports=reports,
        received_claims=received_claims,
        decision_form=FinderDecisionForm(),
    )


@bp.get("/items/<int:item_id>")
def detail(item_id):
    return render_detail(item_id)


def render_detail(item_id, claim_form=None):
    """Render only public item data and the current student's claim form state."""
    item = _item_or_404(item_id)
    owned = g.user is not None and item["reporter_id"] == g.user["id"]
    can_edit = owned and item["status"] == "open" and not get_db().execute(
        "SELECT 1 FROM claims WHERE item_id = ? LIMIT 1", (item_id,)
    ).fetchone()
    can_close = owned and item["status"] == "open" and item["report_type"] == "lost"
    claim_message = None
    if item["report_type"] == "found" and item["status"] == "open":
        if g.user is None:
            claim_message = "Sign in as a student to submit a claim."
        elif g.user["role"] != "student":
            claim_message = "Administrators cannot submit ownership claims."
        elif owned:
            claim_message = "You cannot claim your own report."
        elif get_db().execute(
            "SELECT 1 FROM claims WHERE item_id = ? AND claimant_id = ? LIMIT 1",
            (item_id, g.user["id"]),
        ).fetchone():
            claim_message = "You have already submitted a claim for this report."
        elif claim_form is None:
            claim_form = ClaimForm()
    elif item["report_type"] != "found":
        claim_message = "Claims are available only for found reports."
    else:
        claim_message = "This found report is no longer open for claims."
    return render_template(
        "items/detail.html", item=item, can_edit=can_edit, can_close=can_close,
        owned=owned,
        claim_form=claim_form if claim_message is None else None,
        claim_message=claim_message,
    )



@bp.route("/items/new", methods=("GET", "POST"))
@login_required
def new():
    form = ItemForm()
    if form.validate_on_submit():
        stamp = _utc_now()
        try:
            connection = get_db()
            with connection:
                cursor = connection.execute(
                    """INSERT INTO items
                       (reporter_id, report_type, title, description, category, location,
                        event_date, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
                    (g.user["id"], form.report_type.data, form.title.data,
                     form.description.data, form.category.data, form.location.data,
                     form.event_date.data.isoformat(), stamp, stamp),
                )
                item_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            abort(409, description="The report could not be saved. Please try again.")
        except sqlite3.OperationalError as error:
            _busy_or_raise(error)
        flash("Report created.")
        return redirect(url_for("items.detail", item_id=item_id))
    return render_template("items/form.html", form=form, heading="New Report")


@bp.route("/items/<int:item_id>/edit", methods=("GET", "POST"))
@login_required
def edit(item_id):
    item = _item_or_404(item_id)
    _owner_or_403(item)
    _editable_or_409(item)
    if request.method == "GET":
        try:
            form = ItemForm(data={
                "report_type": item["report_type"], "title": item["title"],
                "description": item["description"], "category": item["category"],
                "location": item["location"],
                "event_date": date.fromisoformat(item["event_date"]),
            })
        except ValueError:
            abort(409, description="This report has an invalid stored date.")
    else:
        form = ItemForm()
    if form.validate_on_submit():
        connection = get_db()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                current = _item_or_404(item_id)
                _owner_or_403(current)
                _editable_or_409(current)
                connection.execute(
                    """UPDATE items SET report_type = ?, title = ?, description = ?,
                       category = ?, location = ?, event_date = ?, updated_at = ?
                       WHERE id = ?""",
                    (form.report_type.data, form.title.data, form.description.data,
                     form.category.data, form.location.data,
                     form.event_date.data.isoformat(), _utc_now(), item_id),
                )
        except sqlite3.IntegrityError:
            abort(409, description="The report changed. Reload and try again.")
        except sqlite3.OperationalError as error:
            _busy_or_raise(error)
        flash("Report updated.")
        return redirect(url_for("items.detail", item_id=item_id))
    return render_template("items/form.html", form=form, heading="Edit Report")


@bp.post("/items/<int:item_id>/delete")
@login_required
def delete(item_id):
    connection = get_db()
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            item = _item_or_404(item_id)
            _owner_or_403(item)
            _editable_or_409(item)
            if request.form.get("confirm_delete") != "yes":
                abort(400, description="Confirm deletion using the checkbox on the report page.")
            connection.execute("DELETE FROM items WHERE id = ?", (item_id,))
    except sqlite3.IntegrityError:
        abort(409, description="This report now has a claim and cannot be deleted.")
    except sqlite3.OperationalError as error:
        _busy_or_raise(error)
    flash("Report deleted.")
    return redirect(url_for("items.mine"))


@bp.post("/items/<int:item_id>/close")
@login_required
def close(item_id):
    connection = get_db()
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            item = _item_or_404(item_id)
            _owner_or_403(item)
            if item["report_type"] != "lost" or item["status"] != "open":
                abort(409, description="Only an open lost report can be closed.")
            connection.execute(
                "UPDATE items SET status = 'closed', updated_at = ? WHERE id = ?",
                (_utc_now(), item_id),
            )
    except sqlite3.IntegrityError:
        abort(409, description="The report could not be closed. Reload and try again.")
    except sqlite3.OperationalError as error:
        _busy_or_raise(error)
    flash("Lost report closed.")
    return redirect(url_for("items.detail", item_id=item_id))
