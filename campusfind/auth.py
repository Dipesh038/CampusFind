"""Registration, login, logout, and reusable authorization helpers."""

import sqlite3
from functools import wraps

import click
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.datastructures import MultiDict
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .forms import LoginForm, LogoutForm, RegisterForm


bp = Blueprint("auth", __name__, url_prefix="/auth")
INVALID_LOGIN = "Invalid email or password."
DUPLICATE_EMAIL = "Email is already registered."


class DuplicateEmailError(Exception):
    """An email is already used by another account."""


def _create_user(name, email, password, role):
    """Insert one account atomically; callers choose a trusted role."""
    password_hash = generate_password_hash(password)
    try:
        with get_db() as connection:
            connection.execute(
                """INSERT INTO users (name, email, password_hash, role)
                   VALUES (?, ?, ?, ?)""",
                (name, email, password_hash, role),
            )
    except sqlite3.IntegrityError as error:
        if (
            error.sqlite_errorname == "SQLITE_CONSTRAINT_UNIQUE"
            and "users.email" in str(error)
        ):
            raise DuplicateEmailError from None
        raise


@bp.route("/register", methods=("GET", "POST"))
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        try:
            _create_user(form.name.data, form.email.data, form.password.data, "student")
        except DuplicateEmailError:
            form.email.errors.append(DUPLICATE_EMAIL)
        else:
            flash("Account created. Please log in.")
            return redirect(url_for("auth.login"))
    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=("GET", "POST"))
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = get_db().execute(
            "SELECT id, password_hash FROM users WHERE email = ?", (form.email.data,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], form.password.data):
            form.password.errors.append(INVALID_LOGIN)
        else:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            flash("You are logged in.")
            return redirect(url_for("main.index"))
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
def logout():
    form = LogoutForm()
    if form.validate_on_submit():
        session.clear()
        flash("You are logged out.")
        return redirect(url_for("main.index"))
    abort(400)


def load_logged_in_user():
    """Read identity and role from the database on each request."""
    g.user = None
    if request.endpoint == "main.health":
        return
    user_id = session.get("user_id")
    if user_id is None:
        return
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        session.clear()
        return
    g.user = get_db().execute(
        "SELECT id, name, email, role FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if g.user is None:
        session.clear()


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(**kwargs)

    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        if g.user["role"] != "admin":
            abort(403)
        return view(**kwargs)

    return wrapped_view


def init_app(app):
    app.before_request(load_logged_in_user)

    @app.context_processor
    def inject_logout_form():
        return {"logout_form": LogoutForm() if g.get("user") is not None else None}

    @app.cli.command("create-admin")
    def create_admin_command():
        """Interactively create one administrator; never promote an account."""
        name = click.prompt("Name")
        email = click.prompt("Email")
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        with current_app.test_request_context():
            form = RegisterForm(
                formdata=MultiDict(
                    {
                        "name": name,
                        "email": email,
                        "password": password,
                        "confirm_password": password,
                    }
                ),
                meta={"csrf": False},
            )
            if not form.validate():
                for field_name, errors in form.errors.items():
                    label = form[field_name].label.text
                    for error in errors:
                        click.echo(f"{label}: {error}", err=True)
                raise click.ClickException("Administrator was not created.")
            try:
                _create_user(form.name.data, form.email.data, form.password.data, "admin")
            except DuplicateEmailError:
                raise click.ClickException(
                    "Email is already registered; no administrator was created."
                ) from None
        click.echo("Administrator created.")
