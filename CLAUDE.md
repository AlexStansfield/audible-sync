# CLAUDE.md - AI Assistant Guide for Audible-Sync

Guidance for AI assistants working with the audible-sync codebase.

## Quick Reference

| Aspect | Details |
|--------|---------|
| **Language** | Python 3.12 (`.python-version`, `pyproject.toml`) |
| **Type** | Background service with an HTTP API, plus a one-shot CLI |
| **Entry Point** | `python -m src.service` (the service); `python -m src.main` (one sync and exit) |
| **Database** | SQLite 3 (`data/audible_sync.db`) |
| **Config** | `config/config.ini`, seeded once into the `settings` table which the app then runs on |
| **Secrets** | Credentials, API token and the web app password's hash encrypted in the database; the key in `secrets/secret.key` or `AUDIBLE_SYNC_SECRET_KEY` (`src/crypto.py`) |
| **Lines of Code** | ~4300 lines across 17 Python modules, plus ~5100 lines of tests |
| **Logging** | Python `logging`, configured in `src/main.py` |
| **Testing** | pytest (`tests/`, binary fixtures in `tests/fixtures/`), run with `uv run pytest`; unit tests required for new code |
| **Linting / Formatting** | ruff (config in `pyproject.toml`), run with `uv run ruff check .` and `uv run ruff format .` |
| **CI** | GitHub Actions: lint, format check and tests on every push/PR to `dev` and `main` |
| **Package Manager** | uv (`pyproject.toml` + `uv.lock`); `requirements.txt` is generated for pip users |
| **Web framework** | FastAPI + uvicorn (`src/api.py`, `src/service.py`); pydantic models in `src/schemas.py` |

## Project Overview

**Purpose:** Sync an Audible library, download owned audiobooks, and decrypt them to DRM-free M4B or Ogg Opus files with embedded metadata, cover art and chapters.

**Inspiration:** [BALD (Bash Audible Library Downloader)](https://github.com/damajor/BALD). This Python implementation uses the same underlying `audible` library but aims to become a user-friendly service with a web UI and automated scheduling.

**Important:** This app does NOT crack DRM. It only decrypts audiobooks the user owns.

**Current State (Milestone 2 complete, Milestone 3 in progress):**
- Incremental library sync from the Audible API
- Download and decrypt to M4B (stream copy) or re-encode to Ogg Opus (`.oga`) at a configurable bitrate, with metadata, cover art and chapters embedded in either format
- Companion PDF, high-res cover and annotations downloaded alongside the book
- Path-safe file naming from configurable templates, and per-book error handling (a failing book is skipped, not fatal)
- Settings in a database table, seeded once from `config.ini`, changed at runtime through the API
- Accounts: one per Audible marketplace login, credentials stored in the database **encrypted**, each with its own library rows, sync cursor and run history; the pipeline takes them in turn. The key lives outside `data/` (`secrets/secret.key`, or `AUDIBLE_SYNC_SECRET_KEY`), so a backup of the database is not a working Audible device
- Login the way the Audible apps do: Amazon's device-code flow - a five-letter code the user enters at `amazon.{domain}/code` on any device while the service polls for the registration - with the browser sign-in (PKCE URL, pasted "page not found" address) as the fallback; through the API or a CLI `login` command, no password through the app
- Book management: a paged, searchable library listing, monitor/unmonitor, delete the files, redownload, retry, refresh from Audible, and a cover endpoint
- The extras: recent log lines over the API, a webhook after every run, and a stats endpoint for a dashboard
- A background service: a scheduler runs the pipeline every `sync_interval_minutes`, and a bearer-token API reports the run in flight, starts or cancels a run, lists the run history and reads/changes the settings. The Docker image runs the service
- Docker image built by GitHub Actions on version tags

**Milestone 3 is complete** (2026-09-12). Milestone 4 - the web app on top of this API - is next; `todo.md` has the roadmap.

## Codebase Structure

```
audible-sync/
├── .github/workflows/
│   ├── ci.yaml               # ruff check, ruff format --check, pytest on push/PR
│   └── docker-publish.yaml   # Build & push image to ghcr.io on v* tags, create release
├── config/
│   └── config.ini            # Application configuration
├── data/
│   ├── audible_sync.db       # SQLite database (created at runtime, gitignored)
│   └── downloads/            # Temporary per-book working folders
├── src/
│   ├── main.py               # CLI entry point: `run` (default) and `login` commands, run_pipeline(), logging setup
│   ├── service.py            # Service entry point: env config, API token, wires scheduler + API, uvicorn
│   ├── api.py                # create_app(): FastAPI routes, bearer auth, CORS, lifespan
│   ├── schemas.py            # Pydantic request/response models - the API contract
│   ├── scheduler.py          # Background thread: interval runs, trigger, cancel, wake
│   ├── runstate.py           # RunState snapshot of the run in flight + StateProgress
│   ├── accounts.py           # Authenticator <-> accounts.auth, adding and re-logging accounts
│   ├── crypto.py             # Encryption at rest of the stored secrets; the key from the env or secrets/secret.key
│   ├── apitoken.py           # The bearer token: env or stored, the ApiToken holder, regenerating it in place
│   ├── system.py             # GET /api/system's probes: FFmpeg and libopus, the library disk, the database size
│   ├── webauth.py            # The web app's sign-in: the one user, scrypt password hashes, sessions, the failed-sign-in throttle
│   ├── audible_login.py      # Device-code login (code pair, polling thread) and the browser fallback; the PendingLogins store
│   ├── library.py            # Per-book actions: find/delete the files, delete/redownload/retry/refresh transitions, bulk, the next run's queue
│   ├── logbuffer.py          # Ring-buffer log handler behind GET /api/logs
│   ├── notify.py             # The run-finished webhook
│   ├── settings.py           # Frozen Settings dataclass, from_ini/from_db, seeding, validation
│   ├── paths.py              # REPO_ROOT and resolve_path: every path is anchored here
│   ├── model.py              # Book, Account and SyncRun data models
│   ├── database.py           # SQLite schema, migrations, queries (library + sync_runs)
│   ├── audible_client.py     # Audible API client and response mapping
│   ├── sync.py               # Incremental library sync and the run cursor
│   ├── progress.py           # Where download byte progress is reported (tqdm lives here)
│   ├── naming.py             # Sanitizer and folder/filename templates ([naming] in config.ini)
│   ├── encoding.py           # Output formats, Opus picture block and chapter tags ([encoding] in config.ini)
│   └── downloader.py         # Download, accessories, metadata, decryption, filing
├── tests/
│   ├── test_database.py      # Schema, migrations (incl. the accounts rebuild), queries against a temp DB
│   ├── test_accounts.py      # Authenticator round trip, auth persistence, adding and replacing credentials
│   ├── test_crypto.py        # Round trip, the prefix, plaintext passthrough, the wrong key, key loading and generation
│   ├── test_webauth.py       # Hashing, the first-start user, verify, changes and reset, session lifetimes, the throttle
│   ├── test_audible_login.py # Code pair, polling, credential mapping, PKCE URL, the store for both methods, the terminal flows
│   ├── test_library.py       # Legacy audio path lookup, file deletion and pruning, the transitions, refresh, bulk, the queue
│   ├── test_logbuffer.py     # Capacity, ordering, level floor, tracebacks
│   ├── test_notify.py        # Payload shape, the POST, never raising
│   ├── test_downloader.py    # Sanitizer, metadata, FFMETADATA writer, per-book error handling
│   ├── test_naming.py        # Sanitizer, templates, optional groups, default layout, validation
│   ├── test_encoding.py      # Format validation, JPEG/PNG header parsing, picture block, chapter tags, M4B freeform tags, ASIN readback
│   ├── test_audible_client.py # Field mapping, series order, missing keys, per-item skip, pagination
│   ├── test_model.py         # Book.from_row, legacy columns, the primary-series rule
│   ├── test_settings.py      # Config parsing, defaults, path anchoring, validation
│   ├── test_paths.py         # Repo-root anchoring, absolute paths, ~ expansion
│   ├── test_main.py          # Log level, run_pipeline wiring, run recording, cancel outcome, main ordering
│   ├── test_service.py       # Env config, build() wiring
│   ├── test_apitoken.py      # Token resolution, logging only once, regenerating, the env refusal
│   ├── test_system.py        # FFmpeg probes with fake runners, the low-space rule, the disk and database sizes
│   ├── test_api.py           # Every endpoint via TestClient with a fake scheduler, auth, CORS, lifespan
│   ├── test_scheduler.py     # Due-time arithmetic and the thread: trigger, cancel, stop, failing runs
│   ├── test_runstate.py      # The snapshot lifecycle and StateProgress
│   ├── test_sync.py          # Incremental cursor, its fallbacks, the availability refresh pass
│   ├── test_progress.py      # NullProgress and the tqdm adapter's lifecycle
│   └── fixtures/silence.m4b  # 900-byte silent AAC M4B for tag-writing tests
├── opencollection/           # The API as a request collection (OpenCollection YAML) for Bruno and the like; not in the image
├── secrets/                  # secret.key, generated on the first start (gitignored, its own mount)
├── compose.yml
├── Dockerfile
├── pyproject.toml / uv.lock  # Direct dependencies, dev group, ruff and pytest config
├── requirements.txt          # Generated: uv export --no-dev --no-hashes --no-emit-project -o requirements.txt
├── README.md
└── todo.md                   # Milestone roadmap
```

## Architecture

```
service.py (entry point)
  ├─→ api.py        (FastAPI routes)     ─→ schemas.py
  ├─→ scheduler.py  (interval thread)    ─→ runstate.py
  └─→ main.run_pipeline  ◄── also the CLI (main.main)
        ├─→ settings.py   (config, validation)  ─→ database.py ─→ paths.py
        ├─→ audible_client.py (API integration)
        ├─→ sync.py       (library sync, cursor)
        └─→ downloader.py (download, metadata, decrypt, file)
              ├─→ naming.py   (paths)
              ├─→ encoding.py (format, Opus tags)
              ├─→ progress.py (byte progress reporting)
              └─→ runstate.py (stage, book, queue position)
```

**Data flow:**
```
Audible API → audible_client.py → sync.py → database.py → SQLite
                                          ↓
                                    downloader.py → FFmpeg → M4B or OGA + PDF/cover/annotations
```

- No `__init__.py` files; flat `src/` directory run as modules (`python -m src.main`)
- Absolute imports: `from src.module import ...`
- Config and DB paths are anchored to the repo root by `src/paths.py`, so the app can be started from any working directory. Never build a path from a relative string; put it through `resolve_path`
- `settings.py` must never import `downloader.py`: the downloader imports `Settings`, so the reverse edge is a circular import. This is why `validate_max_download` lives in `settings.py`
- `crypto.py` is a leaf (it imports only `paths.py`): `database.py` imports it to encrypt and decrypt, and the entry points configure it. It must never import `database.py`, which is why the migration of plaintext rows (`encrypt_stored_secrets`) lives in `database.py` and the entry points call the two in turn
- `database.py` must never import `settings.py`/`accounts.py`: settings imports database. The accounts migration therefore creates a credential-less placeholder that a login with its `account_id` fills in

## Key Modules

### main.py

Three functions, no module-level work:

- `configure_logging(debug)` sets the root logger from `settings.debug`. Called from the entry point, not at import: configuring logging on import would also reconfigure any host process that imports this module, which the Milestone 3 service will do
- `run_pipeline(settings, progress=None, *, state=None, cancel=None)` creates the folders, initialises the DB, then takes every account from `get_accounts()` in turn through `run_account`. Each account is **read again at its turn** (`get_account`), so one deleted, disabled or logged out while an earlier account ran is skipped; a disabled account or one with no credentials (`needs_login`) is skipped with a log line; no accounts at all is a warning and a return. **One account failing does not stop the others**: its run is recorded, the loop moves on, and the first error is raised again once every account has had its turn, so a one-shot CLI run still exits non-zero. `cancel` is also checked between accounts
- `run_account(account, settings, progress=None, *, state=None, cancel=None)` is one account's pass (an `aborted` run - the circuit breaker - is one account's; the others still get their turn): opens that account's `sync_runs` row, builds `Audible(authenticator_for(account))`, runs `sync_library(audible, account, auto_monitor_new=settings.auto_monitor_new)`, `mark_account_synced`, then `download_books(..., account_id=account.id, ...)`. It **owns the run record**: the row is opened before Audible is touched and closed on every exit, including the exception arm, which re-raises after recording. `SUCCESS` when nothing failed, `PARTIAL` when a book failed *or* an exception arrived after the sync had completed, `FAILED` only when the sync itself raised - the `synced` flag is what separates the last two - and `CANCELLED` when `download_books` reports it stopped early. `SUCCESS`/`PARTIAL`/`CANCELLED` become the account's next cursor. Credentials the run refreshed are written back in a `finally` (`persist_auth_if_changed`), whatever happened; a write-back that raises is logged, not raised, so it cannot skip closing the row. `ABORTED` also records a run whose credentials Audible rejected mid-download (`DownloadStats.credentials_error`), with `error` saying to log in again - not as a failed book. `state` (a `RunState`, see `runstate.py`) gets `begin` and `set_account` first and `end` in a `finally` *after* the row is closed, so a poll never sees "nothing running" beside a history row still in flight; `cancel` is a `threading.Event` only the download half honours
- `main(argv=None)` is the CLI entry point, an `argparse` parser with three commands. `run` (the default), `login` and `reset-password` all go through `_prepare()` first: `init_db()`, `seed_settings_from_ini()`, `Settings.from_db()`, `configure_logging`, `crypto.configure(load_secret_key())` + `encrypt_stored_secrets()`. The database comes first because the settings live in it; the config file is copied in once. `run_command` then calls `run_pipeline` with a `TqdmProgress()`; `login_command(marketplace, *, name, monitor_existing, account_id=None, browser=False)` calls `audible_login.login_with_code` (or `login_interactively` with `--browser`) and then `add_account_from_authenticator`, or `replace_account_auth` with `--account`; a `LoginFailed` is a `SystemExit` with the reason, and so is a confirmed login that cannot be stored - after `discard_device` has deregistered the device Amazon just registered. `reset_password_command(*, reset_username=False)` is the way back from a forgotten web app password (there is no email). It **refuses** (`SystemExit`) when `webauth.password_readable()` is false - the CLI holds another key than the service, so the new password would be unreadable to it and the reset, having ended every session, a lockout. Otherwise `webauth.reset_password`, which generates a new password, ends every session and with `--username` sets the name back to `admin`, then prints the password - `print` for the same reason as `login`. The service needs no restart, since it reads the password and sessions from the database. `_prepare` ends with `_warn_about_another_key`: a WARNING when `count_unreadable_secrets()` is non-zero, i.e. this process holds another key than the one the stored secrets were written with (a CLI on the host generating its own `secrets/secret.key` beside a service in Docker), because a login stored then is unreadable to the service. A warning, not a refusal: after a key that is really lost, logging in again is the way back. Tests call `main([])`/`main(["login", ...])` - `parse_args(None)` would read pytest's own argv Settings are read (and therefore validated) before logging or any folder exists, so a bad template or bitrate fails before anything is created. The progress bar is injected here for the same reason logging is configured here: a host process that imports this module gets neither by surprise

