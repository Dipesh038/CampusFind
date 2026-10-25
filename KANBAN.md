# CampusFind Kanban

Updated (UTC): 2026-10-25T06:50:27Z
**In Progress WIP limit: 2**. Current WIP: 0.

## Backlog

| Card | Provisional work | Acceptance criteria |
| --- | --- | --- |
| CF-09 | Test coverage | Defined in its stage prompt; unittest acceptance checks |
| CF-10 | Deployment preparation | Defined in its stage prompt; Ubuntu, Nginx, systemd, HTTPS, persistent SQLite |
| CF-11 | CI/CD | Defined in its stage prompt; GitHub Actions and fixed Python 3.12 |
| CF-12 | Production release (BLOCKED: infrastructure absent) | Defined in its stage prompt; infrastructure-dependent checks |

These are planning labels, not permission to begin later stages.

## To Do

No cards.

## In Progress

No cards.

## Testing

No cards.

## Done

| Card | Status | Acceptance evidence |
| --- | --- | --- |
| CF-01 | DONE | Python 3.12.15 `.venv`; three direct packages and pinned transitive requirements; `pip check` clean; four unittests passed; live `/` and `/health` returned HTTP 200; docs updated |
| CF-02 | DONE | Per-context SQLite connection; 3 tables, 5 named indexes, 2 email guards; safe repeated `init-db`; 13 tests and `pip check` passed; local database exists and is ignored |
| CF-03 | DONE | Student registration, hashed passwords, login, POST logout, CSRF, database-backed roles, create-admin CLI; `pip check`, 32 tests, and disposable live HTTP auth flow passed |
| CF-04 | DONE | Report routes, validated form, ownership/claim protections, search and pagination, escaped templates; `pip check`, 49 tests, and disposable live HTTP flows passed |
| CF-05 | DONE | Student claims, private My Claims, admin review/handover with atomic transitions; `pip check`, 71 tests including controlled concurrency/rollback, and disposable live HTTP flow passed |
| CF-06 | DONE | Responsive plain-CSS pages, role navigation, shared errors, accessible forms; `pip check`, 75 tests, and Brave visual review at 375/768/1440px, including keyboard, no-JavaScript, long-text, and handover checks passed |
| CF-07 | DONE | Existing security/transaction tests mapped; real-CSRF full journey and cross-app persistence passed; DB-aware JSON health and production checks passed; 79 tests and `pip check` passed in original and clean Python 3.12 environments |
| CF-08 | DONE | 39 intended files reviewed and committed; empty supplied GitHub repository confirmed; `main` pushed; remote SHA matched local main; branch workflow documented |

## Actual transitions and review notes

