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

## Continuous integration

GitHub Actions runs the CI workflow for pull requests targeting `main` and pushes to `main`. The Ubuntu test job uses Python 3.12, installs the pinned `requirements.txt`, runs `pip check`, and runs the unittest suite with isolated temporary databases. A package job runs only after a successful test job on a push to `main`. It checks out the same tested commit, builds a source archive from an explicit allowlist, verifies its contents and `RELEASE_SHA` metadata, then uploads a seven-day artifact named with the commit SHA. The app needs no npm or frontend build step.

Open the repository's **Actions** tab to inspect a failed job and its step logs. For a successful `main` run, open that run's **Artifacts** section to download the `campusfind-<commit-sha>` artifact; it contains a `.tar.gz` source archive. On pushes to `main`, the deployment job waits for both jobs, downloads that run's commit-named artifact, verifies it against the checked-out commit, and deploys to the existing PythonAnywhere application. Pull requests run tests only; package and deploy are skipped.

## PythonAnywhere production and continuous deployment

The selected managed host is [CampusFind on PythonAnywhere](https://zainmalik.pythonanywhere.com). The earlier Ubuntu/systemd/Nginx/SSH plan is abandoned; its two unchanged, untracked templates were removed after verifying their original hashes. The board preserves that preparation history. PythonAnywhere manages HTTPS and the WSGI web worker; no public Flask development server, standalone Gunicorn service, custom domain, or public deployment webhook is used.

| Location | Role |
| --- | --- |
| `/home/ZainMalik/CampusFind` | Initial source retained intact for recovery; existing Python 3.12 `.venv` remains here |
| `/home/ZainMalik/campusfind-releases/<tested-sha>/` | Complete immutable source uploaded from a verified CI artifact |
| `/home/ZainMalik/campusfind-data/campusfind.sqlite` | Persistent SQLite database; deployments and rollbacks never copy, delete, initialize, or restore it |
| `/home/ZainMalik/.config/campusfind.env` | Protected production environment; never downloaded, uploaded, overwritten, or printed by deployment |
| `/var/www/zainmalik_pythonanywhere_com_wsgi.py` | Small activation loader; points to one complete release and loads the protected environment |
| `/home/ZainMalik/.config/campusfind-deploy/` | Previous release metadata and a recovery WSGI loader containing paths and commit ID only |

Keep the environment file owner-readable only (0600), with its parent 0700; keep the database directory private and writable by the account for SQLite journals. Check these permissions privately in the PythonAnywhere console. Do not map those directories to public static URLs. The only static mapping should be `/static/` to the active release's `campusfind/static` directory. The unchanged existing virtualenv is reused **only while `requirements.txt` stays byte-for-byte unchanged**. Dependency changes stop deployment and need a separately prepared, verified Python 3.12 virtualenv; this workflow cannot install packages through a console API.

The environment file supplies `APP_ENV`, `SECRET_KEY`, `DATABASE`, and the initial `RELEASE_SHA`. The managed WSGI loader validates production mode and the expected persistent database path. It accepts ordinary `KEY=value` lines, optional `export`, shell quoting and comments, without executing shell code or interpolating variables. Configure secrets privately on the host. For immutable releases, the loader sets the runtime `RELEASE_SHA` to verified artifact metadata; the protected file remains unchanged. The application rejects weak secrets and relative database paths, and startup asserts debug is off and Secure cookies are enabled.

### GitHub setup

Create a token in PythonAnywhere **Account → API token → Create token**. Enter it directly into GitHub **repository Settings → Secrets and variables → Actions → New repository secret**. Never paste it into chat, an editor, source, screenshots, logs, command arguments, or trackers.

| Kind | Name |
| --- | --- |
| Repository Actions secret | `PYTHONANYWHERE_API_TOKEN` |
| Repository Actions variable | `PYTHONANYWHERE_USERNAME` |
| Repository Actions variable | `PYTHONANYWHERE_HOST` |
| Repository Actions variable | `PYTHONANYWHERE_DOMAIN` |
| Repository Actions variable | `APP_URL` |

Set the non-secret variables to the selected account, official API host, domain and HTTPS URL. Values are configured in repository settings; the secret is never read back. The token grants broad account API access, so restrict repository write access and review changes to deployment code/workflows. No extra deployment action or application webhook is installed.

### Job sequence and activation

1. `test` installs the pinned dependencies on Python 3.12, runs `pip check`, then all unit/integration/release-safety tests.
2. On a push to `main`, `package` runs only after tests pass. It creates exactly 26 allowlisted source files plus `RELEASE_SHA`; runtime data, `.git`, tests, `.venv`, caches and secrets are excluded.
3. `deploy` requires both successful jobs and runs only on pushes to `main`. A shared production concurrency group with cancellation disabled prevents overlapping deployment jobs. Official GitHub actions are pinned to full release SHAs; token permissions stay `contents: read`.
4. The job checks out the exact workflow SHA with Git history and downloads only this run's matching artifact. `deploy/pythonanywhere.py` rejects unexpected paths, duplicates, links, extra members, wrong metadata, or source bytes differing from `git show <tested-sha>:<path>`.
5. Before writing source, it requires healthy production reporting a previous SHA, Python 3.12, the expected virtualenv, Force HTTPS, and the expected static mapping. If Force HTTPS is disabled, it enables that setting, reloads and verifies the existing release before changing source. It verifies all previous source bytes against the reported Git commit and unchanged requirements. An unknown manual production modification stops deployment for review.
6. All 27 files upload to a new SHA-specific directory and are downloaded back for byte verification. The previous source remains untouched. Only after this completes are the secret-free recovery loader/metadata saved, the WSGI loader replaced, the configured source/static paths updated, and the existing web app reloaded.
7. Bounded HTTPS health checks require HTTP 200 JSON with `status=ok` and `release` equal to the tested SHA. An upload, reload, API, or health failure exits nonzero. API errors expose only status codes; response bodies and authorization headers are never logged. Calls are spaced below the documented rate limit, with per-request timeouts and an overall job timeout.
8. On activation/health failure, the job restores the exact previous WSGI loader held in memory, previous source/static configuration, reloads, checks the previous release's health, and still fails the job. Staging failures leave the active source alone. Runner termination, job timeout, network loss, or a failed recovery can require manual recovery; do not assume success from a requested reload.

The [official PythonAnywhere API](https://help.pythonanywhere.com/pages/API/) documents multipart file uploads (`POST`, 200/201 success), file reads, web-app configuration and static mapping updates, and reload. The [official deployment example](https://help.pythonanywhere.com/pages/IntegratingWithPythonAnywhere/) describes uploading source, WSGI and static routes before reloading. These operations support the managed-host design without relying on SSH or remote console execution. Their actual availability on this free account is confirmed only by successful authenticated operations, not by a reload request alone.

### Manual deployment and recovery

For deployment outside CD, choose a successful main Actions run and its exact SHA. Download its matching artifact, never copy the working tree or whichever main happens to be newest. If expired, obtain a new successful package for the intended commit. Verify the exact 27 regular-file allowlist, `RELEASE_SHA`, and each source byte against that commit before upload. Use PythonAnywhere Files to upload the source into a new SHA-specific directory. Preserve the initial tree, old release directories, virtualenv, persistent database and protected environment. Check unchanged requirements, then point the WSGI loader/source/static mapping at the complete release and click **Reload** in the Web tab. Verify health, homepage and the known fictional report; never initialize or replace the production database as part of code deployment.

After a CD activation failure, inspect the failed GitHub Actions deployment step first. Do not paste raw environment files or production logs into issues. If automatic recovery is unconfirmed:

1. In PythonAnywhere Files, inspect `.config/campusfind-deploy/previous-release.json` for the previous SHA and retained source/static paths. The file contains no credentials.
2. Copy the **contents** of `.config/campusfind-deploy/previous-wsgi.py` into `/var/www/zainmalik_pythonanywhere_com_wsgi.py` using the host's editor. It contains no secret values and reads the existing protected environment file. Do not edit that environment file or database.
3. In Web, restore the source directory and `/static/` mapping recorded in the recovery metadata. Keep the existing Python 3.12 virtualenv selected and Force HTTPS enabled. Click **Reload**.
4. Confirm `/health` reports the previous SHA, the homepage loads over trusted HTTPS, and the known fictional report is still present. Record the failed run and recovery result. Do not delete newer real records or restore a database backup to roll back code.

If the runner stopped before recovery metadata was saved, the previous source and WSGI remain the starting point; inspect the Web settings and Actions step before making changes. Recovery does not repair schema migrations; this workflow never performs one. Old code may be incompatible with a future schema change, which needs its own release plan.

Rollback behavior is covered by simulated-host tests for failed staging, failed new health, restored old health and failed recovery. A successful first deployment alone does **not** prove live automatic rollback; the project tracker records whether an actual live failure/recovery was exercised.

Free PythonAnywhere web apps [expire after one month](https://help.pythonanywhere.com/pages/FreeAccountsFeatures/). Log in before expiry and click **Run until 1 month from today** on the Web tab; automatic CI reloads do not replace this manual renewal. Check the displayed expiry date regularly and manage disk quota before retaining many releases. Never remove the active release, the previous recovery release, the shared virtualenv or persistent data during cleanup.

### Production acceptance

Record HTTP 200 for the HTTPS homepage and JSON health, its exact release SHA, Force HTTPS redirect and host setting, debug-off startup, Secure/HttpOnly/SameSite cookie flags, fictional registration/login/report creation, and the **same report** surviving both a PythonAnywhere Reload and a subsequent automatic deployment. Inspect file permissions and static mappings privately. Report secret/database exclusion from Git separately from runtime checks. Do not mark CF-10 complete until these live checks pass, or CF-11 complete until a real main push automatically deploys its exact tested SHA.

Next stage: **CF-12**, only after a separate stage prompt. Do not start it automatically.

CF-11 acceptance evidence (2026-10-25): [deployment PR #3](https://github.com/Dipesh038/CampusFind/pull/3) and [main run 40191731669](https://github.com/Dipesh038/CampusFind/actions/runs/40191731669) passed test → package → deploy for `fd13999e8a7319ac49be320907a7a064f8841083`. [Independent read-only verification](https://github.com/Dipesh038/CampusFind/actions/runs/40192225863) confirmed the exact live SHA, the new homepage footer and the original fictional report after deployment. [Negative-test PR #4](https://github.com/Dipesh038/CampusFind/pull/4) produced the expected failed test and skipped package/deploy, was restored, and was closed unmerged. HTTPS redirect, registration/login, secure cookies and Reload persistence were verified. Live automatic rollback was not deliberately exercised; recovery testing is simulated. Subsequent reviewed main commits produce their own tested release/artifact/health SHA.
