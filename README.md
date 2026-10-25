# CampusFind

CampusFind is a small Flask app for a university lost and found project. It provides student accounts, lost and found reports, private ownership claims, administrator review, and a responsive server-rendered interface.

## Mac setup (Python 3.12)

On Apple Silicon, install the required Python version with `brew install python@3.12`. Check it with `python3.12 --version`. Do not use another Python minor version for this project.

From this folder:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m flask --app campusfind init-db
.venv/bin/python -m flask --app campusfind run --debug
```

`requirements.txt` pins the packages resolved in a clean Python 3.12 environment. `--debug` is for local development only. Open `http://127.0.0.1:5000/` and `http://127.0.0.1:5000/health`, or use `curl` for each URL. Press **Control-C** in the server terminal to stop it.

In VS Code, open the Command Palette with **Shift-Command-P**, run **Python: Select Interpreter**, and choose `.venv/bin/python`. If it is not listed, choose **Enter interpreter path** and select that file. No editor configuration file is required.

`APP_ENV` defaults to `development`; `DATABASE` defaults to the ignored `instance/campusfind.sqlite` file. Run `init-db` before using account, report, or claim pages. It creates the local `instance` directory and missing tables, indexes, and email normalization guards. Running it again preserves existing rows. It does **not** migrate a database whose tables already have an older shape; schema changes need a separately planned migration. Never use a real local or production database for automated tests: the tests create temporary database files.

Set `APP_ENV=production`, a strong `SECRET_KEY` through the process environment, and an **absolute** `DATABASE` path in persistent storage for production. Create that storage directory with suitable permissions before running `init-db`. A production secret needs at least 32 UTF-8 bytes and 16 distinct characters; the application rejects missing or weak values and debug mode. The development default secret must never be used in production.

## Accounts and administrator setup

Visit `/auth/register` to create a student account, then `/auth/login` to sign in. Registration trims names, lowercases and trims email addresses, requires a 12–128 character password, and stores a Werkzeug password hash. Passwords are never trimmed. The simple email format check does **not** verify that the person owns the address. Duplicate addresses show a form error. Logout is a CSRF-protected **POST** form in the navigation; a GET request cannot log someone out. Missing or invalid CSRF tokens return HTTP 400 with a generic message.

After `init-db`, create an administrator only when needed with:

```sh
.venv/bin/python -m flask --app campusfind create-admin
```

The command prompts for a name and email, then a hidden password with confirmation. It applies the registration checks, rejects an existing email, and does not promote an existing account. No administrator or default credentials are created during setup. For production, first configure `APP_ENV`, `SECRET_KEY`, and the persistent absolute `DATABASE` path in the service environment; do not put secret values in source files or shell history.

Successful login replaces the old session and stores the user ID as the identity reference. Each account request reads the current user and role from SQLite. Sessions last up to two hours. Cookies use HttpOnly and SameSite=Lax, with Secure enabled in production HTTPS. Login always redirects home and ignores client-supplied next URLs.

## Health and tests

GET `/health` is public JSON. It runs `SELECT 1` through the normal per-context SQLite connection and returns `{"status":"ok"}` with HTTP 200 when that query succeeds. Set optional `RELEASE_SHA` in the process environment to add a `release` field; local development leaves it unset. A connection or query failure returns only `{"status":"unavailable"}` with HTTP 503, including when a browser session is present. The response does not reveal database paths, SQL errors, or secrets. `SELECT 1` checks connectivity, **not** whether the users, items, or claims tables exist. It does not initialize the schema; run `init-db` separately.

Run `.venv/bin/python -m pip check` and `.venv/bin/python -m unittest discover -s tests -v` from the project folder. The unittest suite uses temporary SQLite databases and Flask test clients. It covers schema constraints and reinitialization; registration, roles and production configuration; report and claim ownership, validation, privacy and CSRF; atomic approval, rollback and competing approvals; a complete found-item handover and lost-report closure journey; persistence across app instances; and JSON health success and failure. Tests need no running server or internet connection.

Current limitations: email ownership is not verified, and there is no password recovery, photo upload, or automated matching. Keep ownership evidence private and use administrator review for claim decisions.

For an optional browser check, register a disposable student, try a wrong password, then the correct password, and use the navigation's Log out button. Use a temporary `DATABASE` path and run `init-db` against that same path if you do not want the test account in the local database. Stop the development server with **Control-C**.

## Reports

Anyone can browse `/items` and view a report at `/items/<id>`. Search uses `q` across title, description, and campus location; `category`, `type`, and `status` filters can be combined. Results show 12 reports per page, newest first. Search treats `%`, `_`, and backslashes as literal text. Invalid filters or page values return HTTP 400; a valid page beyond the last is empty. The Browse Reports page has a clear-filters link.

Sign in to use **New Report** or **My Reports**. The form accepts lost or found reports, a fixed category, a campus location, a description, and a real event date no later than the current date in **Asia/Kolkata**. It trims text and checks lengths on the server. The reporter, open status, and UTC timestamps come from the server. After a successful create, edit, delete, or close, the app redirects to a GET page.

