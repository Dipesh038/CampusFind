"""SQLite connection lifecycle and non-destructive schema initialization."""

import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db():
    """Reuse one connection within the current request or app context."""
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE"], timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        g.db = connection
    return g.db


def close_db(error=None):
    """Close a connection when its application context ends."""
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    """Create absent schema objects, retaining existing tables and rows."""
    database_path = Path(current_app.config["DATABASE"])
    if database_path.parent == Path(current_app.instance_path):
        database_path.parent.mkdir(parents=True, exist_ok=True)

    with current_app.open_resource("schema.sql") as resource:
        schema = resource.read().decode("utf-8")

    connection = get_db()
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + schema + "\nCOMMIT;")
    except sqlite3.Error:
        connection.rollback()
        raise


def init_app(app):
    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    def init_db_command():
        """Create missing tables and indexes without clearing data."""
        init_db()
        print("Database schema initialized; existing data preserved.")
