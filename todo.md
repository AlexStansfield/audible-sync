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

### Known issues from the 2026-11-01 review (not yet fixed)

- [ ] A login confirmed for an `account_id` acts on the account as it was when the login started: if the account is deleted before the code is entered, the new device is stored nowhere and the login still reports `confirmed`. Re-read the account in the finisher and fail (deregistering the new device) when it is gone
- [ ] When storing a confirmed login fails (database locked, no key, account gone), the newly registered device stays on the user's Amazon device list with nothing holding it. Deregister it on that path
- [ ] A CLI `login` on the host generates its own `secrets/secret.key` when the service's key lives elsewhere (`AUDIBLE_SYNC_SECRET_KEY` in Docker), and the service then cannot read the new credentials. Warn when the CLI generates a key while the database already holds encrypted secrets
- [ ] A `200` from `/auth/register` whose body is not JSON fails the code login instead of being polled again like a `429`/`5xx`
- [ ] The CLI `login` prints its result and the code to enter (`print`, `echo=print`), which the "never `print`" rule forbids. Either route it through logging or record the CLI's user-facing output as the exception

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

To be defined