| UTC date | Card | Event | Evidence / review note |
| --- | --- | --- | --- |
| 2026-10-25 | CF-01 | Created in In Progress | Initial workspace and Git inspection found an empty repository with no commits. |
| 2026-10-25 | CF-01 | Marked BLOCKED within In Progress | `python3.12` was not found; installed `python3` is 3.14.7. Required environment and acceptance commands are NOT RUN. |
| 2026-10-25 | CF-01 | Preparation review | App source and documentation added. No test pass, browser check, commit, or deployment claimed; no rework recorded yet. |
| 2026-10-25 | CF-12 | Marked BLOCKED in Backlog | Production infrastructure is absent; no release preparation has begun. |
| 2026-10-25 | CF-01 | BLOCKED → In Progress | User installed Python 3.12 and created `.venv`; interpreter verified as Python 3.12.15. |
| 2026-10-25 | CF-01 | Install review and retry | First pip attempt failed on sandbox DNS; approved network retry installed the three direct dependencies and required packages. Ten version pins generated; `pip check` clean. |
| 2026-10-25 | CF-01 | In Progress → Testing | Four unittests passed. Initial server bind and curl checks failed under sandbox restrictions; approved local access allowed live checks. |
| 2026-10-25 | CF-01 | Testing → Done | `/` returned HTTP 200 with the homepage; `/health` returned HTTP 200 JSON `{"status":"ok"}`. Server stopped with Control-C. No code rework was required. |
| 2026-10-25 | CF-02 | Backlog → In Progress | Read CF-01 code, README, both trackers, and Git state; confirmed Python 3.12.15 and preserved pinned dependencies. |
| 2026-10-25 | CF-02 | In Progress → Testing | Added DB lifecycle, schema, init command, and isolated tests; initial `pip check` and 12-test suite passed. |
| 2026-10-25 | CF-02 | Testing → In Progress (rework) | Targeted temporary-database check found uppercase email accepted because `NOCASE` affected the initial normalization check. |
| 2026-10-25 | CF-02 | In Progress → Testing | Added binary email check and additive guards; 13 tests passed, including uppercase rejection and restrictive delete checks. |
| 2026-10-25 | CF-02 | Testing → Done | `pip check` clean; `init-db` created and then safely updated the local schema; metadata found 3 tables, 5 indexes, 2 guards; database file is ignored by Git. No database reset, commit, or push. |
| 2026-10-25 | CF-03 | Backlog → In Progress | Inspected current code, trackers, Git, Python 3.12 environment, and unchanged dependency pins before work. |
| 2026-10-25 | CF-03 | In Progress → Testing | Added auth forms, routes, helpers, CLI, shared templates, and isolated tests; initial 30-test suite passed with no failures. |
| 2026-10-25 | CF-03 | Testing → In Progress (review) | Found that a signed-in health request could open SQLite, and added a DB-free health guard/test; also added a test that password spaces remain unchanged. |
| 2026-10-25 | CF-03 | In Progress → Testing | Final `pip check` clean and all 32 tests passed; dependencies unchanged. |
| 2026-10-25 | CF-03 | Testing → Done | Brave displayed homepage and auth forms; live HTTP with real CSRF tokens passed registration, wrong-password login, correct login, and logout on a temporary database. Server stopped and test database removed. Browser credential entry remains an optional manual walkthrough. No real admin, commit, push, or deployment. |
| 2026-10-25 | CF-04 | Backlog → In Progress | Read the stage prompt, status, Kanban, README, code, tests, and Git state; preserved prior schema and dependency pins. |
| 2026-10-25 | CF-04 | In Progress → Testing | Added report form, routes, four templates, and isolated tests; initial `pip check` and 49-test suite passed. |
| 2026-10-25 | CF-04 | Testing → In Progress (review) | Found delete confirmation checked before ownership; fixed to preserve 403 for cross-user attempts. Added huge-page handling, busy-write response, and broader non-open tests; removed WTForms choice deprecation warning. |
| 2026-10-25 | CF-04 | In Progress → Testing | Final `pip check` clean and all 49 tests passed without the prior WTForms warning. |
| 2026-10-25 | CF-04 | Testing → Done | Disposable local HTTP flow with real CSRF tokens passed lost create/search/edit/close, found create/delete, and cross-user edit 403; repeated after review. Server stopped and disposable database removed. No real database test data, commit, push, or remote. |
| 2026-10-25 | CF-05 | Backlog → In Progress | Read stage prompt, trackers, README, schema, DB/auth/item source and tests, Git state, and Python 3.12 environment. Preserved schema and pinned dependencies. |
| 2026-10-25 | CF-05 | In Progress → Testing | Added student claim flow, private My Claims, admin dashboard/decisions, and isolated tests. Initial 67-test suite passed, including controlled separate-connection approval and injected rollback checks. |
| 2026-10-25 | CF-05 | Testing → In Progress (review) | Added real CSRF coverage (68 tests), then tightened competing-claim row-count checks and exact handover confirmation; added race, invalid-workflow, and escaping tests. |
| 2026-10-25 | CF-05 | In Progress → Testing | Final `pip check` clean and all 71 tests passed; installed pins still match `requirements.txt`. |
| 2026-10-25 | CF-05 | Testing → Done | Fresh disposable live HTTP flow with real CSRF tokens passed two claims, approval, competitor rejection, handover, privacy/access, and repeated transitions. Existing create-admin command ran only on disposable data; server stopped and temporary database/credentials removed. No commit, push, or remote. |
| 2026-10-25 | CF-06 | Backlog → In Progress | Read the stage prompt, trackers, README, templates, forms, routes, tests, and Git state; preserved CF-01–05 behavior, schema, and pinned dependencies. |
| 2026-10-25 | CF-06 | In Progress → Testing | Added responsive plain CSS, shared layout and role navigation, page and empty-state styling, error templates, and focused UI tests. The first full run had one assertion failure: the signed-in navigation text was missing. |
| 2026-10-25 | CF-06 | Testing → In Progress (rework) | Restored signed-in text, adjusted filter/review spacing during browser review, and added long-text wrapping. No behavioral or security assertion was weakened. |
| 2026-10-25 | CF-06 | In Progress → Testing | Final `pip check` returned no broken requirements; all 75 tests passed, including the 71 prior tests. Installed package pins still match `requirements.txt`. |
| 2026-10-25 | CF-06 | Testing → Done | Brave responsive view covered the requested pages and representative states at 375, 768, and 1440px on disposable data. Verified a visible keyboard skip-link focus, navigation/search with JavaScript disabled, long-title wrapping, inline server validation, and pending review plus handover controls. Two disposable local servers were stopped and both temporary database directories removed. No real database, commit, push, remote, CI, or deployment. |
| 2026-10-25 | CF-07 | Backlog → In Progress | Read stage prompt, trackers, README, source and tests; identified existing coverage and the integration/health gaps. Git still has no HEAD or remote. |
| 2026-10-25 | CF-07 | In Progress → Testing | Added DB-aware health, revised production/health tests, and a real-CSRF full journey on temporary SQLite. Initial 79-test suite had one new-test assertion failure: `sqlite3.Row` was compared directly to a tuple. |
| 2026-10-25 | CF-07 | Testing → In Progress (rework) | Corrected the persistence assertion without changing application rules; focused integration test passed. |
| 2026-10-25 | CF-07 | In Progress → Testing | Existing-environment `pip check` passed. Temporary Python 3.12 venv install first failed on sandbox PyPI DNS, then an approved retry installed the ten pinned packages. Fresh-environment `pip check` and all 79 tests passed; temporary venv removed. |
| 2026-10-25 | CF-07 | Testing → Done | Final original `.venv` suite passed 79 tests. Health success/failure and optional release, production configuration, privacy, route journey, reinitialization, and persistent cross-app reads are verified. No local database reset, commit, push, remote, CI, or deployment. |
| 2026-10-25 | CF-08 | Backlog → In Progress | Inspected stage prompt, source inventory, README, `.gitignore`, Git state, and existing board. No commits or remote; existing GitHub CLI login is invalid. |
| 2026-10-25 | CF-08 | Kanban evidence review | The recorded CF-01 Python 3.12 blocker kept its card out of Done until the required interpreter, install, tests, and live checks passed. This prevented preparation alone from being reported as an accepted stage. CF-02's recorded email-normalization rework likewise moved the card back from Testing before its corrected checks passed. No earlier transition was reconstructed during this review. |
| 2026-10-25 | CF-08 | Staged review and rework | Explicit staging selected 39 intended files. `git diff --cached --check` found four trailing-space lines in the trackers; removed the spaces before repeating the staged check. |
| 2026-10-25 | CF-08 | In Progress → Testing | Repeated staged check passed; staged audit found 25 application files, 9 tests, 5 root files, no unexpected paths or credential-shaped content; 79 tests passed. |
| 2026-10-25 | CF-08 | Testing → In Progress | First honest local milestone commit `82664faf4a75ad0fda77e4174e67503dd635f34b` succeeded; immediate working tree was clean. No older stage commits were fabricated. |
| 2026-10-25 | CF-08 | In Progress → To Do (BLOCKED) | User selected an existing empty GitHub repository but its URL was not supplied. `gh auth status` reports invalid login, and no remote exists. Push and remote SHA verification remain unrun; CF-08 acceptance is incomplete. |
| 2026-10-25 | CF-08 | To Do (BLOCKED) → In Progress | User supplied the full existing repository URL. Initial sandboxed `git ls-remote` failed DNS; approved network retry returned no refs, confirming an empty destination. |
| 2026-10-25 | CF-08 | In Progress → Testing | Added the supplied URL as `origin` without replacing a remote; `git push -u origin main` succeeded despite invalid GitHub CLI login. No token was placed in the URL. |
| 2026-10-25 | CF-08 | Testing → Done | Remote `refs/heads/main` returned `9f0a97e994ba5102459eea757758eeed867a5239`, equal to local HEAD at verification. The pushed tree contained the 39 reviewed project files and no ignored runtime data. Separate final checkpoint commit/push is recorded in the terminal summary. |
