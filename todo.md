# Todo

List of tasks to complete by phases

## Milestone 1: Library Sync, Download and Decrypt

### Requirements

- Initial Sync of library from audible
- Record sync status for each book
- Download books
- Decrypt books to m4b
- Settings: Number of books to sync, destination folder
- Docker Support

### Tasks

- [x] Create Database
- [x] Create initial Book Model
- [x] Create Audible model
- [x] Book download
- [x] Settings file
- [x] Check for initial sync and get all books
- [x] Fetch library since last sync
- [x] Sync configured number of books
- [x] Decrypt book to m4b
- [x] Add DockerFile to build a container with requirements
- [x] Create directory for each download
- [x] Move decrypted file to folder
- [x] Cleanup files after finished
- [x] Audible Auth Json path
- [x] Add Docker Compose file to run container
- [x] Add github action to build image and push to repo

## Milestone 2: Download Progress, Metadata and OGA Encoding

### Requirements

- Download Progress
- Improved Metadata generation to include cover image and asin
- Fetch Cover, PDF and Annotations data
- Move settings to database
- Encode books to OGA 
- Add encoding settings

### Tasks

- [x] Add logging
- [x] Download PDF
- [x] Download Cover
- [x] Download Annotations
- [x] Generate metadata
- [x] Add metadata to m4b
- [x] Add settings for encoding bitrate
- [x] Encode books to OGA instead of M4B if configured
- [x] Name m4b/oga files based on metadata

## Milestone 3: API Service

### Requirements

- Support Multiple Audible Subscriptions
- Login to audible via website
- Convert to an API service
- API must be designed with a frontend in mind (milestone 4)
- Get library of books by API
- Get and Change Settings by API
- Monitor Download progress
- Ability to "delete" a download or force a redownload

### Tasks

- [x] Add settings table to database
- [x] Initialise settings on initial run
- [x] Convert to an API service: background scheduler, status, manual sync trigger and cancel, run history and settings endpoints, bearer token, Docker image runs the service
- [x] Monitor download progress (the run in flight is polled through `GET /api/status`)
- [x] Add support for multiple audible subscriptions: one account per marketplace login, credentials in the database, per-account library, cursor and run history, account endpoints
- [x] Login and save credentials: Amazon's device-code sign-in (a code entered on any device, the service polls until confirmed) with the browser sign-in as the fallback, through the API and a CLI `login` command; credentials stored on the account
- [x] Delete a book: `POST /api/books/{id}/delete-files` removes the files and unmonitors; `/redownload` and `/retry` queue it again
- [x] Bulk retry of parked books: `POST /api/books/retry-unavailable`, and a re-login requeues the account's licence-refused books itself
- [x] An OpenCollection request collection (`opencollection/`) for testing the API from Bruno
- [x] The `audible-cli` auth-file import removed: every account comes from a login (an imported file carried a device registration Audible no longer licenses)
- [x] Credentials and the API token encrypted at rest, the key outside `data/` (`secrets/secret.key` or `AUDIBLE_SYNC_SECRET_KEY`), so the database can be backed up without the Audible login
- [x] Set book as monitored/unmonitored to remove from sync (`PATCH /api/books/{id}`), plus a paged, searchable `GET /api/books` and a cover endpoint
- [x] Extras: `GET /api/logs`, a webhook posted after every run (`webhook-url`, with `POST /api/settings/webhook/test`), `GET /api/stats`
- [x] Review fixes (2026-11-01): the scheduler no longer runs back to back when a pass records no run (no accounts yet); a run no longer writes its refreshed credentials over a re-login made while it ran; an account added after the migration no longer inherits the migrated library's cursor; a re-login in another marketplace is refused
- [x] Second review fixes (2026-11-01): `PUT /api/settings` refuses `null` for a setting that cannot be unset (it locked the API out of its own settings); rejected credentials record the run as aborted saying to log in again, not as a failed book; a failed token write-back no longer leaves the run row `running`; a withdrawn book that is already downloaded no longer forces a full library re-read every run; a book action that loses the race with a run's claim is a 409, not a silent success
- [x] Known issues from the review (2026-11-01): `DELETE /api/accounts/{id}` is a 409 while the run is on that account, and a run skips an account removed since it began; a confirmed login whose account was deleted, or that cannot be stored, fails and deregisters the new device (API and CLI); a `200` that is not JSON no longer kills a device-code login's poll; a book refresh stores the token it renewed; the CLI warns when it holds another key than the one the stored secrets were written with; the API token is logged in full only on the start that generates it; the CLI `login`'s `print` recorded as the exception to the logging rule