This is a one-shot run: sync + download, then exit.

### settings.py

`Settings` is a frozen (`slots=True`) dataclass holding every configured value: `debug`, `sync_enabled`, `sync_interval_minutes`, `max_download`, `max_attempts`, `max_consecutive_failures`, `auto_monitor_new`, `download_folder`, `audiobook_folder`, `folder_template`, `filename_template`, `encoding_format`, `bitrate`, `webhook_url`. It is the only thing that reads configuration, so the CLI, the API and the scheduler all work from the same validated object rather than loose keyword arguments.

**Two sources, one order.** `config.ini` is read by `from_ini` and is where a fresh installation's values come from; the `settings` table is read by `from_db` and is what actually runs. `seed_settings_from_ini()` copies the file into the table **once** (only when `has_settings()` is false; a missing file is not an error there, the defaults apply), so after the first run the file is documentation and the table is the truth, changed at runtime through the API. Edits to the file after that are deliberately not picked up.

- `__post_init__` runs `validate_templates`, `validate_encoding`, `validate_max_download`, `validate_max_attempts`, `validate_max_consecutive_failures` (≥ 1 when set; `None` means never give up, read from an empty ini value by `_optional_int`), `validate_sync_interval` (≥ `MIN_SYNC_INTERVAL_MINUTES`, 5) and `validate_webhook_url` (http/https), so **no** route in - `from_ini`, `from_db`, `with_changes`, `dataclasses.replace`, a direct call - can produce settings that would fail part way through a run. It only validates; normalisation belongs to the builders, which keeps the field types honest
- `Settings.from_ini(path=DEFAULT_CONFIG_FILE)` reads the INI, passes every configured path through `resolve_path` and falls back to the documented default for each key. It raises `FileNotFoundError` on a missing file, because `configparser.read` ignores one and a mistyped path would otherwise run silently on defaults
- `Settings.from_db()` overlays the table on the defaults. The table stores **text**; `_PARSERS` maps each key to its parser (bools accept the configparser spellings, `""` reads as `None` for the two optional fields, folders go through `resolve_path`), and a parse failure raises `ValueError` naming the key. A key the table holds that this build does not know is ignored, so a database written by a newer version still reads. `init_db` must have run
- `to_db_values()` is the inverse: every `DB_SETTING_KEYS` field as text, `None` as `""`, bools as `true`/`false`. `DB_SETTING_KEYS` is every field; a test pins `_PARSERS` to the same set so a new field cannot be added without saying how it reads
- `with_changes(**changes)` is what a settings update goes through: refuses a key outside `DB_SETTING_KEYS` (a typo), anchors a folder given as text, reads `webhook_url=""` as unset, then `dataclasses.replace` so validation runs. The API path is therefore JSON → `with_changes` → `to_db_values` → `save_settings`
- `settings.py` imports `database.py` (for the three settings functions); the reverse edge does not exist, so there is no cycle
- Both `[folders]` keys have fallbacks. They used to have none, so an incomplete config died with a bare `KeyError` after the folders had already been created
- `max_attempts` is a plain `int`, not `int | None`: unlike `max_download` there is no "unlimited" reading, because retrying forever is the bug the cap exists to fix
- The two folder defaults are module constants (`DEFAULT_DOWNLOAD_FOLDER`, `DEFAULT_AUDIOBOOK_FOLDER`), already resolved, because ruff's RUF009 forbids a function call in a dataclass default
- `create_folders()` is the one side-effecting method: the two `mkdir(parents=True, exist_ok=True)` calls

### runstate.py

`RunState` is the value a poll of `/api/status` reads: a small, lock-protected snapshot of the run in flight - `run_id`, `started_at`, `stage` (`RunStage`: `syncing`, `downloading`, `cancelling`), `account` (unused until there are several), `book` (`id` - the library row, since the same ASIN can be in two accounts - `asin`, `title`), `books_done`/`books_total`, and `transfer` (`desc`, `bytes`, `total`). `snapshot()` returns a copy, or `None` between runs; `begin(run_id)` resets everything and `end()` clears it. One instance lives for the life of the service and is shared by the scheduler and the API. `set_book` also drops the current transfer, since a new book means the previous one is over. It is a snapshot rather than an event stream on purpose: a client polling every second and one polling every minute both get the current picture with nothing to replay, and a push transport can be layered on later without changing what the pipeline reports.

`StateProgress(state)` is the service's `Progress` (see `progress.py`): the same three-call lifecycle as `TqdmProgress`, writing into the state's `transfer` instead of drawing a bar. `service._run` builds one per run.

### scheduler.py

`Scheduler` is one daemon thread that waits until the next run is due, runs the pipeline, and waits again. Everything it reads is injected - `load_settings`, `run` (a `Pipeline`: `(settings, state, cancel) -> None`), `state`, `last_run_start`, `now`, `stop_timeout` - so the tests drive it with fakes rather than with time.