The reporter can edit or delete an open report only while it has **no claims**, including rejected claims. Deletion requires checking the confirmation box on the report page. The reporter can close an open **lost** report; found reports cannot be closed manually. Ownership and eligibility are checked again inside a SQLite write transaction before any change, so a claim added in between cannot be bypassed. Conflicts return an explanatory HTTP 409; a temporarily busy database returns HTTP 503. All state-changing report actions use CSRF-protected POST requests. Public pages do not show account email or private claim evidence.

## Ownership claims and review

An authenticated **student** can submit 20–2000 characters of ownership evidence from an open **found** report that someone else posted. The form trims the evidence. A student can submit only one claim per report, even if a previous claim was rejected. Claims on lost or non-open reports, self-claims, and administrator submissions are refused. Submission uses a SQLite write transaction and rechecks the report and duplicate rule under the lock. The student's identity, pending status, and UTC timestamp come from the server.

**My Claims** (`/claims/mine`) shows only the signed-in student's claims, their own evidence, status, submission date, and any decision explanation. Public report pages and the report author cannot see claim evidence or notes. Administrators can review private evidence on `/admin`; other students cannot open that dashboard. Text is escaped in all templates.

The administrator dashboard shows report and claim counts, paginated pending claims, and paginated claimed found items awaiting handover. An administrator must write a **3–500 character** explanation to approve or reject a claim. This explanation becomes visible to that claimant, so it should not include another person's private information. Approval atomically approves one pending claim, rejects competing pending claims with a generic explanation, and marks the found report **claimed**. Rejection leaves the report **open**. After a confirmed handover, an administrator checks the confirmation box to mark a claimed found report **returned**; its approved claim remains approved. Repeated or invalid decisions return HTTP 409 without changing state. All decisions and handover actions are CSRF-protected POST requests and successful actions redirect to GET.

## Database and workflow rules

Every database connection enables SQLite foreign keys, has a five-second busy timeout, and is closed with its Flask application context. Application SQL must pass data values as parameters. The schema enforces these rules now:

| Database constraint | Effect |
| --- | --- |
| Required fields and `CHECK` constraints | Reject empty required text, invalid roles, report types, item statuses, and claim statuses; require a review time for reviewed claims |
| Normalized unique email | A binary comparison and additive guards require lowercase, trimmed values; reject duplicates (SQLite's built-in lowercase handling is ASCII-oriented) |
| Foreign keys with `ON DELETE RESTRICT` | Prevent deleting users or items still referenced by reports or claims |
| `UNIQUE(item_id, claimant_id)` | Prevent the same user from claiming an item twice |
| Partial unique index on approved claims | Permit at most one approved claim per item |

The following rules need authorization checks and transactions; the schema alone does not enforce them:

- CF-05 enforces claims on **open found** reports only, with no self-claims or repeat submissions.
- CF-05 approval records review times, resolves competing pending claims, and changes the item to **claimed** in one transaction.
- CF-05 handover requires exactly one approved claim and explicit confirmation before changing the item to **returned**.
- CF-04 lets a reporter close an open lost report as **closed**.
- Reporters may edit or delete only their own **open** reports that have no claims. CF-04 enforces this inside write transactions. Public user deletion is outside scope.

Claim `proof_text` and decision notes are private and never appear in public report views.

## Interface and local visual review

The interface uses the local `campusfind/static/style.css` file, system fonts, and server-rendered pages. It does not load JavaScript or external design assets. Navigation reflects the current database-backed role: visitors see Browse Reports, Login, and Register; students see report and claim actions; administrators see the dashboard. Logout remains a POST form. Each page has a skip link, visible keyboard focus, written status labels, and a shared layout. Form errors appear beside the relevant fields. The 400, 403, 404, 409, 500, and 503 pages use the same layout; `/health` remains JSON.

For a local visual check, use a **disposable** SQLite path and fictional accounts. From the project folder:

```sh
CF_VISUAL_DIR=$(mktemp -d)
DATABASE="$CF_VISUAL_DIR/live.sqlite" .venv/bin/python -m flask --app campusfind init-db
DATABASE="$CF_VISUAL_DIR/live.sqlite" .venv/bin/python -m flask --app campusfind run --port 5056
```

Create fictional student and administrator accounts using registration and `create-admin` with the same `DATABASE`; add fictional reports and a claim through the normal forms. Stop the server with **Control-C** before removing only the temporary directory named by `CF_VISUAL_DIR`. Do not put a real account or private claim proof into screenshots.

In a browser's responsive view, check **375px, 768px, and 1440px** on the homepage, Browse Reports with and without results, report detail, create/edit form with an invalid value, Login, Register, My Reports, My Claims, the administrator dashboard, and representative error pages. Check that the page does not scroll horizontally, text and form controls remain readable, navigation wraps, and confirm actions are clear. Use Tab to reveal the skip link and focus outline. Temporarily disable JavaScript in the browser, reload, and verify navigation and search still work; restore the browser setting afterward.

## Branch workflow

Start from the current `main` branch. For a focused change, create a branch with `git checkout -b <name>`, make the change, and run the relevant tests. Stage the intended files, commit, and push that branch. Open a pull request for review. After CI is introduced in CF-09, merge only after its checks pass. Do not put local databases, secrets, caches, or `.venv` in a commit.