### Known issues from the 2026-11-01 review (not yet fixed)

- [ ] `mark_book_downloaded`/`_failed`/`_unavailable` are not guarded on the claim, so after a stale reclaim (a book taking over `STALE_CLAIM_SECONDS` while a second process runs) the two runs can overwrite each other's outcome. Deferred: the service runs one sync at a time, so it takes a CLI run beside the service, and the fix is a claim token on every transition

## Milestone 4: Web App Frontend

A web application that exposes a UI to the API allowing you to login to audible, view your library, manage your sync settings and sync books

### Requirements

- Login to Audible Sync
- Authenticate with Audible 
- Configure settings
- View Library
- View Book Details
- Delete synced book
- Toggle Monitor of book

### Tasks

The design and spec are in [`docs/design/`](docs/design/DESIGN.md).

- [x] Design: every screen, light and dark themes, behaviour and API mapping (`docs/design/`)
- [x] Backend: an `admin` user created on the first start with a generated password logged once; password hash stored encrypted
- [x] Backend: session sign-in (`/api/auth/login`, `/logout`, `/me`) accepted beside the bearer token; rate-limited failures
- [x] Backend: change the username and password (current password required; a password change ends other sessions)
- [x] Backend: a server-side way to reset a forgotten password
- [x] Backend: add the book's row `id` to `current_run.book` in `GET /api/status` (`RunState.set_book`, `BookRef`). Today it carries only `asin` and `title`. The Dashboard run card needs the id for the cover. Book detail needs it to know the run is on this book, because the same ASIN can exist in two accounts
- [x] Backend: serve the built web app from FastAPI with an `index.html` fallback
- [x] Backend: serve under a base path behind a reverse proxy (`AUDIBLE_SYNC_BASE_PATH`), the prefix stripped or passed on
- [x] Docker and CI: a Node build stage for `web/`; lint, type-check and build it in CI
- [x] Web: scaffold `web/` (Vite, React, TypeScript), theme variables, layout and routing, API client and types from `/openapi.json`
- [x] Web: sign-in and Your sign-in
- [x] Web: Dashboard with the live run card
- [x] Web: Library (grid, table, filters, monitor toggle) and Book detail (actions, delete confirmation)
- [x] Web: Accounts with the code and browser sign-in flows (the live code sign-in from the UI still to be checked against Amazon)
- [ ] Backend: `DELETE /api/accounts/login/{login_id}` to cancel a pending login (stop a code login's polling thread under the store's lock; answer with the login as it ended, so a confirm that won the race is reported). The web app would call it when the sign-in dialog closes or switches from the code to the browser sign-in; today an abandoned code that the user had already entered still becomes an account, and entering it and then finishing a browser sign-in too adds the account twice
- [x] Web: Activity (runs, log)
- [x] Web: Settings
- [x] Design round 2: first-run dashboard, empty and no-result library, Accounts dialogs (rename, remove, sign in again, code expired, sign-in failed), book downloading and unmonitored, phone layout, common patterns, 404, service unreachable, forgot password
- [x] Backend: `POST /api/books/bulk` (monitor, unmonitor, retry, delete files by ids or by filter; updated and skipped counts)
- [x] Web: Library bulk selection (grid and table), action bar, delete confirmation, result message
- [ ] Backend: count the unavailable books Retry unavailable can requeue (licence refusals, not titles Audible withdrew) in `GET /api/stats`, so the Dashboard and the Library stop offering a retry that only says "Nothing to retry"; and monitored counts per status, so the Library's chips match a `monitored` filter
- [x] Backend: API token last four and regenerate; `GET /api/system` (versions, disk space, key source, database size)
- [x] Web: Settings API access and System sections; Dashboard Up next card (Up next done with the Dashboard)
- [x] Backend: per-book run records (`sync_run_books`, `GET /api/sync/runs/{id}/books`)
- [x] Backend: `GET /api/queue` for the Dashboard's Up next card
- [x] Web: the expandable run row in Activity (built with Activity, since `GET /api/sync/runs/{id}/books` exists)

