"""Administrator claim decisions and confirmed found-item handover."""

import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .auth import admin_required
from .db import get_db
from .forms import DecisionForm, ReturnForm
from .items import _busy_or_raise, _utc_now


bp = Blueprint("admin", __name__)
PAGE_SIZE = 10
COMPETING_NOTE = "Another claim was approved for this item."


def _page(name):
    value = request.args.get(name, "1")
    if len(value) > 6 or not value.isascii() or not value.isdecimal() or int(value) < 1:
        abort(400, description="Invalid dashboard page.")
    return int(value)


def _begin_immediate(connection):
    connection.execute("BEGIN IMMEDIATE")


def _claim_or_404(claim_id):
    claim = get_db().execute(
        """SELECT c.id, c.item_id, c.status AS claim_status,
                  i.report_type, i.status AS item_status
           FROM claims c JOIN items i ON i.id = c.item_id WHERE c.id = ?""",
        (claim_id,),
    ).fetchone()
    if claim is None:
        abort(404)
    return claim


def _pending_open_found_or_409(claim):
    if (claim["claim_status"] != "pending" or claim["report_type"] != "found"
            or claim["item_status"] != "open"):
        abort(409, description="This claim can no longer be decided.")


def _decision_note():
    form = DecisionForm()
    if not form.validate_on_submit():
        message = form.explanation.errors[0] if form.explanation.errors else "Enter a valid explanation."
        abort(400, description=message)
    return form.explanation.data


@bp.get("/admin")
@admin_required
def dashboard():
    pending_page = _page("pending_page")
    handover_page = _page("handover_page")
    connection = get_db()
    counts = connection.execute(
        """SELECT
             (SELECT COUNT(*) FROM items) AS total_reports,
             (SELECT COUNT(*) FROM items WHERE report_type = 'lost' AND status = 'open') AS open_lost,
             (SELECT COUNT(*) FROM items WHERE report_type = 'found' AND status = 'open') AS open_found,
             (SELECT COUNT(*) FROM claims WHERE status = 'pending') AS pending_claims,
             (SELECT COUNT(*) FROM items i WHERE i.report_type = 'found' AND i.status = 'claimed'
                AND EXISTS (SELECT 1 FROM claims c WHERE c.item_id = i.id AND c.status = 'approved')) AS awaiting_handover,
             (SELECT COUNT(*) FROM items WHERE report_type = 'found' AND status = 'returned') AS returned_found"""
    ).fetchone()
    pending = connection.execute(
        """SELECT c.id, c.proof_text, c.created_at, i.id AS item_id, i.title,
                  i.category, i.location, i.event_date, i.status AS item_status
           FROM claims c JOIN items i ON i.id = c.item_id
           WHERE c.status = 'pending'
           ORDER BY c.created_at ASC, c.id ASC LIMIT ? OFFSET ?""",
        (PAGE_SIZE, (pending_page - 1) * PAGE_SIZE),
    ).fetchall()
    handovers = connection.execute(
        """SELECT i.id, i.title, i.category, i.location, i.event_date
           FROM items i WHERE i.report_type = 'found' AND i.status = 'claimed'
             AND EXISTS (SELECT 1 FROM claims c WHERE c.item_id = i.id AND c.status = 'approved')
           ORDER BY i.created_at ASC, i.id ASC LIMIT ? OFFSET ?""",
        (PAGE_SIZE, (handover_page - 1) * PAGE_SIZE),
    ).fetchall()
    return render_template(
        "admin/dashboard.html", counts=counts, pending=pending, handovers=handovers,
        pending_page=pending_page, handover_page=handover_page, page_size=PAGE_SIZE,
    )


@bp.post("/admin/claims/<int:claim_id>/approve")
@admin_required
def approve(claim_id):
    note = _decision_note()
    connection = get_db()
    try:
        with connection:
            _begin_immediate(connection)
            claim = _claim_or_404(claim_id)
            _pending_open_found_or_409(claim)
            stamp = _utc_now()
            approved = connection.execute(
                """UPDATE claims SET status = 'approved', admin_note = ?, reviewed_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (note, stamp, claim_id),
            )
            if approved.rowcount != 1:
                abort(409, description="This claim was already decided.")
            competing_count = connection.execute(
                "SELECT COUNT(*) FROM claims WHERE item_id = ? AND id <> ? AND status = 'pending'",
                (claim["item_id"], claim_id),
            ).fetchone()[0]
            rejected = connection.execute(
                """UPDATE claims SET status = 'rejected', admin_note = ?, reviewed_at = ?
                   WHERE item_id = ? AND id <> ? AND status = 'pending'""",
                (COMPETING_NOTE, stamp, claim["item_id"], claim_id),
            )
            if rejected.rowcount != competing_count:
                abort(409, description="Competing claims changed during approval.")
            item_update = connection.execute(
                """UPDATE items SET status = 'claimed', updated_at = ?
                   WHERE id = ? AND report_type = 'found' AND status = 'open'""",
                (stamp, claim["item_id"]),
            )
            if item_update.rowcount != 1:
                abort(409, description="The found report changed before approval.")
    except sqlite3.IntegrityError:
        abort(409, description="This claim conflicts with an existing decision.")
    except sqlite3.OperationalError as error:
        _busy_or_raise(error)
    flash("Claim approved; competing pending claims were rejected.")
    return redirect(url_for("admin.dashboard"))


@bp.post("/admin/claims/<int:claim_id>/reject")
@admin_required
def reject(claim_id):
    note = _decision_note()
    connection = get_db()
    try:
        with connection:
            _begin_immediate(connection)
            claim = _claim_or_404(claim_id)
            _pending_open_found_or_409(claim)
            updated = connection.execute(
                """UPDATE claims SET status = 'rejected', admin_note = ?, reviewed_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (note, _utc_now(), claim_id),
            )
            if updated.rowcount != 1:
                abort(409, description="This claim was already decided.")
    except sqlite3.IntegrityError:
        abort(409, description="The claim changed. Reload and try again.")
    except sqlite3.OperationalError as error:
        _busy_or_raise(error)
    flash("Claim rejected; the found report remains open.")
    return redirect(url_for("admin.dashboard"))


@bp.post("/admin/items/<int:item_id>/return")
@admin_required
def return_item(item_id):
    form = ReturnForm()
    connection = get_db()
    try:
        with connection:
            _begin_immediate(connection)
            item = connection.execute(
                "SELECT id, report_type, status FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if item is None:
                abort(404)
            if item["report_type"] != "found" or item["status"] != "claimed":
                abort(409, description="Only a claimed found report can be returned.")
            approved_count = connection.execute(
                "SELECT COUNT(*) FROM claims WHERE item_id = ? AND status = 'approved'",
                (item_id,),
            ).fetchone()[0]
            if approved_count != 1:
                abort(409, description="A return requires exactly one approved claim.")
            if not form.validate_on_submit() or request.form.get("confirm_handover") != "yes":
                abort(400, description="Confirm that the item was handed over.")
            updated = connection.execute(
                """UPDATE items SET status = 'returned', updated_at = ?
                   WHERE id = ? AND report_type = 'found' AND status = 'claimed'""",
                (_utc_now(), item_id),
            )
            if updated.rowcount != 1:
                abort(409, description="The found report changed before return.")
    except sqlite3.IntegrityError:
        abort(409, description="The return could not be recorded. Reload and try again.")
    except sqlite3.OperationalError as error:
        _busy_or_raise(error)
    flash("Handover confirmed; found report marked returned.")
    return redirect(url_for("admin.dashboard"))
