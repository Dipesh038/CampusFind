"""CampusFind application factory and its initial public routes."""

import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from flask import Blueprint, Flask, current_app, jsonify, render_template
from flask_wtf.csrf import CSRFError, CSRFProtect


_DEVELOPMENT_SECRET = "campusfind-development-only-secret-do-not-deploy"
csrf = CSRFProtect()
main = Blueprint("main", __name__)


@main.get("/")
def index():
    return render_template("index.html")


@main.get("/health")
def health():
    from .db import get_db

    try:
        get_db().execute("SELECT 1").fetchone()
    except sqlite3.Error:
        return jsonify(status="unavailable"), 503
    response = {"status": "ok"}
    if current_app.config.get("RELEASE_SHA"):
        response["release"] = current_app.config["RELEASE_SHA"]
    return jsonify(response), 200


def create_app(test_config=None):
    """Create the app without opening a database connection."""
    app = Flask(__name__, instance_relative_config=True)
    environment = os.environ.get("APP_ENV", "development").lower()
    secret = os.environ.get("SECRET_KEY")
    if environment != "production" and not secret:
        secret = _DEVELOPMENT_SECRET
    app.config.from_mapping(
        APP_ENV=environment,
        SECRET_KEY=secret,
        DATABASE=os.environ.get("DATABASE")
        or str(Path(app.instance_path) / "campusfind.sqlite"),
        RELEASE_SHA=os.environ.get("RELEASE_SHA"),
    )
    if test_config is not None:
        app.config.update(test_config)

    environment = app.config["APP_ENV"]
    if environment not in {"development", "production", "testing"}:
        raise RuntimeError("APP_ENV must be development, testing, or production")

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=environment == "production",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
        SESSION_REFRESH_EACH_REQUEST=False,
    )

    if environment == "production":
        if not Path(app.config["DATABASE"]).is_absolute():
            raise RuntimeError("Production DATABASE must be an absolute path")
        if app.debug or os.environ.get("FLASK_DEBUG") == "1":
            raise RuntimeError("Debug mode is not allowed in production")
        secret = app.config["SECRET_KEY"]
        if (
            not isinstance(secret, str)
            or len(secret.encode("utf-8")) < 32
            or len(set(secret)) < 16
            or secret == _DEVELOPMENT_SECRET
        ):
            raise RuntimeError("Production requires a strong SECRET_KEY")

    csrf.init_app(app)
    app.register_blueprint(main)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        return render_template(
            "errors/400.html",
            message="Form security check failed. Reload the page and try again.",
        ), 400

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(503)
    def handle_request_error(error):
        return render_template(f"errors/{error.code}.html", message=error.description), error.code

    @app.errorhandler(500)
    def handle_server_error(error):
        return render_template("errors/500.html"), 500

    from . import db

    db.init_app(app)
    from . import auth

    app.register_blueprint(auth.bp)
    auth.init_app(app)
    from . import items

    app.register_blueprint(items.bp)
    from . import admin, claims

    app.register_blueprint(claims.bp)
    app.register_blueprint(admin.bp)
    return app