- **Due** is `last_run_start + sync_interval_minutes`, never earlier than now, or *now* if nothing has ever run - where the start is the newer of the run history and `_last_attempt`, when this process last started the pipeline. A pass that records no row (no accounts yet, or none that can log in) still waits its interval; pacing from the history alone read "never run" and ran the pipeline back to back, 143,000 times in three minutes on a fresh install (2026-11-01); `None` when `sync_enabled` is false. `last_run_start` is `database.latest_sync_run_start()`, the newest run **whatever became of it**, so a failing setup waits the full interval between tries instead of retrying every tick, and a restart carries on from the previous process rather than syncing on every boot
- `trigger()` asks for a run now and returns `False` if one is in flight (the API's 409). It works with the schedule disabled: a manual run is the point of turning the schedule off
- `cancel()` sets the event the pipeline checks and flips the state to `cancelling`; `False` when idle
- `wake()` makes the thread re-read the settings, which `PUT /api/settings` calls so a changed interval or a toggled schedule applies to the wait already in progress
- `stop()` cancels the run in flight and joins with `stop_timeout`. This is what a `docker stop` turns into via the app's lifespan: the book being downloaded is handed back to the queue and the process exits cleanly. The library sync and an ffmpeg pass are not interruptible, so `service.STOP_TIMEOUT` is 90 s and `compose.yml` sets `stop_grace_period: 2m` to match; a kill after that is what the stale-claim timeout recovers
- The loop re-reads the settings before every wait and every run; if they will not parse (a bad value saved to the table) it logs and parks until woken rather than dying or spinning. A run that raises is logged and the thread survives; the pipeline has already recorded the failure on its row

### api.py and schemas.py

`create_app(*, scheduler, state, api_token, cors_origins=None, version=None)` builds the FastAPI app; it takes the scheduler and state rather than building them so tests hand it fakes (`FakeScheduler` in `tests/test_api.py` duck-types `start/stop/trigger/cancel/wake/status`). The lifespan starts the scheduler on startup and stops it on shutdown. Every endpoint is a plain `def` so FastAPI runs the SQLite calls in its threadpool.

- Everything under `/api` except `/api/health`, `/api/auth/login` and `/api/auth/logout` requires `Authorization: Bearer <token>` (`HTTPBearer`, compared as bytes with `secrets.compare_digest`) **or** a live web app session cookie (`audible_sync_session`, checked with `webauth.check_session`) - `_require_auth`; neither is a 401 with `WWW-Authenticate: Bearer`. The cookie is what lets an `<img>` load a cover
- The web app's sign-in (see `webauth.py`): `POST /api/auth/login` (`SignIn`: `username`, `password`, `remember` → `Me` and a `Set-Cookie`: `HttpOnly`, `SameSite=Strict`, `Path=/`, `Secure` when the request is https or `create_app(secure_cookie=True)` (`AUDIBLE_SYNC_SECURE_COOKIE`, for a TLS-terminating reverse proxy, behind which the service only sees http) - the service is often plain http on a LAN - and `Max-Age` 30 days only with `remember`; a wrong username or password is one 401 "Wrong username or password"; `LoginThrottle` makes the sixth attempt from an address in 15 minutes a 429 with `Retry-After`; signing in from a browser that already has a session ends that session first), `POST /api/auth/logout` (204, ends the session and clears the cookie; open, so an expired session can still clear it), `GET /api/auth/me` (`Me`: `username`, `generated_password`), `PUT /api/auth/username` (`UsernameChange`) and `PUT /api/auth/password` (`PasswordChange`; ends every *other* session - every session under the bearer token, which has no "this one"), both 403 when `current_password` is wrong, which counts towards the throttle like a failed sign-in, and 422 for a value `webauth` refuses. `SameSite=Strict` plus JSON bodies is the CSRF defence; the CORS middleware sets `allow_credentials` so a web app on a configured origin can use the cookie - **never with `*`** (Starlette then echoes any origin with credentials, and another port on the same host is the same site, so `SameSite=Strict` would not stop a page there acting as the user; `*` logs a WARNING and allows the bearer token only) -, though the dev server is meant to proxy `/api` and be same-origin. `SignIn`, `UsernameChange` and `PasswordChange` cap the password fields at `MAX_PASSWORD_LENGTH` so the open sign-in never hashes an arbitrarily long input. `create_app` takes `login_throttle` for tests
- Errors are always `{"detail": ...}`. A `ValueError` from the settings validators becomes a 422 carrying the message; pydantic's own `ValidationError` (a `ValueError` subclass) is re-raised so a response that does not fit its schema surfaces as a 500, not a misleading 422
- Routes: `GET /api/health` (`status`, `version` from `pyproject.toml`), `GET /api/status` (`scheduler`, `current_run` = `state.snapshot()`, `last_run`, `accounts`), `POST /api/sync` (202 / 409), `POST /api/sync/cancel` (202 / 409), `GET /api/sync/runs?limit=&offset=&account_id=` (`items`, `total`), `GET /api/sync/runs/{id}` (404), `GET /api/sync/runs/{id}/books` (`RunBooks`: `run_id`, `recorded` - the run's `books_recorded` -, and `downloaded`/`failed`/`refused` lists of `RunBookOut`: `book_id` (None once the book is gone), `asin`, `title`, `cover_url`, `error`, `bytes`, `seconds`, `finished_at`; 404 unknown run), `GET /api/settings`, `PUT /api/settings`, `GET /api/accounts`, `GET /api/accounts/{id}`, `PATCH /api/accounts/{id}` (`name`, `enabled`), `DELETE /api/accounts/{id}?deregister=` (deregisters the device with Amazon first when asked and the account has credentials - best effort, the account goes either way; files on disk stay; 409 while the run in flight is on that account, read from `state.snapshot()` - an account the run has not reached yet is fine, the run re-reads each at its turn), `GET /api/marketplaces`, `POST /api/accounts/login` (`LoginStart`: `country_code`, `method` `code`|`browser`, and what the login is for - `name` + `monitor_existing` for a new account, or `account_id` + `deregister_previous` to replace an existing account's credentials via `replace_account_auth` - → 202 with a `LoginOut`: `login_id`, `method`, `status`, `expires_at`, and `code` + `verification_url` or `url`; 422 unknown marketplace via the `ValueError` handler, 404 unknown account - checked before Amazon is asked -, 502 when Amazon will not issue a code or cannot be reached), `GET /api/accounts/login/{login_id}` (the `LoginOut` as it stands: `pending`, `confirmed` with `account`, `expired`, or `failed` with `error`; 404 once forgotten), `POST /api/accounts/login/{login_id}` (`LoginComplete`: `response_url` only - step two of a browser login → 201 with the confirmed `LoginOut`; 404 unknown, expired, finished or a code login - start again, 400 no code in the address - the login stays open, 502 Amazon rejected it - the login is failed). The account-creating work is a `finish` closure built in `_finisher` and handed to the store, so the route for both methods is the same two lines. `create_app` takes `logins: PendingLogins` so a test can reach the store. `AccountOut` never carries `auth`. Books: `GET /api/books` (query `account_id`, `status`, `monitored`, `q`, `sort`, `order`, `page`, `page_size` → `BookList` with `total`), `GET /api/books/{id}` (`BookOut`, including `primary_series` read straight off the `Book` property via `from_attributes`), `PATCH /api/books/{id}` (`monitored` only), `POST /api/books/{id}/delete-files` / `/redownload` / `/retry` (`BookAction` with the files removed; 409 with `library.BookBusy`'s message while a run holds the book, and for `retry` on a downloaded book), `POST /api/books/bulk` (`BulkRequest`: `action` `monitor`|`unmonitor`|`retry`|`delete_files` and exactly one of `ids` or `filter` - `BookFilter`, the listing's `account_id`/`status`/`monitored`/`q` - else 422; 404 for a filter on an unknown account → `BulkResultOut`: `updated`, `queued`, `skipped` `[{id, reason}]`, via `library.bulk`), `GET /api/queue?limit=` (`QueueOut`: `items` - `QueueItem`: `position`, `account_id`, `book` - plus `total` (planned, before refusals), `waiting` and `max_download`; via `library.next_run_queue`), `POST /api/books/retry-unavailable` (`RetryUnavailable`: `account_id` optional, body optional - 404 unknown account → `Requeued` with the count, via `requeue_unavailable`), `POST /api/books/{id}/refresh` (409 when the account has no credentials, 502 when Audible does not answer), `GET /api/books/{id}/cover` (`FileResponse` of `cover_path`, else a 302 to `cover_url`, else 404). The file actions read `Settings.from_db()` for the library folder and templates. Extras: `GET /api/logs?limit=&level=` (`LogList`; empty when the app was built without a `log_buffer`), `GET /api/stats` (`Stats`: per-account `AccountStats` - `account`, `books` counts, `bytes_on_disk`, `last_run` - plus the totals and `next_run_at`), `POST /api/settings/webhook/test` (`url` optional, else the configured one; 400 with neither, 502 when the POST fails). API access and System: `GET /api/settings/api-token` (`ApiTokenInfo`: `last4`, `source` `env`|`stored`), `POST /api/settings/api-token/regenerate` (`NewApiToken`: the `token`, this once; 403 without a session cookie, 409 for an env token), `GET /api/system` (`SystemInfo`: `version`, `ffmpeg_version`, `libopus`, `audiobook_folder` - `DiskOut`: `path`, `free_bytes`, `total_bytes`, `low_space` -, `key_source` `env`|`file`, `key_file`, `database_bytes`)
- **The web app's build** (`create_app(web_dir=...)`, from `AUDIBLE_SYNC_WEB_DIR`): when the folder holds an `index.html`, `_serve_web_app` registers one catch-all route **after** the API routers (so `/api/*`, `/docs` and `/openapi.json` win). It serves a file that exists under the folder - `resolve()` then `is_relative_to` keeps `..` in a decoded path from climbing out (Starlette hands the route `../x` for `%2E%2E/x` or `..%2Fx`) - with `Cache-Control: immutable` under `assets/` (Vite names those after their content); otherwise `index.html` with `no-cache`, so a deep link like `/library?status=failed` reaches the browser's router. An unmatched `/api/...` path stays a JSON 404 and a missing `assets/` file a 404, never the page. `/index.html` asked for by name is `no-cache` too. The route takes **every method**: `_methods_allowed` asks Starlette's own `route.matches` (which sees through the `_IncludedRouter` wrappers `include_router` makes) whether another route has the path, and answers 405 with `Allow` if so, else a JSON 404 for anything but a GET/HEAD page - so a typo or a wrong method answers the same with or without a build. No `index.html`, nothing is registered and every other path is FastAPI's 404 - the tests and an API-only setup. `service.build` logs which it is
- `PUT /api/settings` takes a partial body (`SettingsUpdate`, `extra="forbid"`, every field optional; `model_dump(exclude_unset=True)` tells an omitted field from an explicit `null`, and `null` is a 422 except for `max_download`, `max_consecutive_failures` and `webhook_url` - stored, it would be an empty value `from_db` cannot parse, locking the API out of its own settings), goes through `Settings.from_db().with_changes(...)` so validation runs, saves with `save_settings`, applies `debug` to the root logger at once, and calls `scheduler.wake()`. Other changes apply to the next run: `Settings` is frozen and the scheduler re-reads it per run
- `schemas.py` holds the pydantic models. `SettingsOut` and `SyncRunOut` use `from_attributes` so a `Settings` or `SyncRun` validates directly; `SettingsOut` coerces the two `Path` fields to text in a `mode="before"` validator. These are the contract the web app codes against and may differ from the storage dataclasses

### service.py

`python -m src.service`. `ServiceConfig.from_env()` reads the process-level configuration - `AUDIBLE_SYNC_HOST` (`0.0.0.0`), `AUDIBLE_SYNC_PORT` (`8080`), `AUDIBLE_SYNC_API_TOKEN`, `AUDIBLE_SYNC_CORS_ORIGINS` (comma-separated), `AUDIBLE_SYNC_DEBUG`, `AUDIBLE_SYNC_SECURE_COOKIE`, `AUDIBLE_SYNC_WEB_DIR` (the web app's build, `DEFAULT_WEB_DIR` = `web/dist` under the repo root, through `resolve_path`; the two flags read configparser's true/false spellings through `_flag`, as `config.ini` does, and any other value is a `ValueError` naming the variable) - which is deliberately only what is about the process; what to sync lives in the settings table. `build(config)` does `init_db`, `seed_settings_from_ini`, `Settings.from_db`, `configure_logging`, `crypto.configure(load_secret_key())` + `encrypt_stored_secrets()`, resolves the token, `ensure_web_user()` (the web app's user on the first start, its generated password logged once in a WARNING that names the `reset-password` command), and only **then** installs the `RingBufferHandler` - so a generated token or password goes to the console alone and can never be read back through `GET /api/logs` or copied off the Logs screen - wires `RunState` + `Scheduler` + `create_app`, and returns the app without listening, so a test can drive it with `TestClient`. `main()` hands it to `uvicorn.run(..., log_config=None, access_log=False)`: uvicorn stays on the root logger, and the access log is off because a UI polling `/api/status` would write a line a second.

`apitoken.resolve_api_token(configured) -> ApiToken`: the env value wins (`source="env"`); otherwise the one stored in the settings table under `api_token` (decrypted; a plaintext one from an older version still reads, and one under another key is replaced with an ERROR in the log); otherwise a new `secrets.token_urlsafe(32)` is stored encrypted for next time. Generated once rather than per start, so the token copied from the log keeps working across restarts. `api_token` is a key `Settings.from_db` does not know, so it never appears in `GET /api/settings`. The token is logged **in full only on the start that generates it** (a WARNING saying it is not logged again); later starts log where it came from - `AUDIBLE_SYNC_API_TOKEN`, or the stored one with its last four characters - so `docker logs` attached to a bug report does not hand over the API. Lost, it is replaced by setting `AUDIBLE_SYNC_API_TOKEN`, or by `POST /api/settings/api-token/regenerate` from a signed-in web app.

`ApiToken(value, source)` is the holder `create_app` takes - only that, so a token's source is never guessed; the tests build `ApiToken(TOKEN, "stored")`: `_require_auth` reads `value` **per request**, so `regenerate()` - store a new encrypted token, swap `value` under a lock, a WARNING without the value - takes effect on the next request with no restart. `regenerate` raises `TokenFromEnv` for an `env` token (the API's 409): a stored one would be overridden on the next start. Its `repr` shows `last4`, never the value; `last4` is `""` for a token under `MIN_MASKED_LENGTH` (12), since four characters of a short token are most of it. The regenerate route also wants a **live session cookie** (403 under the bearer token alone), so a leaked token cannot rotate itself and lock its owner's scripts out. `apitoken.py` imports `database` and `crypto` only; it is its own module because `api.py` needs `regenerate` and cannot import `service.py`.

`system.py` is the System card: `probe_ffmpeg(run=subprocess.run) -> Ffmpeg(version, libopus)` (the first line of `ffmpeg -version`, and whether `-encoders` lists `libopus` by name; a missing or failing FFmpeg is `None`/`False` and a WARNING, never raised), `ffmpeg(clock=time.monotonic)` keeps the first probe that **found** FFmpeg (`_probed`) for good and a failed one for `FAILED_PROBE_TTL` (5 minutes), so a later install shows without a restart and the Settings screen does not run FFmpeg and log a WARNING on every load, `disk(path, usage=None) -> Disk` (`shutil.disk_usage` of the nearest existing ancestor, looked up at call time so a test can patch it; `None` sizes when unreadable - the walk is inside the `try` because on 3.12 `Path.exists` raises `PermissionError` under a folder it cannot enter), `is_low_space(free, total)` - free under `min(5% of total, 50 GB)`, the design's rule - and `database_bytes(db_file)` (the file plus `-wal` and `-shm`).

### crypto.py

Encryption at rest for the secrets the database holds: each account's credentials (`accounts.auth`), the API token (`settings.api_token`) and the web app password's hash (`settings.web_password`). The threat is the database *leaving* the host - a backup, a copy sent with a bug report, a volume snapshot - not an attacker already on it: the stored blob is a refresh token and a device private key, which make whoever holds them that device as far as Amazon is concerned. So the key never lives under `data/`.

- `load_secret_key(env=None) -> bytes` - `AUDIBLE_SYNC_SECRET_KEY` wins; else the key file at `AUDIBLE_SYNC_SECRET_KEY_FILE` (default `DEFAULT_KEY_FILE`, `secrets/secret.key` under the repo root, `/app/secrets` in the image), **generated** with a 0700 directory and a 0600 file when there is none - a first start - with a WARNING saying where and to back it up separately. `SecretKeyError` for a malformed key or an unreadable file. Tests pass an explicit `env` dict
- `configure(key)` / `is_configured()` - one key per process, module state on purpose, set by `main._prepare` and `service.build` **after** logging and **before** anything that stores or reads a secret (the API token, a login). `configure(None)` forgets it
- `encrypt(text) -> str` - `enc:` + a Fernet token (AES-128-CBC + HMAC, from `cryptography`; the ciphertext is text and authenticated). `decrypt(stored)` - the inverse; a value **without** the prefix is one written before there was a key and comes back as it is, which is what lets an old database start before `encrypt_stored_secrets` rewrites it. `WrongKey` (a `SecretKeyError`) when the value was encrypted under another key - reported, never misread, thanks to the HMAC
- `generate_key()` - a fresh Fernet key, what the key file and the env variable hold (44 characters of URL-safe base64)
- `key_source(env=None) -> KeySource(kind, path)` - where `load_secret_key` takes the key from (`env`, or `file` and its path; `_key_file` is shared so the two cannot disagree), reading nothing; what `GET /api/system` reports

What the callers do with `WrongKey` matters: `database._account_from_row` reads the account as having **no credentials** (`needs_login`, the pipeline skips it, the API reports it) and logs an ERROR **once per account per process** (`_undecryptable_logged`), **without touching the stored blob** - restoring the right key brings the login back, and a re-login replaces it either way. `apitoken.resolve_api_token` generates and stores a new token. Losing the key therefore costs a login, not data.

### accounts.py

The bridge between the `audible` library's `Authenticator` and the `accounts.auth` column, and the only module that builds an `Authenticator` from the decoded blob or takes one apart. `Authenticator.from_dict`/`to_dict` round-trip exactly, so an account's credentials live in the database with no file; `database.py` encrypts the JSON on the way in and decrypts it on the way out (see `crypto.py`), so nothing here sees ciphertext.

- `authenticator_for(account)` - `Authenticator.from_dict(dict(account.auth))`; the copy matters because `from_dict` pops `locale_code` out of what it is given. `ValueError` for an account with none
- `persist_auth_if_changed(account, auth) -> bool` - `Authenticator` refreshes its access token in memory only; `run_account` calls this in a `finally` so the stored blob does not age until every run begins with a refresh. Compared as dicts, so nothing is written when nothing moved. The write goes through `database.replace_account_auth_if_unchanged` against the snapshot the run started from, so a re-login made while the run was going is not overwritten by the old device's refreshed token
- `display_name(auth)` - `"{customer_info.name} ({COUNTRY})"`, the default account name; `customer_name_of(auth)`
- `add_account_from_authenticator(auth, *, name=None, monitor_existing=True) -> int` - what a confirmed login for a new account becomes
- `check_marketplace(account, country_code)` - `ValueError` when a login for `account` is in another marketplace than its library (the placeholder, with no marketplace, takes any). The API (422) and the CLI check before Amazon is asked, and `replace_account_auth` checks again
- `replace_account_auth(account, auth, *, deregister_previous=True)` - new credentials for an existing account, **keeping its id and so its library** and download history. It reads the account again first (a login takes minutes) and raises `AccountGone` (a `LookupError`) when it was deleted meanwhile, or when `save_account_auth` finds no row to write; the caller owns the new device then and deregisters it. The previous device deregistered is the one stored *now*, not the one read when the login began. The recovery when a registration stops working (the October 2026 licence refusals): the previous device is deregistered with Amazon first, best effort - it is the one that stopped working, so it may not be able to - and the new blob is stored either way; `country_code` and `customer_name` are refreshed, the user's `name` is kept, and the account's licence-refused books are queued again (`database.requeue_unavailable`): the refusals were the old device's, so a new device is the moment to try them. Reached through `POST /api/accounts/login/{login_id}` with `account_id`, or `python -m src.main login --marketplace uk --account ID`
- There is **no auth-file import** any more (removed 2026-11-01): an `audible-cli` file carries a registration made with the old device profile, which Audible refuses download licences, so an imported account failed its first run and needed the code login anyway. The only way in is a login - a new account, or `replace_account_auth` with the `account_id` of one that has none (the migration's placeholder) or whose registration stopped working

The `audible` library validates every credential field on assignment (`audible.utils.test_convert`): `access_token` must start `Atna|`, `refresh_token` `Atnr|`, `adp_token` is `{enc:}{key:}{iv:}{name:}{serial:Mg==}`, `device_private_key` a PEM block. `tests/test_accounts.py`'s `AUTH_BLOB` is the smallest dict that passes; reuse it rather than inventing one.

### audible_login.py

Signing in to a marketplace without a terminal, a browser driver or a password. Two methods, both ending in an `Authenticator` whose `to_dict()` is what `accounts.auth` stores:

**Device code**, the default and what the Audible apps do. `POST api.amazon.{domain}/auth/create/codepair` returns a five-letter `public_code` and a `private_code`; the user enters the public code at `https://www.amazon.{domain}/code` on any device, signing in to Amazon there (2FA included); meanwhile the service polls `POST /auth/register` with the pair, which answers `401 Unauthorized` until the code is confirmed and `200` with the tokens after. Nothing depends on the user's browser, which the browser flow does - Amazon showed "Your device can't be used with this site" to a desktop browser on 2026-10-31. The protocol is the one mkb79/Audible PR #914 implements; once that is released the library's own client can replace the three functions here.

- `DeviceProfile` / `IPHONE` - what the registration says about the app (Audible for iPhone 4.60, iOS 26.2, the matching user agent). **Mirrors `audible` 0.13.1's `register`**: Audible refuses download licences to devices registered with older profiles, so change it with the library
- `start_code_login(country_code, *, now=None, http=None, device=IPHONE) -> CodeLogin` - the code-pair request, with a fresh 40-character serial; `expires_at` is now plus Amazon's `expires_in` (600 s as asked), `interval` Amazon's `polling_interval_in_seconds` (6 s when measured live), never under 1. `LoginFailed` when Amazon refuses or answers nonsense, `ValueError` for an unknown marketplace
- `try_register(login, *, http=None, device=IPHONE) -> dict | None` - one poll: the credentials once confirmed, `None` for `401 Unauthorized` (still waiting) **and** for `429`/`5xx` or a `200` whose body is not JSON (Amazon, logged, polled again), `LoginFailed` for anything else. `credentials_from(success)` maps `response.success` exactly as the library's `register` does (`expires` absolute, cookie values stripped of quotes, `None` for absent cookies/extensions), so both methods store the same blob
- `poll_code_login(login, *, http=None, sleep=time.sleep, now=None) -> Authenticator` - polls at `interval` until `expires_at`, riding out `httpx.HTTPError`; `LoginExpired` (a `LoginFailed`) when the code goes unconfirmed
- `login_with_code(country_code, *, echo=print, http=None, sleep=time.sleep)` - the CLI's version: show the code and the address, block on the poll

**Browser**, the fallback for pre-Amazon Audible accounts: OAuth with PKCE from the `audible` library. `start_login(country_code, *, now=None, ttl=LOGIN_TTL) -> BrowserLogin` (`create_code_verifier()` + `build_oauth_url(...)`, keeps the verifier and serial, 15 minutes), `authorization_code_from(response_url)` (`ValueError` with a message meant to be read when the pasted address carries no `openid.oa2.authorization_code`), `complete_login(pending, response_url) -> Authenticator` (`audible.register.register`, which raises a bare `Exception` carrying Amazon's response on a rejection), and `login_interactively(country_code, *, prompt=input, echo=print)` for the terminal.

`BrowserLogin` and `CodeLogin` share the `Login` base (`id`, `method`, `country_code`, `serial`, `expires_at`, `status`, `account_id`, `error`; mutable, `kw_only` dataclasses so the subclasses can add required fields). `LoginStatus` is `pending` → `confirmed` | `expired` | `failed`.

**`PendingLogins`** is the in-memory store of logins in flight for both methods, and owns the polling thread of each code login. Every login is started with a `Finish` callback - `(Authenticator) -> account id` - which is the API's business (`api._finisher`: `add_account_from_authenticator` or `replace_account_auth`); a confirmed login records what it returned. When `finish` raises, the login is `failed` and **the new device is deregistered** (`discard_device(auth)`: best effort, never raises, returns whether Amazon took it, and the error says so), since nothing would ever hold or deregister it otherwise. `start(country_code, finish) -> BrowserLogin`; `start_code(country_code, finish) -> CodeLogin` (the code-pair request runs in the caller, the thread starts after; the copy returned is taken *before* the thread runs so it reads `pending`); `get(login_id)` (a copy, `expired` computed on read, `None` once forgotten); `complete(login_id, response_url) -> BrowserLogin` (step two: `KeyError` for unknown, expired, finished, in-progress or code logins, `ValueError` for a code-less address **without consuming the login**, the `register` exception after marking it `failed`); `join(login_id, timeout)` waits for a thread (tests, shutdown). A finished or expired login is kept for `ttl` after `expires_at` so a client polling it sees how it ended, then pruned on the next call. Injectable: `now`, `http` (an `httpx.Client`; tests give one on `httpx.MockTransport`), `sleep`. In memory on purpose: a login is a minute's interaction and nothing secret needs persisting; a restart mid-login means starting again. `_shared_http` is one lazily built `httpx.Client` for Amazon's auth endpoints when none is injected.

Tests never reach Amazon: `tests/test_audible_login.py`'s `FakeAmazon` serves both endpoints behind `httpx.MockTransport` from a scripted list of poll answers (`PENDING`, `REJECTED`, `OVERLOADED`, `registered_response()`), with a `gate` event to hold a poll while a test looks at a pending login; `_clock()` gives a `now` and a `sleep` that advances it so polling loops run without waiting. `tests/test_api.py` imports those, injects a `FakeAmazon` into the app's store, and at teardown switches Amazon to refusing and releases the sleep so a login a test left pending ends rather than spinning. The browser `register` is patched on `src.audible_login`; a real `Authenticator` is built from `tests/test_accounts.py`'s `AUTH_BLOB`. The code-pair request was checked against Amazon on 2026-10-31 (a code came back); the browser URL on 2026-09-12.

### library.py

The reverse of the download pipeline: what the API does to one book. Three rules the actions share: a book a run is working on (`downloading`) is never touched (`BookBusy`, the API's 409 - also raised when `reset_for_download` changes nothing because a run claimed the row after the caller read it), the files on disk go before the row forgets them, and every transition goes through `database.reset_for_download` so the state machine has one way back to the start.

- `audio_path_for(book, settings) -> Path | None` - `file_path` when recorded. A book downloaded before it was recorded is looked for where the downloader would file it today (`book_output_paths` with the current templates, then the ` [asin]` variant used for a name collision), and only counts when `read_embedded_asin` says the file carries **this** book's ASIN - so a file filed under other templates is a miss, never somebody else's file. Rows with no `encoding_format` are M4B (Opus arrived with the column)
- `delete_book_files(book, settings) -> list[Path]` - unlinks the audio, PDF, cover and annotations that exist, then prunes empty parents up to but never including `audiobook_folder`, so the last book of a series takes the series folder with it while a folder that still holds a book stays
- `delete_download(book, settings)` - delete the files, `clear_book_files`, `reset_for_download(monitored=False)`: the book stays deleted. `redownload(book, settings)` - the same with `monitored=True`, so it is fetched afresh with attempts reset. `retry(book)` - `reset_for_download(monitored=True)` without touching files, for a `failed` or `unavailable` book; `ValueError` for a downloaded one (that is what redownload is for). `refresh(book) -> Book` - the one network call: `Audible.get_book` on the book's account, `update_books` (the sync's upsert, so the download state is untouched); `ValueError` when the account has no credentials. A token the call renewed is written back with `persist_auth_if_changed` in a `finally`, as a run does; a failing write-back is logged, not raised
- `book_files(book, settings)` - the audio (via `audio_path_for`), PDF, cover and annotations that exist on disk; what `delete_book_files` removes
- `next_run_queue(settings) -> Queue` - the Dashboard's Up next: the books still to come, from the queue as it stands now. Each `Account.runnable` account (the rule `run_pipeline` reads too, so the two cannot drift) in `get_accounts()` order, its `get_books_to_download` (the run's own query, so a stale `downloading` row shows) cut at `max_download`, per account. `Queue.planned` is `(account_id, Book)` pairs; `waiting` counts those accounts' queues uncut. **A plan, not a promise**: a refused licence uses no slot, so the run reaches further down; the sync before the downloads can restore a withdrawn title at its old purchase date, taking a slot from the end of the list; and during a run the list's head is what that run has left (the book in hand is `current_run.book`). It reads every waiting row in full - about 5 ms for 300, measured - which is not worth a second query shape
- `bulk(action, book_ids, settings) -> BulkResult` - one `BulkAction` (`monitor`, `unmonitor`, `retry`, `delete_files`) on many books, **book by book through the functions above**, so the rules stay in one place. A duplicate id is acted on once. Skips carry a `SkipReason` the UI words: `not_found`, `busy` (a run holds it - for every action, including monitor unlike `PATCH /api/books/{id}`, because the design's banner reports it so; also when `_reset` finds the row claimed after the read), `downloaded` (retry), `withdrawn` (retry on an `unavailable` book with `is_consumable` false - a certain refusal that would feed the circuit breaker, left to the sync as `requeue_unavailable` does; the single-book retry still allows it), `no_files` (delete_files on a book with nothing on disk that is not `downloaded`; a downloaded book whose files have gone is tidied like the single-book route does), `error` (anything else one book raises: logged with its traceback, and the batch carries on so the result accounts for every book). `book_files` is looked up once per book and passed to `delete_download(files=)`. `schemas.BulkRequest`/`BulkSkip` type `action`/`reason` with these enums, and `ids` are 1..2^63-1 so an id SQLite cannot hold is a 422 `queued` counts every retried book and, for monitor, only books already `waiting_download` - a failed or parked book keeps its status until retried, which is what the design's result banner says

### webauth.py

The web app's own sign-in: one user, no sign-up, no email. Imports `database` and `crypto` only.

- **The user** lives in the settings table under keys `Settings.from_db` ignores: `web_username`, `web_password` (the hash, **encrypted** with `crypto.encrypt`; the key name is `database.WEB_PASSWORD_SETTING` so `count_unreadable_secrets` sees it) and `web_password_generated` (`true` while the generated password is in use - the UI's "choose your own" banner). `ensure_admin()` creates `admin` with a `secrets.token_urlsafe(12)` password on the first start and returns it for the caller to log; it also regenerates (keeping the username, logging an ERROR, **ending every session** - session rows do not depend on the key, and a key replaced because it leaked must not leave old browsers signed in) when the stored hash will not decrypt, as the API token does. `password_readable()` - whether the stored hash decrypts under this process's key, which `reset-password` checks first. `current_user() -> User | None`
- **Passwords**: stdlib `hashlib.scrypt` (n 2^14, r 8, p 1, 16-byte salt), stored as `scrypt$n$r$p$salt$hash` so the parameters can be raised later without breaking old hashes; `check_password` compares with `compare_digest` and is False for anything unreadable. scrypt rather than the spec's argon2/bcrypt: OWASP-recommended too, and no compiled dependency. `validate_password`: 12 to 1024 characters (scrypt's cost grows with the input). `verify(username, password)` checks both in constant time, the username trimmed as `set_username` trims it; `_password_matches` (shared with `check_current_password`) always runs one hash - against a dummy when there is no stored hash - **before** combining the results, so neither the answer nor the timing says which was wrong. `check_current_password`, `set_password(password, *, generated=False)`, `set_username` (trimmed, 1-64 characters, `ValueError` otherwise), `reset_password(*, reset_username=False)` for the CLI
- **Sessions** are rows of the `sessions` table keyed by the SHA-256 of the cookie's `token_urlsafe(32)`, so a copy of the database is not a session. In the database rather than memory so "Keep me signed in" survives a restart and the CLI's reset signs out a running service's browsers. `create_session(*, persistent, now=None) -> token` (also deletes expired rows); `check_session(token, *, now=None)`: a persistent session ends `PERSISTENT_SESSION` (30 days) after the sign-in, matching the cookie's Max-Age; a browser session slides, ending `BROWSER_SESSION` (12 hours) after its last use. Use is recorded at most once a `TOUCH_INTERVAL` (a minute), so a UI polling `/api/status` every 2 s does not write every request. `end_session`, `end_other_sessions(token | None)`, `end_all_sessions()`
- **`LoginThrottle`**: sign-in attempts per client IP, in memory. `attempt(key)` counts the attempt **before** the password is checked and returns None, or refuses with the seconds until the oldest of `max_failures` (5) within `window` (15 minutes) ages out, counting nothing; `succeeded(key)` clears the client. Counting after the check let a parallel burst all pass it during the 50 ms each hash takes (and 40 threadpool workers at 16 MiB of scrypt each); counting first caps the hashes in flight per client at five. Every call prunes every client, so addresses that never return are forgotten. Keyed by the socket's address, not a forwarded header anyone can set - behind a reverse proxy everyone shares one address, acceptable for a single-user app. Injectable `clock`

### logbuffer.py and notify.py

`RingBufferHandler(capacity=1000)` is a `logging.Handler` keeping the newest records as plain dicts (`time`, `level`, `logger`, `message`, with the traceback appended for `exc_info`); `records(limit=, level=)` returns the newest `limit` at or above `level`, oldest first. `service.build` installs one on the root logger after `configure_logging`, and passes it to `create_app(log_buffer=...)` for `GET /api/logs`. `docker logs` is the real log; this is the copy a UI can show. Tests attach it to a logger of their own **with the level set** - the default WARNING level drops INFO before the handler sees it.

`notify.py`: `run_finished_payload(run, account)` is `{"event": "sync.finished", "run": asdict(run), "account": {id, name, country_code}}`; `send_webhook(url, payload, *, client=None)` POSTs it with a 10 s timeout and raises `httpx.HTTPError` on failure (the API's `POST /api/settings/webhook/test` wants that); `notify_run_finished(url, run, account) -> bool` wraps it and **never raises** - a webhook is a courtesy and a receiver that is down must not fail the run. `run_account` calls it in its outer `finally`, after the row is closed on either path, with `get_sync_run(run_id)` so what is sent is the final record; `tests/test_main.py` patches both on `src.main`. `httpx.MockTransport` is how the POST itself is tested.

### paths.py

`REPO_ROOT` (the directory containing `src/`, `/app` in the image) and `resolve_path(value)`, which expands `~` and anchors a relative path to `REPO_ROOT`. Absolute paths pass through unchanged and the result is deliberately **not** `resolve()`d: the audiobook folder is a bind mount under Docker and often a symlink to a network share.

It is its own module rather than part of `settings.py` because `database.py` needs `REPO_ROOT` too, and `Settings.from_db` will import `database.py`.

### model.py

`Book` is a dataclass and models both an Audible API item and a row of `library`; `SyncRun` (below) does the same for `sync_runs`.

**API fields:** `asin`, `title`, `subtitle`, `authors`, `narrators`, `series` (list of `{"title", "sequence"}`, either of which may be `None`), `genres`, `length` (minutes), `is_finished`, `percent_complete`, `date_added`, `release_date`, `cover_url`, `has_pdf`, `is_consumable`.

`is_consumable` comes from `customer_rights.is_consumable` and is `False` while Audible has withdrawn a Plus title. It **fails open** everywhere - a missing response group, a missing key, or a NULL column all read `True` - because defaulting to `False` would park an entire library in one sync.

**Database-only fields**, `None` on a book that came straight from the API: `id` (the row id, what the API addresses a book by), `account_id`, `status`, `attempts`, `last_error`, `last_attempt_at`, `file_path` (where the audio was filed; NULL on books downloaded before it was recorded, see `library.audio_path_for`), `pdf_path`, `cover_path`, `annotations_path`, `encoding_format`, `downloaded_at`; plus `monitored` (default `True`), whether the book is wanted at all - an unmonitored book is never queued. `attempts` defaults to `0`, which must stay in step with the column default - a database test compares a whole freshly inserted `Book` for equality (with the row id substituted).

`Account` is one row of `accounts`: **one Audible marketplace login**, not a person - the same person has one per marketplace. `id`, `name`, `country_code`, `customer_name`, `auth` (the decoded `Authenticator.to_dict()` blob, kept out of `__repr__`), `enabled`, `monitor_existing`, `created_at`, `last_synced_at`. `auth` may be `None`: `needs_login` is then true, the pipeline skips the account and the API reports it. `monitor_existing` is the choice made when the account was added - whether the back catalogue is queued (`True`) or inserted unmonitored so the user picks (`False`) - and governs only the first, full fetch; the incremental sync follows the global `auto_monitor_new` setting.

`BookStatus` is a `StrEnum` living here beside `Book`: `waiting_download`, `downloading`, `downloaded`, `unavailable` and terminal `failed`. **`unavailable` is not terminal.** Audible withdraws Plus (`AYCL`) titles a customer added while they were included - the book stays in the library but stops being licensable - and later offers them again; 16 of the 306-title library were withdrawn when measured (2026-09-09). Such a book leaves the download queue so it stops costing a licence request every run, and the next sync that sees it consumable returns it to `waiting_download` on its own. A `StrEnum` because a member *is* the text the column already stores, so no row had to be rewritten and a comparison against a plain string still holds. `_book_status` maps the column onto the enum and returns `None` for anything unrecognised rather than raising - a database written by another version has to stay readable, and nothing selects on the Python value because the queue is a SQL predicate.

`date_added` is an **ISO 8601 string** exactly as Audible returns it (e.g. `2024-01-01T00:00:00Z`), stored, sorted and compared as text. It is never parsed into a `datetime`.

**`Book.primary_series` is how you read a book's series; never `series[0]`.** Audible returns the series a book belongs to in an order that is not stable and carries no notion of a primary one - not between the `library` list and `library/{asin}`, and not even between two books of the same series in one response (measured 2026-09-09: `Ringworld` came back with "Known Space" first, `The Ringworld Engineers` with "Ringworld" first). Taking index 0 filed one series under two folder names. `_series_sort_key` owns the rule: **lowest sequence wins, ties fall to the title and then the series ASIN.** It reads as "prefer the series this book is early in over the omnibus it is buried in", so *Dune* files under "Dune" at 1 rather than "The Dune Sequence" at 12; being a total ordering, the answer never depends on the order the API used, which is what keeps the three *His Dark Materials* books together despite Audible listing a typo'd "His Dark Materialsik" at the same sequence. An entry with no usable sequence sorts last (no sequence is no evidence); one with **no title is not a candidate at all**, because a sequence alone would render a bare `{author}/2 - {title}/` folder. `sort_series` applies the same key in `_prepare_book`, so the stored JSON is canonical and the upsert stops rewriting the column every sync.

A series entry is `{"title", "sequence", "series_asin"}`. `series_asin` is the series' own ASIN, which identifies it independently of a title Audible can typo; it is read defensively everywhere, so a row written before it was stored still resolves and **no migration is needed** - `series` is a JSON column.

`Book.from_row(row)` is the single owner of the JSON decode and the SQLite 0/1 to bool coercion. It reads by column name through `dict(row).get(...)`, so a column the row does not carry falls back to the field default and a legacy database still reads; only `asin` is required. The hand-written `__repr__` is kept on purpose - the generated one would put the cover URL and three file paths into every log line that formats a book. Dataclass field order deliberately does not mirror the table (`has_pdf` sits with the API fields); nothing is positional against a row any more.

`SyncRun` models a row of `sync_runs`: `id`, `account_id` (`None` on a row from before there were accounts), `started_at`, `finished_at`, `outcome`, `books_seen`, `books_added`, `books_downloaded`, `books_failed`, `error`, `books_recorded` (whether the run recorded its books in `sync_run_books`; False on runs from before, so "no books" and "not recorded" differ). Same `from_row` idiom as `Book`. `RunBook` is a row of `sync_run_books` - `run_id`, `asin` and `title` (a snapshot), `outcome` (`RunBookOutcome`: `downloaded`, `failed`, `refused`; an unknown value is kept as text), `finished_at`, `book_id` (from a `LEFT JOIN`, None once the library row is gone), `error`, `bytes`, `seconds`, `cover_url` (the library's). `SyncOutcome` is the matching `StrEnum` - `running`, `success`, `partial`, `failed`, `cancelled`, `aborted` - for the same reason `BookStatus` is one, with `_sync_outcome` tolerating an unrecognised value. **`partial` means the sync completed but the downloads did not all succeed**, and it counts as a cursor: the library really was read, and which books failed belongs to the `library` state machine. **`cancelled` is a run somebody stopped**; a cancel is only honoured after the library sync (seconds, against the hours of downloads), so it has always read the library through and counts as a cursor too. **`aborted` is a run that stopped itself**: `max_consecutive_failures` books in a row refused or failed (the circuit breaker in `download_books`), which is the account or Audible rather than the books; also after the sync, so also a cursor, with the reason in `error`. A row still `running` with a NULL `finished_at` is a run that was killed - the thing the library table could never tell apart from a run that found nothing.

### database.py

Six tables: `accounts`, `library`, `sync_runs`, `sync_run_books` (one row per book a run downloaded, failed or was refused, see below), `settings` and `sessions` (the web app's sign-ins: `id` is the SHA-256 of the cookie's token, `persistent`, `created_at`, `last_seen_at`, `expires_at`; `add_session`, `get_session`, `touch_session`, `delete_session`, `delete_sessions(*, keep=None)` and `delete_expired_sessions(now)` at the foot of the module, with `src.webauth` owning the lifetimes). `init_db()` sets `PRAGMA journal_mode=WAL` (a property of the database *file*, so setting it once holds for every later connection, including one opened by a process that never calls `init_db`), creates the tables, then runs `_migrate_schema()` (adds any missing `library` columns - `pdf_path`, `cover_path`, `annotations_path`, `has_pdf`, `encoding_format`, `downloaded_at`, `is_consumable`, `attempts`, `last_error`, `last_attempt_at`, `monitored`, `file_path` - and `sync_runs.account_id` and `sync_runs.books_recorded`, 0 for every existing run) and then `_migrate_library_to_accounts()`.

**`library` is keyed by `(account_id, asin)`** with a surrogate `id INTEGER PRIMARY KEY`: the same ASIN can be owned in two marketplaces. SQLite cannot change a primary key in place, so a pre-accounts database (no `account_id` column) is rebuilt once: the table is renamed to `library_legacy`, the DDL (`_library_ddl()`, shared with `init_db` so the two cannot drift) creates the new shape, whichever of `_LIBRARY_COLUMNS` the old table had are copied under one account, the old table is dropped and `_create_indexes` recreates the indexes. That account is created **with no credentials** under `LEGACY_ACCOUNT_NAME` if none exists; a login with its `account_id` (`POST /api/accounts/login` or `login --account`) fills it in, keeping its id. Old `sync_runs` rows are given the same account, and serve as its cursor and no other's (see `latest_successful_sync_start`). Verified against a copy of the live database (2026-09-12): every row and status carried over, the queue and cursor unchanged.

**Functions:**
- `init_db()`
- `update_books(account_id, books, *, monitor_new=True)` - an upsert into one account's library: inserts new books at `waiting_download` with `monitored = monitor_new` and, `ON CONFLICT(account_id, asin)`, refreshes only the mutable API fields (title, subtitle, the four JSON lists, length, `is_finished`, `percent_complete`, `release_date`, `cover_url`, `has_pdf`). It deliberately never writes `date_added` - that is the incremental sync cursor, and moving it would skip or re-fetch purchases - nor the three retry columns, the accessory paths, `encoding_format` or `downloaded_at`, all of which belong to the downloader. It **does** own `is_consumable`, and `status` **only** between `waiting_download` and `unavailable` in either direction (a `CASE` in the conflict clause): that is how a withdrawn Plus title leaves the queue and a restored one rejoins it with no manual step. `downloading`, `downloaded` and `failed` are never touched, and neither is `monitored` on conflict - whether a book already in the library is wanted is the user's decision. Returns the count **inserted**, taken as `SELECT COUNT(*)` either side of the write on the same cursor: `cursor.rowcount` after an `executemany` of an upsert is `-1`, not a count, and `RETURNING` cannot be used with `executemany` at all. Handles a duplicate ASIN inside a single batch, which a check-then-insert could not. Keep the explicit column list rather than generating one from the dataclass; its `json.dumps` must stay in step with `Book.from_row`'s decode
- `get_books(limit=None, *, account_id=None) -> list[Book]` - all books, newest `date_added` first; one account's when given
- `get_books_to_download(*, account_id=None, stale_after=STALE_CLAIM_SECONDS) -> list[Book]` - the download queue, oldest `date_added` first, **monitored books only**, one account's or everyone's: rows still `waiting_download`, **plus** any left in `downloading` whose `last_attempt_at` is older than `stale_after` (a NULL timestamp counts as stale). It must use the same rule as `claim_book_for_download` or the two disagree about what "abandoned" means. Selecting `waiting_download` alone is what made `STALE_CLAIM_SECONDS` unreachable: the claim could always reclaim an abandoned row, but nothing ever offered it one, so a book a killed run held stayed `downloading` forever with its part-file. Does **not** claim; the caller claims each book individually before working on it, so a row a *live* run holds is offered here and then simply fails to claim - which is why this can afford to be generous
- `get_book(book_id) -> Book | None` - by row id, how the API addresses a book. `get_book_by_asin(asin, *, account_id=None)` - in one account's library, or the first found in any; only unambiguous while one account holds the ASIN, so it is a convenience for tests and single-account code
- `latest_date_added(account_id)` - `MAX(date_added)` in one account's library, the incremental sync cursor fallback. Independent of how `get_books` sorts
- `needs_consumability_refresh(account_id) -> bool` - whether anything in that account's library is parked `unavailable` with `is_consumable` 0 (withdrawn at sync time), or has it NULL (predates the column, nullable precisely so NULL can mean "never read from the API"). A withdrawn book that is downloaded does not count: it kept the full re-read on for good. Keyed on the stored view, not on `status`: a licence-refused book reads consumable and a re-read would only confirm it. `sync_library` uses it to decide whether to re-read the whole library
- **The state-machine functions take the row id**, `book.id`, not the ASIN: `mark_book_downloaded(book_id, encoding_format=None, *, file_path=, pdf_path=, cover_path=, annotations_path=)` - sets status `downloaded`, `encoding_format` and `downloaded_at` (ISO 8601 UTC from `_utcnow()`, monkeypatch it in tests) and records the accessory paths in the same statement. Paths not given keep their current value. Clears `last_error` - a book that succeeded on its second attempt must not keep showing the first failure - but keeps `attempts`, a true record of what the book cost
- `claim_book_for_download(book_id, *, stale_after=STALE_CLAIM_SECONDS) -> bool` - takes ownership in a single UPDATE: status to `downloading`, `attempts + 1`, `last_attempt_at` now, matching `waiting_download` **or** a `downloading` row whose `last_attempt_at` is older than `stale_after` (a NULL timestamp counts as stale, or such a row would never be picked up again). Returns whether this caller won. Two processes cannot both take one book: the second matches no rows. `STALE_CLAIM_SECONDS` is 6 hours - longer than the slowest real book, short enough that a crashed run recovers on the next tick rather than by hand. That recovery only happens because `get_books_to_download` offers such a row; keep the two queries in step
- `mark_book_failed(book_id, error, *, max_attempts, terminal=False) -> BookStatus | None` - records `last_error` and decides retry-or-give-up **inside** the UPDATE (`CASE WHEN ? OR attempts >= ?`), from the `attempts` the claim already incremented, so it cannot race another process between a SELECT and an UPDATE. `terminal` short-circuits the count for a failure already known to be permanent. Returns the status the book landed in, or `None` for an unknown ASIN
- `mark_book_unavailable(book_id, error)` - parks a book Audible will not license for download: status `unavailable`, `last_error` recorded, **`is_consumable` left as the sync read it**. **Not** terminal and does **not** count towards `max_attempts`. It stays parked until `reset_for_download` (the API's `retry`): the sync's availability refresh only restores a book whose *stored* view was withdrawn and whose API view flipped, so a book the API calls consumable but Audible refuses anyway is not re-queued to be refused again. It used to write `is_consumable = 0`, which made the refresh "restore" it every run; with `max-download = 2`, two such books blocked everything (found live, 2026-10-31)
- `release_book(book_id)` - status back to `waiting_download` and `attempts - 1`, guarded on `downloading`. For an abort that is not the book's fault: expired credentials fail every book equally, so charging it to whichever book was next would eventually mark a good one `failed`
- `update_book_accessories(book_id, pdf_path=, cover_path=, annotations_path=)` - accessory paths only; unused by the pipeline, kept for the API

The `sync_runs` functions are grouped together at the foot of the module, by table rather than split across the readers and writers above, because they are only meaningful together:

- `start_sync_run(account_id=None) -> int` - opens a row at `running` with `started_at = _utcnow()` and returns its id. Written before anything is fetched, so a killed run leaves the row behind as its own record
- `finish_sync_run(run_id, *, outcome, books_seen=, books_added=, books_downloaded=, books_failed=, error=)` - closes the row with `finished_at = _utcnow()`
- `latest_successful_sync_start(account_id=None) -> str | None` - `MAX(started_at)` over `_CURSOR_OUTCOMES` (`success`, `partial`, `cancelled`, `aborted`) for that account only (`account_id IS ?`). Runs from before there were accounts are given to the migrated account by the migration; counting unowned rows for every account made an account added later start incrementally and never fetch its back catalogue. **The incremental sync cursor.** The run's *start*, not its finish, because anything Audible added while the run was reading has to be picked up next time
- `latest_sync_run_start() -> str | None` - `MAX(started_at)` over **every** run, whatever became of it. What the scheduler paces itself from, so a failing setup waits the full interval between tries
- `record_run_book(run_id, book, outcome, *, error=, bytes=, seconds=)` - one book's outcome in a run, with the ASIN and title as a snapshot; `ValueError` for an outcome outside `RunBookOutcome`. `start_sync_run` sets `books_recorded = 1`. `get_run_books(run_id) -> list[RunBook]` in the order written. SQLite foreign keys are not switched on, so `delete_account` deletes the account's run records itself, and a book row that goes leaves the record with `book_id` read as None
- `get_sync_runs(limit=20, offset=0, *, account_id=None) -> list[SyncRun]`, `get_sync_run(run_id) -> SyncRun | None`, `count_sync_runs(*, account_id=None) -> int` - the run history reads; unused by the pipeline, these are what the API and UI are for

The **book management** functions are what the API's per-book actions record; the file deletion itself is `library.py`'s:

- `clear_book_files(book_id)` - forgets `file_path`, the three accessory paths, `encoding_format` and `downloaded_at`; the status is left to `reset_for_download`
- `set_monitored(book_id, monitored) -> bool` - False for an unknown book
- `requeue_unavailable(account_id=None) -> int` - the bulk retry: every `unavailable` book whose `is_consumable` is not 0 (NULL fails open) back to `waiting_download` with `attempts = 0` and `last_error` NULL, `monitored` untouched; one account's or everyone's; returns the count. **Skips titles Audible itself withdrew** - the sync restores those when they are offered again, and queuing them is a guaranteed refusal that would feed the circuit breaker. What `POST /api/books/retry-unavailable` and `replace_account_auth` call, because a licence-refused book otherwise stays parked forever (see `mark_book_unavailable`)
- `reset_for_download(book_id, *, monitored) -> bool` - `waiting_download`, `attempts = 0`, `last_error` NULL, `monitored` as asked, **guarded with `status != downloading`**: a run holds such a row and its own bookkeeping would overwrite this. Returns whether the row changed; the caller looks the book up first to tell busy from missing
- `book_ids_matching(*, account_id=, status=, monitored=, q=) -> list[int]` - every row id the same filters match, in id order; `list_books` and this share `_book_filter`, so a bulk action on "all matching" acts on what the listing counted
- `list_books(*, account_id=, status=, monitored=, q=, sort="date_added", descending=True, limit=50, offset=0) -> (list[Book], int)` - a page and the total that matched. `q` is a `LIKE` over the title, the `authors` JSON and the `series` JSON, which is enough at the size of a personal library; `sort` must be a key of `BOOK_SORT_COLUMNS` (`date_added`, `title`, `author` - the JSON text, first author first -, `release_date`, `downloaded_at`), `ValueError` otherwise, and the API's `Literal` for `sort` must stay in step with it

For the dashboard: `library_stats(*, account_id=None) -> dict` (`by_status` with every `BookStatus` present, zero when empty, plus `monitored`, `unmonitored`, `total`) and `downloaded_file_paths(*, account_id=None)` (every recorded `file_path`, which `api._bytes_on_disk` stats, ignoring files that have gone).

The `accounts` functions follow the same text-in-text-out rule as `settings`: the JSON of `Authenticator.to_dict()` is stored and returned decoded, and `src/accounts.py` is the only thing that builds an `Authenticator` from it.

- The blob is **encrypted at rest**: `_encode_auth` runs the JSON through `crypto.encrypt` on every write (`add_account`, `save_account_auth`) and `_account_from_row` decrypts on every read, reading an undecryptable blob as `auth=None` (logged once per account) rather than failing the whole listing. `API_TOKEN_SETTING` (`api_token`) is the settings key the token is stored under, encrypted the same way by `apitoken.resolve_api_token`
- `count_unreadable_secrets() -> int` - how many stored secrets (`accounts.auth`, `api_token`, `web_password`) the configured key cannot decrypt (`WrongKey`); what the CLI's other-key warning reads
- `encrypt_stored_secrets() -> int` - the one-off migration for a database from before there was a key: every plaintext `accounts.auth` and a plaintext `api_token` are rewritten encrypted; idempotent, run at every start right after the key is configured. Returns the count, logged when non-zero
- `add_account(name, country_code, *, auth, customer_name=None, monitor_existing=True) -> int` - `auth=None` means it still needs a login
- `get_accounts() -> list[Account]` (oldest first), `get_account(account_id) -> Account | None`
- `update_account(account_id, *, name=, country_code=, customer_name=, enabled=)` - only the fields given
- `replace_account_auth_if_unchanged(account_id, expected, auth) -> bool` - a compare-and-set in one `BEGIN IMMEDIATE` transaction, what a run's token write-back uses
- `save_account_auth(account_id, auth) -> bool` - store, or clear with `None`; whether the account was there to write to. `mark_account_synced(account_id)`
- `delete_account(account_id)` - cascades to that account's `library` rows and `sync_runs`; files on disk are left alone

The `settings` functions sit at the foot of the module for the same reason. The layer is deliberately **typeless** - text in, text out - so a value the running build cannot parse is still stored and read back rather than lost; `src/settings.py` owns parsing, defaults and validation:

- `get_settings() -> dict[str, str]` - every stored key
- `save_settings(values)` - an upsert per key with `updated_at = _utcnow()`, so a partial update leaves every other setting alone. An empty dict writes nothing
- `has_settings() -> bool` - whether anything was ever stored; what `seed_settings_from_ini` checks

**Reads return `Book` objects, not tuples.** `_get_connection()` sets `row_factory = sqlite3.Row` and every reader maps rows through `Book.from_row()`. Access is by column name, which is why `SELECT *` stays correct even on an older database where `_migrate_schema` appended columns in a different order than the DDL - positional indexing was silently wrong there. Never index a row positionally.

Note `_migrate_schema` only adds the ten later columns, so a database predating the rest of the schema still lacks `date_added`; `get_books`, `get_books_to_download` and `latest_date_added` all reference it and raise on that schema. `get_book_by_asin` works, because `from_row` falls back to field defaults. Widening the migration belongs with the status/state-machine work.

All functions close their connections (`contextlib.closing`). `init_db` also indexes `date_added`, and the download queue with a composite `(status, date_added)` matching the shape of its query - SQLite serves a plain status lookup from the leading column, so the superseded single-column `idx_library_status` is dropped rather than kept alongside. `sync_runs` gets `(outcome, started_at)`, the shape of the cursor query. Guarded by the columns (and, for `sync_runs`, the table) actually present so an old database still migrates: the composite raises on a schema with no `date_added`.

### audible_client.py

`Audible(auth: audible.Authenticator)` wraps `audible.Client`, bound to the authenticator (and so the marketplace) it is given. **The module is named `audible_client.py`, not `audible.py`:** the old name shadowed the dependency it wraps, so isort could not tell the two apart and `known-third-party = ["audible"]` had to be pinned in `pyproject.toml` to hold the import grouping together. That override is gone; do not reintroduce a first-party module named `audible`.

- `get_library(purchased_after=None)` - follows pagination until a short page comes back, so a library over 1000 titles syncs fully
- `get_book(asin)` - single book, same response groups. No caller yet
- `RESPONSE_GROUPS` - the ten groups `_prepare_book` actually reads, shared by both calls. `customer_rights` comes back on the bulk library endpoint, so spotting withdrawn titles costs no extra call
- `_prepare_book(item)` - maps an API item to `Book`, reading every optional field defensively. Prefers the 1215px cover, falls back to 500px. Sets `has_pdf` from `pdf_url` and `is_consumable` from `customer_rights.is_consumable`, defaulting to `True`. Each series entry keeps its own `series_asin`, and the list goes through `sort_series` so what is stored is canonical rather than whatever order the response used - see `model.py`
- `_prepare_books(items)` - maps a page and skips (with a logged traceback) any single item that cannot be read, so one odd podcast or unnumbered series entry does not abort the run

### sync.py

`sync_library(audible, account, *, auto_monitor_new=True) -> SyncResult`, a NamedTuple of `(books_seen, books_added)`. It returns both because `run_account` records both; `books_seen` is the length of the incremental fetch. Everything it reads and writes is scoped to `account.id`.

**Whether an inserted book is monitored** depends on which fetch found it: the first, full fetch is the back catalogue and follows `account.monitor_existing`; an incremental fetch finds new purchases and follows `auto_monitor_new`. A book already stored keeps whatever the user set (`update_books` never writes `monitored` on conflict).

**The cursor** comes from `_sync_cursor(account_id)`, in preference order:

1. `latest_successful_sync_start(account_id)` minus `CURSOR_OVERLAP` (one hour) - a real record of "last synced"
2. `latest_date_added(account_id)`, the `MAX(date_added)` this used to derive everything from, so a database that predates `sync_runs` behaves exactly as it did until it has recorded its first run
3. `None`, meaning fetch the whole library

The overlap is not decoration: `purchased_after` is filtered on **Audible's** clock, so a local clock running even slightly fast would step straight over a purchase and never look at it again. Re-reading an hour costs nothing because `update_books` is an upsert.

`_api_timestamp(moment)` formats the cursor as `%Y-%m-%dT%H:%M:%SZ`. This conversion is required, not cosmetic: `database._utcnow` writes `+00:00` while Audible's own `date_added` is the `Z` form, so a run timestamp cannot be handed to the API unconverted. A cursor taken from `latest_date_added()` needs no conversion - it came from Audible in the first place.

It then re-reads the **whole** library when `needs_consumability_refresh()` says so - while any book is parked `unavailable`, or any row predates the `is_consumable` column. An incremental fetch never re-reads a book already in the library, so on its own it could never notice that Audible had offered a withdrawn Plus title again and the book would stay parked forever. The extra pass is one request per 1000 titles, only happens while something is parked, and its inserts are not added to the returned count.

### downloader.py

The largest module. Key pieces:

**`Downloader(audible)`**
- `get_license_response(asin, quality)` - quality is `"High"` (the correct value for AAXC downloads). Raises `LicenseError` unless the response says `Granted`; a denied license is a normal 200 with no `content_metadata`
- `get_download_link(license_response)` (static)
- `download_file(url, filename, desc=None)` - streams through the shared client; raises on HTTP errors. An **instance** method, not static, so it reports to `self.progress`; until it took a `desc` this was the one download - the multi-gigabyte one - the bar could not name
- `get_chapter_info(asin)` - `content/{asin}/metadata` with `chapter_info`. Returns `None` on `httpx.HTTPError`
- `download_book(book, temp_dir)` - license, decrypted voucher (written before the download so a key problem is found early), AAXC download, chapters. Returns a `DownloadedBook(aaxc, voucher, chapters)`
- `download_pdf(asin, path)` - `https://www.audible.{domain}/companion-file/{asin}` via the authenticated session; checks content type
- `download_cover(url, path)` - via the **unauthenticated** shared client: the audible session signs every request, which would send the account's ADP token to the image CDN
- `download_annotations(asin, path)` - Amazon sidecar endpoint; only writes a file if clips or bookmarks exist

**Accessory contract:** the three accessory methods return `False` only when the thing is genuinely absent (404, non-PDF content type, no clips or bookmarks). Every other failure raises, so the book goes back to the queue and is retried (up to `max-attempts`) instead of being filed as complete with a `NULL` path that nothing would ever fix.

**HTTP:** `get_http_client()` is a shared `httpx.Client` with a 30s connect / 120s read timeout and redirects followed. httpx defaults to 5s, which aborted a part-finished multi-gigabyte download on any brief CDN stall. `_stream_to_file(response, path, desc=None, progress=None, cancel=None)` is the one streaming loop for all three downloads; it writes to a `.part` file and renames on completion. Progress goes to the injected object (see `progress.py`), defaulting to `NullProgress`, and `finish()` runs in a `finally` so a failed download does not leave a bar open across the next one. `cancel` is checked between chunks and raises `SyncCancelled`, which is what makes a stop take effect within seconds of a multi-gigabyte download rather than at the end of it; the `.part` file is left for the temp-folder cleanup. `Downloader(audible, progress=None, cancel=None)` holds both and passes them to all three transfers.

**Cancellation:** `SyncCancelled` is not a `RuntimeError` - nothing about the book went wrong - and `download_books` handles it like an auth failure: `release_book` (attempt count untouched) and break. The event is also checked *before* each claim, so a book the run never reached is left exactly as it was. `decrypt_aaxc` is not interruptible; a cancel that lands during ffmpeg waits for that one book. `DownloadStats` carries a `cancelled` flag (defaulted, so the four counts still read positionally) from which `run_pipeline` records `SyncOutcome.CANCELLED`.

**Chapters:** `flatten_chapters(chapters)` descends into the nested `chapters` list Audible returns for books split into parts. Taking only the top level left a multi-part book with a few hours-long "Part One" markers instead of its real chapters.

**Metadata and decryption**
- `generate_metadata(book)` - takes a `Book`; title/album, artist/album_artist/author, composer (narrators), series, genre, year, ASIN comment. Series comes from `Book.primary_series`, which guarantees a title; the sequence still needs `or ""` because `_prepare_book` always creates the key, so a `.get(..., "")` default never fires and a null sequence used to reach `_escape_ffmetadata` and be written as a literal `series-part=None`
- `write_ffmpeg_metadata_file(metadata, path, chapters=None)` - writes an FFMETADATA1 file including `[CHAPTER]` blocks, escaping through `_escape_ffmetadata`. A newline is escaped as a backslash followed by the real newline; writing the two characters `\` and `n` would be read back as a literal `n`
- `decrypt_aaxc(book, voucher, book_data=None, cover_path=None, chapters=None, *, encoding_format="m4b", bitrate=64)` - runs `ffmpeg` via `subprocess` with `-audible_key`/`-audible_iv` and raises on non-zero exit. The argv comes from one of two helpers:
  - `_m4b_ffmpeg_args` - `-c:a copy`, cover mapped as `attached_pic`, metadata + `[CHAPTER]` blocks from the FFMETADATA file via `-map_metadata`/`-map_chapters`. `-map_chapters` is only passed when there **are** chapters: pointing it at a chapterless metadata file discards the chapter track the AAXC itself carries, which FFmpeg would otherwise have copied. The MP4 muxer only writes the keys it knows and drops `series`, `series-part`, `author` and `media_type`; after FFmpeg succeeds `decrypt_aaxc` calls `write_m4b_extra_tags` (in `src/encoding.py`) to add those as iTunes freeform atoms. Do not use `-movflags use_metadata_tags` for this: it keeps every key but removes the embedded cover (verified 2026-09-08)
  - `_opus_ffmpeg_args` - `-c:a libopus -b:a {bitrate}k -vbr on` into the `oga` muxer. Ogg has no picture stream or chapter track, so the cover goes in as a `METADATA_BLOCK_PICTURE` tag and chapters as `CHAPTERxxx`/`CHAPTERxxxNAME` tags (built by `src/encoding.py`) inside the FFMETADATA file, and `-map_chapters -1` stops FFmpeg copying the AAXC's own chapters on top of them. FFmpeg renames `comment` to `DESCRIPTION` and `album_artist` to `ALBUMARTIST` in Ogg

**Orchestration**
- `_process_book(downloader, book, temp_dir, settings)` - the full pipeline for one book: download, accessories, decrypt, release the AAXC, file into the library, mark downloaded with `file_path` and the accessory paths in one statement. Raises on any failure.
- `_download_accessories(downloader, book, temp_dir, safe_title)` - PDF, cover and annotations, keyed by the database column they belong to. The cover extension comes from the image bytes (`image_info`), not the URL, because plenty of cover URLs carry no extension and the name is kept permanently.
- `_resolve_output_path(final_folder, stem, extension, asin)` - keeps two books that render to the same name apart. Every output carries its own ASIN in the comment tag, so a file belonging to this book is reused and anything else gets ` [{asin}]` appended. Without this the second book silently overwrote the first (the live library has two *Red Rising* ASINs that collide).
- The finished book is **moved**, not copied, and the AAXC and voucher are deleted straight after decryption, which keeps peak disk at roughly one copy of the book rather than three.
- `download_books(audible, settings, progress=None, *, account_id=None, state=None, cancel=None, run_id=None)` - walks the waiting books of that account (the client is one marketplace's, so the queue must be that account's; `max_download` is therefore **per account per run**) until `settings.max_download` books have been **taken on**, and returns a `DownloadStats` NamedTuple `(attempted, succeeded, failed, unavailable, cancelled=False, aborted=False)`, which is what `run_pipeline` writes the run record from. **The circuit breaker:** `settings.max_consecutive_failures` refusals or failures in a row with no success between (`streak`, reset by a success) stops the run with `aborted=True`, checked at the top of each iteration so the cleanup of the book that tripped it still runs; `_outcome` records `SyncOutcome.ABORTED` with the reason in `error`. Found necessary live (2026-10-31): with refusals no longer using a slot, a day on which Audible refused every licence would otherwise have meant a licence request for all 288 queued titles. A slot is used by a book that downloads, fails, is lost to another run's claim, or the run is stopped on; **a licence Audible refuses uses none** - it is one POST and nothing was downloaded - so a row of refused titles at the head of the queue does not starve the books behind them (found live, 2026-10-31, when Audible had retired the oldest purchases to streaming only). **Run records:** with a `run_id` (which `run_account` passes), each book downloaded (with the filed audio's size, `_process_book` returning its path, and the seconds since the claim), failed or refused is written with `record_run_book` through `_record`, which logs and carries on when the write fails - a record is the Activity screen's detail and must never turn a downloaded book into a failure. A book handed back (credentials, cancel, another run's claim) is not recorded. `state.set_book(book, done=attempted, total=cap)` is called before each claim and `set_book(None, ...)` after the loop, so a poll sees the book in hand and the slots used against the cap. Each book is **claimed** (`claim_book_for_download`) before it is touched, one at a time rather than as a batch, so a scheduler tick starting mid-run skips a book another process holds and only books really tried spend an attempt. Three failure arms, and order matters - `LicenseError` subclasses `RuntimeError`, so its arm must sit above `except Exception`:
  - `Unauthorized`/`NoRefreshToken`/`AuthFlowError` **breaks** the loop and calls `release_book` first: every remaining book would fail the same way, and an expiring token must not burn a good book's attempts. It is not counted as a failed book; the error goes into `DownloadStats.credentials_error`
  - `LicenseError` calls `mark_book_unavailable(...)` - **not** a failure. The denial raises before any of the book is downloaded, so it costs one cheap POST, and the title may be offered again; the book is parked and reported separately from the failures
  - anything else calls `mark_book_failed(...)` with `settings.max_attempts`, which returns the book to the queue until the cap is reached and then fails it terminally
  The temp folder is always removed, and the run ends with a succeeded/failed summary.

**Final layout** comes from the `[naming]` templates in `config.ini`, rendered by `src/naming.py`:
- `folder` (default `{author}/[{series}/][{sequence} - ]{title}`) and `filename` (default `{title}`); `[...]` groups are dropped when any placeholder inside is empty, empty segments are skipped, every value is sanitized
- Defaults give `audiobooks/{author}/{series}/{sequence} - {title}/{title}.m4b`, or `audiobooks/{author}/{title}/{title}.m4b` without a series (`.oga` when `[encoding] format = oga`)
- Missing author → `Unknown Author`; empty folder or filename → ASIN; unknown placeholder → `ValueError` at startup (`validate_templates`, called from `Settings.__post_init__`)
- PDF, `{filename}_cover.jpg` and `{filename}_annotations.json` sit next to the audio file; the extension comes from `output_extension()` in `src/encoding.py`

### naming.py

Everything that turns book data into a path; see also the `[naming]` templates under Configuration.

- `sanitize_filename(name, fallback="Unknown")` - makes a single path segment safe on Linux, macOS, Windows and SMB. `:` becomes ` -`, `/` and `\` become `-`, other invalid characters are dropped, whitespace collapsed, Windows device names (`CON`, `NUL`, `COM1`, ...) get a trailing underscore, and the result is capped at 150 characters **and** 200 UTF-8 bytes. File systems limit a component in bytes, so a CJK title overflows long before the character cap. Use it for every title, author, series or sequence that becomes part of a path.
- `temp_book_folder(download_folder, asin, title)` - the per-book working folder `downloads/{asin}_{safe_title}/`
- `book_template_values(book)`, `render_template`, `validate_templates`, `book_output_paths` - the template engine. `{series}` and `{sequence}` come from `Book.primary_series` (see `model.py`), which is also what treats a sequence with no series title as empty, so a book never lands in a bare `{author}/2 - {title}/` folder. Both values must come from the *same* entry, or a two-series book files as `Dune/12 - Dune`.

### encoding.py

Everything format-specific that is not an ffmpeg flag. `FORMATS` maps `m4b`/`oga` to extensions; `write_m4b_extra_tags(path, metadata)` opens a finished M4B with mutagen and adds every metadata key outside `MP4_NATIVE_KEYS` as a `----:com.apple.iTunes:<key>` freeform atom (plus `stik=2` for audiobooks), returning the keys written; `validate_encoding(format, bitrate)` raises `ValueError` at startup for an unknown format (via `output_extension`, so the message is defined once) or a bitrate outside 1..256 kbps (libopus rejects more). `read_embedded_asin(path)` reads the ASIN back out of a finished M4B or OGA from the `comment` tag every output carries, which is how `_resolve_output_path` tells a retry of the same book from a different book with the same name. `image_info(bytes)` reads MIME, width, height and depth from JPEG (SOF marker) or PNG (IHDR) headers with the stdlib, returning zeros for anything else. `picture_block(path)` packs the FLAC-style picture block (type 3, MIME, "Cover Artwork", dimensions, data) and returns unwrapped base64 for the `METADATA_BLOCK_PICTURE` tag. `chapter_tags(chapters)` turns Audible chapters into `CHAPTER000=HH:MM:SS.mmm` / `CHAPTER000NAME=` pairs. No escaping here; `write_ffmpeg_metadata_file` escapes when writing.

### progress.py

Where download byte progress is reported. `_stream_to_file` used to open a `tqdm` bar itself, which is right for a terminal and wrong for everything else: the carriage returns land in the service log and a UI has nothing to read.

- `Progress` - a `Protocol` with `start(desc, total)`, `advance(amount)` and `finish()`. A protocol rather than a bare callback because a bar has a lifecycle: it has to be created with a total, advanced, and closed. `total` is `None` when the response carries no `Content-Length`, which is normal
- `NullProgress` - reports nothing, and is the default everywhere `progress` is omitted, so no call site needs to guard on `progress is not None`
- `TqdmProgress` - the CLI adapter, built with the same arguments as the bar it replaced so a terminal run looks exactly as it did. One instance drives one transfer at a time, and `start` closes any bar left open rather than leaking it
- `StateProgress` (in `runstate.py`) - the service's adapter, writing into a `RunState`

**`tqdm` is imported here and nowhere else**, so no library module depends on a terminal. Keep it that way.

## Configuration

`config/config.ini`:

```ini
[general]
debug = true            ; sets the log level to DEBUG

[sync]
; max-download = 10     ; limit books processed per run, 1 or more; unset = all waiting
; max-attempts = 3      ; tries before a book is marked failed, 1 or more; default 3
; max-consecutive-failures = 5 ; refusals/failures in a row before a run gives up; empty = never
; enabled = true        ; service only: run syncs on a schedule
; interval-minutes = 360 ; service only: how often, 5 or more
; auto-monitor-new = true ; queue a purchase the first time it is seen

[folders]
downloads = data/downloads
audiobooks = audiobooks

[encoding]
; format = oga          ; opt-in Ogg Opus re-encode; unset = m4b stream copy, no re-encoding
; bitrate = 64          ; kbps, oga only, 1-256

[notifications]
; webhook-url =         ; service only: http(s) URL posted to after each run
```

Read in `src/settings.py` with `configparser`, into a frozen `Settings`, and copied into the `settings` table on the first run only - after that the table is the live configuration (see `settings.py` above). Relative paths are anchored to the repo root, so the app can be started from any working directory. The config file is copied into the Docker image, so committed values become the image defaults for a fresh database. Users override by mounting `./config` (see `compose.yml`).

The **service process** is configured by environment variables, kept deliberately to what is about the process rather than about syncing: `AUDIBLE_SYNC_HOST`, `AUDIBLE_SYNC_PORT`, `AUDIBLE_SYNC_API_TOKEN`, `AUDIBLE_SYNC_CORS_ORIGINS`, `AUDIBLE_SYNC_DEBUG` (see `service.py`), plus `AUDIBLE_SYNC_SECRET_KEY` / `AUDIBLE_SYNC_SECRET_KEY_FILE`, which the CLI reads too (see `crypto.py`).

## Development

### Setup

```bash
git clone https://github.com/alexstansfield/audible-sync.git
cd audible-sync
uv sync                       # installs runtime + dev tools (pytest, ruff); pip users: pip install -r requirements.txt
uv run python -m src.main login --marketplace uk   # a code to enter at amazon.co.uk/code
uv run python -m src.main
```

**Dependencies:** `pyproject.toml` lists only direct dependencies (`audible`, `httpx`, `tqdm`, `mutagen`, `fastapi`, `uvicorn`) plus a `dev` group. **`audible` must be ≥ 0.13.1**: from about 2026-10-21 Audible refuses download licences (`Denied`, "License not granted to customer") to devices registered with the library's older profile, and 0.13.1 registers new devices with the one that is granted (mkb79/Audible#909). A registration made before that stays refused and has to be replaced - see `replace_account_auth`. After changing it run `uv lock` and regenerate `requirements.txt` with the export command shown in the tree above. The Dockerfile installs with `--no-dev`.

FFmpeg must be on PATH (`brew install ffmpeg` / `apt install ffmpeg`).

### Workflow

1. Branch from `dev`
2. Make changes **with unit tests** (see Testing below)
3. Run `uv run ruff format .`, then `uv run ruff check .` and fix anything it reports
4. Run `uv run pytest` and make sure it passes
5. Run through the manual integration checklist for anything touching the download pipeline
6. Commit and open a PR against `dev`; CI runs the same three commands and must be green

### Linting and formatting

**Tool:** ruff, for both linting and formatting. Configuration lives in `pyproject.toml` under `[tool.ruff]`.

```bash
uv run ruff format .          # format in place
uv run ruff check .           # lint
uv run ruff check --fix .     # lint and apply safe auto-fixes
```

Settings: Python 3.12 target, 120 character lines, `src` treated as first-party for import sorting. Enabled rule groups: pycodestyle (`E`, `W`), pyflakes (`F`), isort (`I`), pyupgrade (`UP`), bugbear (`B`), comprehensions (`C4`), simplify (`SIM`) and ruff's own rules (`RUF`).

Rules of thumb that follow from this config:
- Optional parameters are annotated `T | None`, never a bare `T = None`
- Compare with `is None` / `is not None`, not `== None`
- No bare `except:`; catch the narrowest exception the handler actually deals with
- Let the formatter own line wrapping; don't hand-format around it
- The whole tree is checked, including `tests/`, so tests follow the same rules

The codebase was brought to zero ruff findings in one pass; keep it there rather than accumulating exclusions.

### Testing

**Framework:** pytest. Tests live in `tests/`, one file per module (`tests/test_downloader.py`, `tests/test_database.py`, ...). Run with:

```bash
uv run pytest            # whole suite
uv run pytest -k sanitize   # subset
```

**Unit tests are required, not optional.** Every change that adds or modifies behaviour must come with tests:
- New function or method → tests for the happy path and the edge cases
- Bug fix → a test that reproduces the bug and now passes
- Refactor → existing tests still pass; add tests first if the code had none

**How to test this codebase:**
- Prefer pure functions. `sanitize_filename`, `generate_metadata`, `write_ffmpeg_metadata_file`, everything in `encoding.py` and the path-building logic are directly testable with no setup. `write_m4b_extra_tags` needs a real MP4: copy `tests/fixtures/silence.m4b` to `tmp_path` and read it back with `mutagen.mp4.MP4`.
- For `decrypt_aaxc`, monkeypatch `downloader.subprocess.run` with a recorder (see the `fake_ffmpeg` fixture) and assert on the argv and on the FFMETADATA file, which the recorder must read before `decrypt_aaxc` deletes it. The fixture also replaces `downloader.write_m4b_extra_tags` with a recorder, since the fake ffmpeg produces no file for mutagen to open.
- Never hit the Audible API, the network or FFmpeg in tests. Mock `Audible`/`Downloader` methods and `subprocess.run` with `unittest.mock` or `monkeypatch`.
- For database tests point `src.database.DB_FILE` at a temp file (`tmp_path` fixture) and call `init_db()`.
- For `download_books`, patch `get_books_to_download`, `claim_book_for_download`, `mark_book_downloaded`, `mark_book_failed`, `release_book`, `Downloader.download_book` and `decrypt_aaxc`, then assert on the resulting files and calls (see `_patch_pipeline`). All of these are looked up as attributes of `src.downloader`, so patch them there - miss one and the test writes to the real library database. `_patch_pipeline` takes optional `claimed=`, `failures=` and `records=` recorder lists, and always patches `record_run_book`. It takes `(audible, settings)`; build the settings with `make_settings(download_folder=..., audiobook_folder=...)`. This is how the per-book error handling was verified. `Downloader.download_book` takes `(book: Book, temp_dir)` and returns a `DownloadedBook`.
- For progress, pass a recorder implementing `start`/`advance`/`finish` (see `RecordingProgress` in `tests/test_downloader.py`) and a fake streaming response whose `num_bytes_downloaded` advances as chunks are yielded, because that counter - not `len(chunk)` - is what `_stream_to_file` takes its deltas from. For `TqdmProgress`, monkeypatch `src.progress.tqdm`.
- For `run_pipeline`, patch `start_sync_run` and `finish_sync_run` as attributes of `src.main` alongside the rest; `tests/test_main.py`'s `_patch_pipeline` takes `synced=` and `stats=` which may be exceptions, so one helper drives both failure arms. Its fake `download_books` accepts `state=` and `cancel=` and records the state's snapshot, which is how the stage reporting is asserted.
- For the API, `tests/test_api.py` builds the app with `create_app(scheduler=FakeScheduler(), state=RunState(), api_token=..., version="test")` and a `db` fixture pointing `DB_FILE` at a temp file, then uses `fastapi.testclient.TestClient` **without** the context manager so the lifespan (and the scheduler) is not started; one test uses `with TestClient(app)` to assert the lifespan starts and stops the scheduler. `httpx2` is in the dev group because Starlette's TestClient now needs it.
- For the scheduler, everything is injected: `Scheduler(load_settings=..., run=FakeRun(), state=..., last_run_start=lambda: ..., now=lambda: NOW)`. `FakeRun` sets `started`/`finished` events and can block until released or cancelled; call `_settle(scheduler)` before re-triggering after a run, because the fake signals `finished` a moment before the scheduler flips `running` off. The fixture stops every scheduler it made.
- For cancellation in `download_books`, set the event from inside the fake `download_book` (or before the call) and assert on `release_book`, the claims made, and `stats.cancelled`. For `_stream_to_file`, yield the chunks from a generator that sets the event between them.
- `tests/test_apitoken.py` drives `resolve_api_token` and `ApiToken.regenerate` against its own `db` fixture. `tests/test_system.py` gives `probe_ffmpeg` a fake runner returning canned `-version`/`-encoders` text and `disk` a fake `usage`; the API tests patch `system.ffmpeg` and `system.shutil.disk_usage`, and expect `key_source` `env` because the autouse fixture sets `AUDIBLE_SYNC_SECRET_KEY`. The regenerate tests sign in first (`user` + `_sign_in`); one checks the old token with a fresh `TestClient` - the `api` client's jar would otherwise send a cookie.
- `tests/test_service.py` exercises `ServiceConfig.from_env` with explicit dicts and `build()` with `configure_logging` and `seed_settings_from_ini` patched on `src.service`.
- The secret key: `tests/conftest.py` has an **autouse** `secret_key` fixture that configures a fresh key per test, puts it in `AUDIBLE_SYNC_SECRET_KEY` and moves `crypto.DEFAULT_KEY_FILE` under `tmp_path`, so nothing a test does can read or write a key file in the repository and `add_account` always has a key to encrypt with. Reconfigure with `crypto.configure(crypto.generate_key())` to play the wrong key; `tests/test_main.py`'s ordering tests patch `load_secret_key`, `crypto.configure` and `encrypt_stored_secrets` on `src.main`. `tests/test_crypto.py` drives `load_secret_key` with explicit `env` dicts and checks the generated file's mode.
- Book management: `tests/test_library.py` files real files under a temp `audiobook_folder`; for the legacy-path lookup it copies `tests/fixtures/silence.m4b` and sets `©cmt` to `ASIN: ...` with mutagen, exactly what `read_embedded_asin` reads. The transitions are tested against the `db` fixture; `refresh` with `Audible` and `authenticator_for` patched on `src.library`. The API tests patch `src.api.library.refresh` and drive the file actions with real files under a folder set through `PUT /api/settings`.
- Extras: `tests/test_logbuffer.py` uses its own non-propagating logger at DEBUG so pytest's capture is not involved; `tests/test_notify.py` drives `send_webhook` with `httpx.MockTransport` and patches `src.notify.send_webhook` for the wrapper; the API tests patch `src.api.send_webhook` and attach the app's `log_buffer` to a test logger.
- The bulk retry: `tests/test_database.py` drives `requeue_unavailable` with `_set` for the states the public API cannot reach, `tests/test_accounts.py` asserts `replace_account_auth` requeues only that account's consumable parked books, and the API tests park books with `claim_book_for_download` + `mark_book_unavailable`
- The web sign-in: `tests/test_webauth.py` drives sessions with an explicit `now=` and the throttle with a `Clock` object, against its own `db` fixture. The API tests' `user` fixture creates `admin` with a known `PASSWORD`; `_sign_in(client)` posts it, and a second `TestClient(api.app)` is a second browser with its own cookie jar (the `api` client's jar also sends its cookie with bearer requests, so a test about the token alone uses a fresh client). A non-ASCII bearer header has to be passed as bytes - httpx refuses a `str`. scrypt costs about 50 ms a hash, so keep hashing out of loops
- Login: `tests/test_audible_login.py` fakes Amazon's two auth endpoints with `FakeAmazon` on `httpx.MockTransport` (import it, `PENDING`, `REJECTED` and `registered_response()` into other tests rather than building another), checks the PKCE challenge in the URL against the kept verifier, patches `src.audible_login.register` to assert what `complete_login` sends and to drive a rejection, and drives `PendingLogins` with an injected clock, HTTP client and sleep - `join(login_id)` before reading a code login's outcome. The API tests patch `src.audible_login.complete_login` (the store calls it, not the route) and inject a `FakeAmazon` through the `api` fixture, which ends leftover polls at teardown. Never call the real `register` or Amazon in a test.
- Accounts: `make_book` fills in `id` (from a counter) and `account_id` (`ACCOUNT_ID`, 1) so a test book behaves like one the database returned; pass `id=None` for one straight from the API. `make_account()` builds an `Account` whose stand-in `auth` blob does not read as needing a login. The `db` fixture in `tests/test_database.py` creates account 1 and `_id(asin)` looks up a row id for the state-machine functions; `_asins(books)` in `tests/test_downloader.py` maps ids back to ASINs so fakes keep asserting on ASINs. `tests/test_main.py`'s `_patch_pipeline` takes `accounts=` and patches `get_accounts`, `authenticator_for`, `persist_auth_if_changed` and `mark_account_synced` on `src.main`. For a real `Authenticator` use `tests/test_accounts.py`'s `AUTH_BLOB`.
- For `Audible`, build items with a helper and pass a fake client that replays canned pages; never construct a real `Authenticator`. See `tests/test_audible_client.py`.
- For `Settings.from_db` and the seed, `tests/test_settings.py` has its own `db` fixture pointing `src.database.DB_FILE` at a temp file; write text with `database.save_settings` and read back with `Settings.from_db()`.
- `tests/conftest.py` holds the shared `make_book()` and `make_settings()` factories. `make_settings(**overrides)` is `dataclasses.replace(Settings(), **overrides)`, so an override that would not survive `from_ini` still raises; import it as `from tests.conftest import make_book` (`tests` is in ruff's `known-first-party`). It returns a real `Book` with decoded lists - never JSON strings, which is what the old row-tuple helpers built. Keep other fixtures small and inline.

The suite currently covers the sanitizer, metadata generation, the FFMETADATA writer, the ffmpeg argv for both formats, the encoding helpers, the per-book error handling in `download_books`, the progress seam, the run record, the sync cursor and its fallbacks, and the database layer. If you touch a module that has no tests yet, add the tests for the part you touched rather than for the whole module.

### Manual integration checklist

Service (a quick local version with no Audible access is a scratch database with an account whose `auth` is a made-up blob, and `build()` under `uvicorn.Server` in a thread - a triggered run then records `failed` within a second):

- [ ] `docker compose up` → `/api/health` 200 without a token, `/api/status` 401 without and 200 with the token, the scheduler shows `next_run_at`
- [ ] `POST /api/sync` → 202 and `/api/status` shows the stage, the book and the bytes moving; a second `POST` → 409
- [ ] `POST /api/sync/cancel` during a download → the run is recorded `cancelled`, the book is back in `waiting_download` with `attempts` unchanged and its temp folder removed
- [ ] `docker stop` during a download → clean exit within the grace period, claim released
- [ ] `PUT /api/settings {"sync_interval_minutes": 5}` → `next_run_at` moves without a restart; a bad bitrate → 422 carrying the validator text
- [ ] The first service start seeds the settings table from `config.ini`; a later edit to the file is *not* picked up
- [ ] The API token survives a restart when not set in the environment
- [ ] The first start generates `secrets/secret.key` (0600) and the log says so; `accounts.auth` and `settings.api_token` read `enc:...` in the database; a restart with the same key reads them back; a start with `AUDIBLE_SYNC_SECRET_KEY` set to another key shows the account as `needs_login` with one ERROR line and a new API token, and the original key restores both; a database from the previous version has its plaintext rows rewritten on the first start (`Encrypted N stored secret(s)`)
- [ ] A database from before accounts migrates on first start: one placeholder account showing `needs_login`, every book attached to it, the cursor and queue unchanged; a code login with its `account_id` fills it in and the next run syncs it (the migration itself was checked against a copy of the live database on 2026-09-12)
- [ ] A second marketplace added with its own login syncs as its own account with its own run rows; `max-download` applies to each account's run
- [ ] `PATCH {enabled: false}` skips the account on the next run; an account with `needs_login` is skipped with a warning
- [ ] `GET /api/logs` shows the lines the run just logged; `GET /api/stats` totals match `GET /api/books`; with `webhook_url` set, a run posts `sync.finished` to it (a `python -m http.server` with a tiny handler will do) and a receiver that is down only produces a warning
- [ ] `GET /api/books?q=` finds a book by title, author and series; paging totals match. `POST .../delete-files` on a downloaded book removes its files and prunes the folder, and the book is not fetched again; `.../redownload` fetches it and files it at the same path; `.../delete-files` on a book downloaded before `file_path` was recorded still finds its file
- [ ] `python -m src.main login --marketplace uk`: the printed code entered at `amazon.co.uk/code` on a phone adds an account within a poll interval, and the account syncs on the next run. Same through `POST /api/accounts/login` then `GET /api/accounts/login/{id}` turning `confirmed`; a code left unentered turns `expired` after ten minutes. With `--account 1` / `"account_id": 1` the existing account keeps its id and library and gets the new credentials, and a licence request is then `Granted`
- [ ] `--browser` / `"method": "browser"`: the printed address shows Amazon's sign-in form, the "page not found" address pasted back adds an account; a second paste of the same address is a 404
- [ ] After a run that downloaded one book and was refused another, `GET /api/sync/runs/{id}/books` lists them under `downloaded` (with the file's size and the time) and `refused`; a run from before the upgrade answers `recorded: false`
- [ ] `GET /api/queue` with `max-download = 2` and two enabled accounts lists each account's two oldest waiting monitored books, account by account; disabling an account takes its books out; a run's downloads match the list, except where Audible refuses a title (the run reaches further down) or the sync restored a withdrawn one (it takes a slot)
- [ ] `POST /api/settings/api-token/regenerate` from a signed-in browser (403 with the bearer token alone) → the old token is a 401 on the very next request and the new one a 200, with no restart; with `AUDIBLE_SYNC_API_TOKEN` set it is a 409. `GET /api/system` shows the FFmpeg version and `libopus: true` in the image, the library disk's free/total space, the key file's path (or `env`) and the database size (checked on a scratch server, 2026-11-02, on a host without FFmpeg: `ffmpeg_version: null`)
- [ ] `POST /api/books/bulk` with a `filter` equal to a `GET /api/books` query reports `updated` equal to that listing's `total` (less any skipped); `delete_files` on a mixed selection removes the files of the downloaded books and skips the rest as `no_files`
- [ ] After a re-login with `account_id`, the account's `unavailable` books are back in `waiting_download` (the log says how many) and `POST /api/books/retry-unavailable` on an account with none left answers `requeued: 0`
- [ ] The first start logs `Web app sign-in: user admin, password ...` once and a restart does not; signing in (`curl -c jar`) makes `/api/status` 200 by cookie as well as by token; a sixth wrong password from one address is a 429; a password change signs out a second cookie jar but not the first; `reset-password` (`docker compose exec ... python -m src.main reset-password`) prints a new password and signs out both, with the service still running (checked on a scratch database, 2026-11-02)
- [ ] With a build in `web/dist` (or `AUDIBLE_SYNC_WEB_DIR`): the start logs `Serving the web app from ...`, `/` and a deep link such as `/library?status=failed` return `index.html`, an `assets/` file comes back with `immutable` caching, and `/api/nope` is a JSON 404; with none, the log says the API only and `/` is a 404
- [ ] The `opencollection/` folder opens in Bruno as a collection: with `apiToken` set in the `local` environment, Health and Status both answer 200
- [ ] A refused licence does not use a `max-download` slot: with the oldest queued titles refused, the run still downloads `max-download` books from further down the queue, and the refused ones stay `unavailable` across the next sync

Pipeline:

- [ ] Full sync on an empty database, and one `sync_runs` row with `outcome=success`, `finished_at` set and counters matching the log
- [ ] Incremental sync on an existing database: the `Fetching books purchased since ...Z` line shows the previous run's start minus an hour, in Audible's `Z` format
- [ ] A database written by the previous version gains `sync_runs` on first run, and its first sync still uses the `MAX(date_added)` fallback
- [ ] A run killed mid-download leaves a row still `running` with a NULL `finished_at`, and does not move the cursor
- [ ] The book that killed run had claimed is picked up again once `STALE_CLAIM_SECONDS` has passed, and is *not* picked up before then
- [ ] A run where a book failed records `partial`, and the next run's cursor still advances
- [ ] `PRAGMA journal_mode` reads `wal`
- [ ] The progress bar still renders for the AAXC, PDF and cover, and the AAXC bar carries the book title
- [ ] Download with and without `max-download`
- [ ] Series and non-series books land in the right folders
- [ ] A title with `:` or `/` produces a sane path
- [ ] A failing book is skipped, its temp folder removed, and the run continues
- [ ] A book that fails `max-attempts` times is marked `failed`, with `last_error` set, and is not picked up on the next run
- [ ] A withdrawn Plus title syncs as `unavailable`, never enters the queue, and returns to `waiting_download` on a sync once Audible offers it again
- [ ] A second run started while the first is downloading skips the in-flight book rather than duplicating it
- [ ] Cover, chapters and metadata visible in the M4B (e.g. `ffprobe -show_format`), including `series` and `series-part` for a series book
- [ ] With `format = oga`: `ffprobe` shows an `mjpeg (attached pic)` stream (decoded from `METADATA_BLOCK_PICTURE`), the chapter list once with no duplicates, and `comment=ASIN: ...` among the audio stream tags (`-show_streams`, Ogg tags are stream-level); the DB row has `encoding_format` and `downloaded_at`
- [ ] Docker image builds and runs

### Debugging

```bash
# Inspect the database (sqlite3 CLI may not be installed; Python works)
uv run python -c "import sqlite3; c=sqlite3.connect('data/audible_sync.db'); print(c.execute('select status,count(*) from library group by status').fetchall())"

# Shell in the container
docker compose run --rm -it audible-sync bash
```

## Docker

- `Dockerfile`: `python:3.12-slim-trixie`, installs uv and ffmpeg, `uv sync --locked --no-dev`, `EXPOSE 8080`, an unauthenticated `HEALTHCHECK` on `/api/health`, `CMD uv run python -m src.service`
- `compose.yml`: publishes `8080`, passes `AUDIBLE_SYNC_API_TOKEN` and `AUDIBLE_SYNC_SECRET_KEY` through from the environment (empty means "generate one"), and `AUDIBLE_SYNC_SECURE_COOKIE` (for a reverse proxy serving https), mounts `./data`, `./secrets` (the key file, to be backed up separately from `./data`), `./audiobooks`, optional `./config`; `stop_grace_period: 2m` to match `service.STOP_TIMEOUT`
- Run with `docker compose up -d`; `restart: unless-stopped` is right for the service. A one-shot run is `docker compose run --rm audible-sync uv run --no-sync python -m src.main`
- `.dockerignore` excludes `data/`, `audiobooks/` (gigabytes), `venv/`, `test/`, `secrets/` and `opencollection/`, so a local `docker compose build` from the repo root sends only the code. A local test setup lives in `test/` (gitignored): a compose file with `build: ..` and its own `config/` mount.
- Image: `ghcr.io/alexstansfield/audible-sync:latest` and `:vX.Y.Z`, published on `v*` tags

## Continuous Integration

Two workflows in `.github/workflows/`:

- `ci.yaml` runs on every push and pull request to `dev` and `main`: installs with `uv sync --locked`, then `ruff check .`, `ruff format --check .` and `pytest`. A PR is not mergeable until it passes.
- `docker-publish.yaml` runs on `v*` tags: builds the image, pushes `latest` and the version tag to ghcr.io, and creates a GitHub release.

## Code Conventions

- `snake_case` functions, `PascalCase` classes, `_leading_underscore` for private helpers
- Import order: standard library, third-party, local (`from src.x import ...`)
- Use `logging.getLogger(__name__)`; never `print` - except the CLI `login` command's output meant for the person at the terminal (the code to enter, the outcome), which is `print`/`echo=print` on purpose: it is not a log line and timestamps would only get in the way
- Type hints and docstrings on new public functions
- Anything that becomes a path goes through `sanitize_filename`
- Failures inside per-book processing should raise; `download_books` handles them
- Every behaviour change ships with pytest unit tests (see Testing)
- Code is ruff-formatted and ruff-clean before commit (see Linting and formatting)

## Known Issues and Cleanup Candidates

- `requirements.txt` is generated from the lockfile and will drift if `uv lock` runs without re-exporting
- Every module now has a test file, but the suite is still thickest around `downloader.py` and `database.py`
- Two books whose templates render to the same name are filed side by side (` [{asin}]` suffix) rather than merged; the naming template is what actually needs disambiguating
- `list_books`' search is a `LIKE` over JSON text: fine for a personal library, not an index. If the library grows into the thousands, FTS is the next step

## Roadmap

See `todo.md` for the authoritative list.

**Milestone 1 - complete:** sync, download, decrypt, config, Docker, CI.

**Milestone 2 - complete:** logging, PDF/cover/annotations, metadata and chapter embedding, configurable file naming, and Ogg Opus encoding with a bitrate setting. Async download progress is still listed under it in `todo.md` but only pays off with a web UI; recommended to move to Milestone 3.

**Milestone 3 - complete (2026-09-12):** settings table; the service runtime (scheduler, run state, cancel, status/sync/runs/settings endpoints, bearer token, Docker runs the service); accounts (one per marketplace login, credentials in the database, per-account pipeline and endpoints); the browser login flow; book management (listing, monitor, delete, redownload, retry, refresh, cover); and the extras (logs, webhook, stats).

**Milestone 4 - next:** the web app. The API it needs is in place: polling `GET /api/status` for progress (SSE is an option if polling proves too coarse), the bearer token until the app's own login replaces it, and `AUDIBLE_SYNC_CORS_ORIGINS` for a dev server. **Step 0 is complete** (2026-09-09), including the smaller items folded in with it.

## Understanding "Sync"

"Sync" means keeping the local audiobook collection in step with the Audible library: metadata into the database, then files onto disk. Both happen in one run. Milestone 3 turns this into a scheduled background service with a manual trigger.

---

**Document Version:** 4.19
**Last Updated:** 2026-11-01
**Codebase Version:** Milestones 2 and 3 complete (background service with scheduler, run state and cancellation; bearer-token API for status/sync/runs/settings/accounts/login (device code, browser fallback)/books/logs/stats; library keyed per account; credentials and API token encrypted at rest with a key outside `data/`; webhook after each run; the Docker image runs the service)
**Primary Branch:** `dev`
