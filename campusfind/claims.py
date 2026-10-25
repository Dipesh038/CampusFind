"""Student ownership claims and each student's private claim history."""

import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, url_for

from .auth import login_required
from .db import get_db
from .forms import ClaimForm
from .items import _busy_or_raise, _item_or_404, _utc_now, render_detail


bp = Blueprint("claims", __name__)


def _eligible_or_error(item, claimant_id):
    if item["reporter_id"] == claimant_id:
        abort(403, description="You cannot claim your own report.")
    if item["report_type"] != "found" or item["status"] != "open":
        abort(409, description="Only an open found report can receive claims.")
    if get_db().execute(
        "SELECT 1 FROM claims WHERE item_id = ? AND claimant_id = ? LIMIT 1",
        (item["id"], claimant_id),
    ).fetchone():
        abort(409, description="You have already submitted a claim for this report.")


@bp.post("/items/<int:item_id>/claims")
@login_required
def submit(item_id):
    if g.user["role"] != "student":
        abort(403)
    item = _item_or_404(item_id)
    _eligible_or_error(item, g.user["id"])
    form = ClaimForm()
    if not form.validate_on_submit():
        return render_detail(item_id, claim_form=form), 400

    connection = get_db()
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            current = _item_or_404(item_id)
            _eligible_or_error(current, g.user["id"])
            connection.execute(
                """INSERT INTO claims
                   (item_id, claimant_id, proof_text, status, created_at)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (item_id, g.user["id"], form.proof_text.data, _utc_now()),
            )
    except sqlite3.IntegrityError:
        abort(409, description="This claim could not be submitted. Reload and try again.")
    except sqlite3.OperationalError as error:
        _busy_or_raise(error)
    flash("Claim submitted for review.")
    return redirect(url_for("claims.mine"))


@bp.get("/claims/mine")
@login_required
def mine():
    if g.user["role"] != "student":
        abort(403)
    claims = get_db().execute(
        """SELECT c.id, c.proof_text, c.status, c.admin_note, c.created_at,
                  c.reviewed_at, i.id AS item_id, i.title AS item_title
           FROM claims c JOIN items i ON i.id = c.item_id
           WHERE c.claimant_id = ? ORDER BY c.created_at DESC, c.id DESC""",
        (g.user["id"],),
    ).fetchall()
    return render_template("claims/mine.html", claims=claims)
