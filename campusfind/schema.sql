-- CF-02: additive initialization only. This file contains no resets or migrations.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    email TEXT NOT NULL COLLATE NOCASE UNIQUE
        CHECK (length(email) > 0 AND email COLLATE BINARY = lower(trim(email))),
    password_hash TEXT NOT NULL CHECK (length(password_hash) > 0),
    role TEXT NOT NULL DEFAULT 'student'
        CHECK (role IN ('student', 'admin')),
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    reporter_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    report_type TEXT NOT NULL CHECK (report_type IN ('lost', 'found')),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    category TEXT NOT NULL CHECK (length(trim(category)) > 0),
    location TEXT NOT NULL CHECK (length(trim(location)) > 0),
    event_date TEXT NOT NULL
        CHECK (event_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'claimed', 'returned', 'closed')),
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL
        REFERENCES items(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    claimant_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    proof_text TEXT NOT NULL CHECK (length(trim(proof_text)) > 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    admin_note TEXT,
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    reviewed_at TEXT,
    UNIQUE (item_id, claimant_id),
    CHECK (
        (status = 'pending' AND reviewed_at IS NULL)
        OR (status IN ('approved', 'rejected') AND reviewed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_items_reporter_id ON items(reporter_id);
CREATE INDEX IF NOT EXISTS idx_items_public
    ON items(report_type, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_claims_claimant_id ON claims(claimant_id);
CREATE INDEX IF NOT EXISTS idx_claims_status_created_at
    ON claims(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_claims_one_approved_per_item
    ON claims(item_id) WHERE status = 'approved';

-- Additive guards also protect databases initialized before the binary CHECK fix.
CREATE TRIGGER IF NOT EXISTS trg_users_email_normalized_insert
BEFORE INSERT ON users
FOR EACH ROW
WHEN NEW.email COLLATE BINARY <> lower(trim(NEW.email))
BEGIN
    SELECT RAISE(ABORT, 'email must be lowercase and trimmed');
END;

CREATE TRIGGER IF NOT EXISTS trg_users_email_normalized_update
BEFORE UPDATE OF email ON users
FOR EACH ROW
WHEN NEW.email COLLATE BINARY <> lower(trim(NEW.email))
BEGIN
    SELECT RAISE(ABORT, 'email must be lowercase and trimmed');
END;
