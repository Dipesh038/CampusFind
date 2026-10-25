"""Student ownership claims and each student's private claim history."""

import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, url_for

from .auth import login_required
from .db import get_db
from .forms import ClaimForm, FinderDecisionForm
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


def _finder_claim_or_error(claim_id):
    claim = get_db().execute(
        """SELECT c.id, c.item_id, c.claimant_id, c.status AS claim_status,
                  c.proof_text, i.reporter_id, i.report_type, i.status AS item_status,
                  i.title AS item_title, u.name AS claimant_name, u.email AS claimant_email
           FROM claims c
           JOIN items i ON i.id = c.item_id
           JOIN users u ON u.id = c.claimant_id
           WHERE c.id = ?""",
        (claim_id,),
    ).fetchone()
    if claim is None:
        abort(404)
    if claim["reporter_id"] != g.user["id"]:
        abort(403, description="Only the finder who reported this item can decide this claim.")
    return claim


@bp.post("/claims/<int:claim_id>/accept")
@login_required
def finder_accept(claim_id):
    claim = _finder_claim_or_error(claim_id)
    if (claim["claim_status"] != "pending" or claim["report_type"] != "found"
            or claim["item_status"] != "open"):
        abort(409, description="This claim can no longer be decided.")

    form = FinderDecisionForm()
    note = "Accepted by finder."
    if form.validate_on_submit() and form.explanation.data:
        note = f"Accepted by finder: {form.explanation.data}"

    now = _utc_now()
    connection = get_db()
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            current = _finder_claim_or_error(claim_id)
            if (current["claim_status"] != "pending" or current["report_type"] != "found"
                    or current["item_status"] != "open"):
                abort(409, description="This claim can no longer be decided.")
            connection.execute(
                """UPDATE claims
                   SET status = 'approved', admin_note = ?, reviewed_at = ?
                   WHERE id = ?""",
                (note, now, claim_id),
            )
            connection.execute(
                """UPDATE items
                   SET status = 'claimed', updated_at = ?
                   WHERE id = ?""",
                (now, current["item_id"]),
            )
            connection.execute(
                """UPDATE claims
                   SET status = 'rejected',
                       admin_note = 'Another claim was accepted by the finder.',
                       reviewed_at = ?
                   WHERE item_id = ? AND status = 'pending' AND id != ?""",
                (now, current["item_id"], claim_id),
            )
    except sqlite3.OperationalError as error:
        _busy_or_raise(error)

    flash(f"Claim accepted! You can now contact {claim['claimant_name']} at {claim['claimant_email']} to coordinate handover.")
    return redirect(url_for("items.mine"))


@bp.post("/claims/<int:claim_id>/reject")
@login_required
def finder_reject(claim_id):
    claim = _finder_claim_or_error(claim_id)
    if (claim["claim_status"] != "pending" or claim["report_type"] != "found"
            or claim["item_status"] != "open"):
        abort(409, description="This claim can no longer be decided.")

    form = FinderDecisionForm()
    note = "Rejected by finder."
    if form.validate_on_submit() and form.explanation.data:
        note = f"Rejected by finder: {form.explanation.data}"

    now = _utc_now()
    connection = get_db()
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            current = _finder_claim_or_error(claim_id)
            if current["claim_status"] != "pending":
                abort(409, description="This claim can no longer be decided.")
            connection.execute(
                """UPDATE claims
                   SET status = 'rejected', admin_note = ?, reviewed_at = ?
                   WHERE id = ?""",
                (note, now, claim_id),
            )
    except sqlite3.OperationalError as error:
        _busy_or_raise(error)

    flash(f"Claim from {claim['claimant_name']} was rejected.")
    return redirect(url_for("items.mine"))


