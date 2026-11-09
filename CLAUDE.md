# CLAUDE.md - AI Assistant Guide for Audible-Sync

What the code cannot tell you: decisions, gotchas and how to test. For what a function does, read the function; for the API, `src/schemas.py` and `docs/api.md`.

## Quick reference

| Aspect | Details |
|--------|---------|
| **Language** | Python 3.12, uv (`pyproject.toml` + `uv.lock`); `requirements.txt` is generated |
| **Entry points** | `python -m src.service` (the service); `python -m src.main` (one sync and exit, plus `login` and `reset-password`) |
| **Database** | SQLite (`data/audible_sync.db`), which also holds the live settings |
| **Secrets** | Encrypted in the database; the key in `secrets/secret.key` or `AUDIBLE_SYNC_SECRET_KEY` (`src/crypto.py`) |
| **Backend** | FastAPI + uvicorn; `src/api.py` puts together the routes of `src/api_*.py`, one module per area; `src/schemas.py` the contract |
| **Web app** | `web/`: Vite, React 19, TypeScript, TanStack Query, Biome, Vitest; served by the service from `web/dist`. Spec in `docs/design/DESIGN.md` |
| **Checks** | `uv run ruff format .`, `uv run ruff check .`, `uv run pytest`; in `web/`: `npm run lint`, `typecheck`, `test`, `build`, and `npm run gen:api` after an API change |
| **CI** | The same on every push and PR to `dev` and `main`, plus a stale-types check on `web/openapi.json` and `web/src/api/schema.d.ts`. Images are built on `v*` tags |

**Purpose:** sync an Audible library, download the books a user owns and decrypt them to M4B or Ogg Opus with metadata, cover and chapters. It does NOT crack DRM. Inspired by [BALD](https://github.com/damajor/BALD), using the same `audible` library.

**State:** Milestones 1-4 are complete, released as v3.0.0. `todo.md` is the roadmap.

## Layout

```
src/        flat, no __init__.py, run as modules, absolute imports (from src.x import y)
tests/      one file per module; fixtures/ has silence.m4b and naming_cases.json (shared with web/)
web/        the web app, see "Web app"
docs/       user docs; design/ has the web app's spec and mockups; manual-testing.md the hand checks
config/     config.ini, the defaults of a fresh database
secrets/    the key file (gitignored, its own mount, backed up apart from data/)
test/       a local Docker setup (gitignored as /test/, anchored, or it hides web/src/test/)
opencollection/  the API as a request collection for Bruno and the like
```

Modules: `service` (wiring) → `api` (→ `api_auth`, `api_sync`, `api_settings`, `api_system`, `api_accounts`, `api_books`) + `schemas`, `scheduler` + `runstate` → `main.run_pipeline` (also the CLI) → `settings`, `audible_client`, `sync`, `downloader` (→ `naming`, `encoding`, `progress`). Beside those: `accounts` (credentials), `audible_login`, `library` (per-book actions), `webauth`, `apitoken`, `crypto`, `database`, `model`, `paths`, `system`, `logbuffer`, `notify`, `openapi_export`.

**Import rules, which exist to avoid cycles:**

- `settings` must never import `downloader` (so `validate_max_download` lives in `settings`)
- `crypto` is a leaf and must never import `database` (so `encrypt_stored_secrets` lives in `database`)
- `database` must never import `settings` or `accounts` (so its accounts migration only creates a placeholder, which a login fills in)
- `apitoken` is its own module because `api` needs `regenerate` and cannot import `service`
- The `api_*` modules never import `api`, which includes them: each exposes a function that builds its router from what it is given (scheduler, run state, token), and a route patched in a test is patched on its own module (`api_accounts.authenticator_for`, `api_settings.send_webhook`)
- The client module is `audible_client.py`, not `audible.py`, which would shadow the dependency. Do not bring that name back
- `tqdm` is imported in `progress.py` and nowhere else

**Paths:** never build one from a relative string; use `paths.resolve_path`. It deliberately does not `resolve()`: the audiobook folder is often a bind mount or a symlink to a share. Anything that becomes a path goes through `sanitize_filename`, which caps bytes as well as characters, since file systems limit a component in bytes.

## What Audible does

All observed; the API is undocumented.

- **Device registrations go stale.** From about 2026-10-21 Audible refuses download licences (`Denied`, "License not granted to customer") to devices registered with older profiles. `audible` must be ≥ 0.13.1, `audible_login.IPHONE` mirrors its profile (change them together), and an old registration has to be replaced by a login. That is why there is no auth-file import: an `audible-cli` file carries an old registration
- **Withdrawn Plus titles.** A title added while included in Plus stays in the library, stops being licensable (`customer_rights.is_consumable` false) and may come back. So `unavailable` is **not terminal**, and `is_consumable` **fails open** everywhere: defaulting to false would park a whole library in one sync
- **A licence can be refused for a title the API calls consumable**, as with old purchases retired to streaming. Such a book stays parked until retried. `mark_book_unavailable` leaves `is_consumable` alone: writing 0 made the sync "restore" it every run, and two such books with `max-download = 2` blocked everything
- **Series order is not stable**, not even between two books of a series in one response, and titles get typos ("His Dark Materialsik"). Read `Book.primary_series`, **never `series[0]`**. The rule, in `_series_sort_key`: lowest sequence, then title, then series ASIN; an entry with no title is never a candidate, or a book files under a bare `{author}/2 - {title}/`
- **Podcasts:** following a show adds one listing item, `content_delivery_type` `PodcastParent`, `content_type` `Podcast`, no length and no audio; episodes (`PodcastEpisode`) only come back with `parent_asin`. Unfollowed, it leaves the listing (`status` `Revoked`) but `library/{asin}` still describes it. Real books can have `content_type` `Performance`, so match the podcast values rather than allowing only `Product`. Downloading episodes would be a feature of its own, and whether they license as AAXC is unknown
- **`date_added` stays text**, stored and compared as Audible wrote it. **`purchased_after` wants the `Z` form**, and `database._utcnow` writes `+00:00`: `_api_timestamp` is required. It filters on Audible's clock, hence the hour of `CURSOR_OVERLAP`
- **Timeouts:** a full page of 300 titles took 16.5 s, so `audible.Client` gets 60 s, not its default 10. Downloads use 30 s connect and 120 s read; httpx's default 5 s killed part-finished downloads on a CDN stall
- **A denied licence is a 200** with no `content_metadata`; `get_license_response` raises `LicenseError`. The correct quality for AAXC is `"High"`
- **The cover goes through the unauthenticated client:** the audible session signs every request, which would hand the ADP token to the image CDN
- **Amazon's device list:** a device exists only once `/auth/register` is collected. A code entered after we stopped polling shows "Device connected" and creates nothing (checked 2026-11-05). `customer_info.user_id` is stable across registrations (compared a year and two methods apart)
- **Chapters nest:** `flatten_chapters` descends, or a multi-part book gets a few hours-long "Part One" markers
- **Validation:** the library checks credential fields on assignment (`Atna|`, `Atnr|`, PEM, `adp_token` shape). `tests/test_accounts.py`'s `AUTH_BLOB` is the smallest dict that passes; reuse it

## The pipeline

- **`run_account` owns the run record:** the row is opened before Audible is touched and closed on every exit, and `state.end()` comes after that, so a poll never sees "nothing running" beside a row still in flight. A row left `running` is a killed run. A webhook never fails a run
- **Outcomes:** `partial` means the sync completed and the downloads did not all succeed; `failed` that the sync itself raised; `cancelled` someone stopped it; `aborted` it stopped itself. All but `failed` count as the **cursor**, which is the run's *start*, so anything added while reading is picked up next time
- **One account failing does not stop the others.** The first error is raised again at the end so a CLI run exits non-zero. Each account is read again at its turn, since the list can be hours old
- **`device_in_use`:** a run marks the account before reading it, and a login that replaces its credentials then stores them at once and deregisters the old device only when the run ends; deregistering at once ended the run `aborted`. The run stays on the old device meanwhile, which may be why they signed in, so its refusals park books after the login's requeue: leaving `device_in_use` queues them again. A later token write-back cannot overwrite the new device either (`replace_account_auth_if_unchanged`). In memory, so a CLI login beside the service is not covered
- **Claims:** `get_books_to_download` and `claim_book_for_download` must agree on "abandoned" (`STALE_CLAIM_SECONDS`), or a killed run's book stays `downloading` forever. The queue offers generously; the claim decides, book by book. A claim is a token that every outcome (`mark_book_*`, `release_book`) must present, and it is cleared with the outcome: "abandoned" is a guess from the clock, and a run still alive past it would otherwise overwrite the outcome of the run that reclaimed its book. Tests that only need a downloaded book use `conftest.mark_downloaded`
- **`mark_book_failed` decides inside the UPDATE**, from the attempts the claim counted, so nothing can race a read. `max_attempts` has no "unlimited": retrying forever is the bug it exists to fix
- **The failure arms in `download_books` are ordered:** `LicenseError` subclasses `RuntimeError` and must sit above `except Exception`. Credentials errors and cancels **release** the book without charging an attempt, and stop the run
- **A refused licence uses no `max-download` slot** (one POST, nothing downloaded), or refused titles at the head of the queue starve the rest. So `max_consecutive_failures` is the breaker: one bad day would otherwise mean a licence request per queued book
- **The accessory contract:** the PDF, cover and annotations methods return `False` only for genuinely absent; anything else raises and the book is retried, rather than filed complete with a NULL that nothing would ever fix
- **`update_books`** never writes `date_added` (the cursor fallback), the retry columns, the file paths or `monitored` on conflict, and moves `status` only between `waiting_download` and `unavailable`. Its count comes from `SELECT COUNT(*)` around the write, because `rowcount` after `executemany` is -1. Keep the explicit column list
- **The full re-read** only happens while `needs_consumability_refresh` says something is parked; a withdrawn book already downloaded does not count, or it stays on for good
- **`_remove_stored_podcasts`:** a podcast stored before they were left out has nothing marking it but no length, so each such undownloaded row is confirmed with Audible before it goes. It runs before the sync counts as done, so nothing it raises gets out, and the first failed check ends it until the next sync
- **Scheduler pacing** counts `_last_attempt` as well as the run history: from history alone, a pass recording no row ran back to back, 143,000 times in three minutes on a fresh install. `last_run_start` is the newest run whatever became of it, so a failing setup waits its interval and a restart does not sync at once
- **Stopping:** the sync and an ffmpeg pass cannot be interrupted, hence `service.STOP_TIMEOUT` 90 s and `stop_grace_period: 2m` in `compose.yml`. Cancel is checked between chunks and before each claim
- **`RunState` is a snapshot**, not an event stream, so a client polling at any rate gets the current picture with nothing to replay. `run_pipeline` configures nothing on import; the progress bar is injected from the CLI, so a host process is not surprised

## Files and encoding

- **FFmpeg's MP4 muxer drops** `series`, `series-part`, `author` and `media_type`, so `write_m4b_extra_tags` adds them as freeform atoms after it. Do **not** use `-movflags use_metadata_tags`: it keeps the keys but removes the embedded cover (verified 2026-09-08)
- **`-map_chapters` only when there are chapters:** a chapterless metadata file discards the track the AAXC carries. Opus has no picture stream or chapter track: cover as `METADATA_BLOCK_PICTURE`, chapters as `CHAPTERxxx` tags, and `-map_chapters -1` so they are not doubled
- **FFMETADATA newlines** are a backslash and a real newline; writing `\n` as two characters reads back as a literal `n`. A null sequence needs `or ""`, because `_prepare_book` always creates the key and it would be written as `series-part=None`
- **Every output carries its ASIN** in the comment tag, which is how `_resolve_output_path` tells a retry from another book with the same name (two *Red Rising* ASINs collide) and how `library.audio_path_for` finds a file from before `file_path` was recorded
- **The cover's extension comes from its bytes**, not the URL. The book is moved, and the AAXC deleted straight after decrypting, keeping peak disk at about one copy
- **`.part` files:** `_stream_to_file` writes one and renames on completion; a failed transfer leaves it for the temp-folder clean-up

## Settings

`config.ini` is copied into the `settings` table **once**, and the table is the truth from then on; later edits to the file are deliberately not read. The database layer is typeless text; `settings.py` parses and validates.

- A new setting needs `_PARSERS`, and validation goes in `__post_init__`, so no route in can build settings that fail mid-run. A test pins `_PARSERS` to `DB_SETTING_KEYS`. Unknown keys are ignored, so a newer database still reads
- `api_token` and the `web_*` keys share the table and `from_db` ignores them, so they never appear in `GET /api/settings`
- `null` in `PUT /api/settings` is a 422 except for the three optional fields: stored, it is an empty value `from_db` cannot parse, locking the API out of its own settings
- Process-level values only are env variables (`AUDIBLE_SYNC_*`, see `docs/configuration.md`). What to sync lives in the table

## Security decisions

- **The threat for encryption at rest is the database leaving the host** (backup, bug report), not an attacker on it: the blob makes its holder that Amazon device. So the key never lives under `data/`. Losing the key costs a login, not data: `WrongKey` reads as `needs_login`, logged once, without touching the blob, so the right key brings it back
- **The order in `service.build` matters:** logging, key, secrets, token, web user, and only then the `RingBufferHandler`, so a generated token or password reaches the console alone and can never be read through `GET /api/logs`. Each is logged once, in full, only on the start that generates it
- **`POST /api/settings/api-token/regenerate` wants a session cookie**, so a leaked token cannot rotate itself and lock its owner out. An env token refuses, since it would be overridden on restart
- **`reset-password` refuses when the password does not decrypt under its key:** a CLI holding another key than the service would lock everyone out
- **Web sign-in:** scrypt from the stdlib (no compiled dependency), and the hash encrypted too. `verify` always runs one hash and compares both fields so neither the answer nor the timing says which was wrong. `LoginThrottle` counts *before* checking, which caps hashes in flight per client at five: counting after let a parallel burst through, at 16 MiB of scrypt each. It keys on the socket's address, not a header anyone can set. Sessions store the SHA-256 of the cookie, so a database copy is not a session
- **CSRF:** `SameSite=Strict` plus JSON bodies. **Never `allow_credentials` with `*`**: Starlette echoes any origin, and another port on the host is the same site
- **`Secure` on the cookie** only when the request is https or `AUDIBLE_SYNC_SECURE_COOKIE`, since the service is often plain http on a LAN

## Accounts and login

- **An account is one marketplace login, not a person.** `library` is keyed `(account_id, asin)`; a credential-less placeholder (the migration's) is legal and skipped
- **One login, one account:** `store_login` gives the account holding the same `user_id` + marketplace the new credentials, since a second row would file every book again. The id is kept in the clear as `accounts.customer_id` with a partial unique index on it and the marketplace, so the rule holds across processes (a CLI login beside the service: a refused insert means look again) and for an account whose credentials cannot be read (a lost key: signing in restores it rather than adding another). Clearing credentials keeps it. A login not saying whose it is never matches. `backfill_customer_ids` fills it in at start (after the log buffer, so its warning reaches the Logs screen) and when an account is removed; of duplicates from before, the oldest gets it, the rest are left alone with a warning and may still sign in on their own row
- **`replace_account_auth` stores first and nothing raises after that**, then deregisters. Deregistering first left a failed store with two dead devices. It refuses another customer's login (`AnotherCustomer`) and another account's (`LoginAlreadyHeld`), leaving the working device alone; on any exception the caller discards the new device, or nothing would ever hold it
- **Requeueing:** the new device is the moment to retry licence-refused books, but `requeue_unavailable` skips titles Audible withdrew, which are a guaranteed refusal feeding the breaker
- **Device code is the default login**, because the browser flow depends on the user's browser (Amazon refused a desktop one on 2026-10-31). It is the protocol of mkb79/Audible PR #914; once released, the library's client can replace the three functions in `audible_login.py`
- **`PendingLogins` is in memory on purpose:** a login is a minute's interaction and nothing secret needs keeping. A registration answered after a cancel is discarded, and a login being stored (`_busy`) is waited for rather than called expired
- **`try_register`** treats 429, 5xx and a non-JSON 200 as "poll again"; only other answers fail the login

## API and serving

- Every endpoint is a plain `def`, so SQLite runs in the threadpool. Errors are `{"detail": ...}`. A settings `ValueError` is a 422, but pydantic's `ValidationError` is re-raised: a response that does not fit its schema must be a 500, not a misleading 422
- **Book rules (`library.py`):** a row a run holds is never touched (`BookBusy`, 409), files go before the row forgets them, and every transition goes through `reset_for_download`. Bulk goes book by book through the same functions so the rules stay in one place
- **The catch-all** is registered after the routers. It takes every method and uses `_methods_allowed`, so a typo answers the same with or without a build. It checks `resolve()` + `is_relative_to`, since Starlette hands it `../x` for `%2E%2E/x`
- **Base path:** one build works at any path, since the service rewrites `<base href="/">` in `index.html` (keep that tag as it is). Both proxy styles work (prefix stripped or passed on). A first segment matching one of the app's own pages is refused, because the two could not be told apart behind a stripping proxy; a test checks `_RESERVED_SEGMENTS` against `web/src/app/routes.tsx`
- **`next_run_queue` is a plan, not a promise:** refusals reach further down the queue, and a restored title can take a slot

## Database

- **Rows are read by column name**, through `Book.from_row`; never positionally, because migrations append columns in a different order from the DDL. Its hand-written `__repr__` keeps cover URLs out of log lines. `attempts`' default must match the column
- **State-machine functions take the row id, not the ASIN**, since one ASIN can be in two accounts. `get_book_by_asin` is a convenience for tests
- **SQLite foreign keys are off**, so `delete_account` deletes what it owns itself, and a run's book record reads `book_id` as None once the row has gone
- **Rebuilding `library`** for a new primary key is `_migrate_library_to_accounts`; the DDL is shared with `init_db` so they cannot drift. Index creation is guarded by the columns present, so an old database migrates
- **WAL** is a property of the file, so one `init_db` holds for every later connection
- **`list_books`' search** is a `LIKE` over JSON text: fine for a personal library, FTS if it ever reaches thousands

## Web app

- **Types come from the API.** `npm run gen:api` writes `web/openapi.json` and `src/api/schema.d.ts`; both are committed and CI checks them. Never hand-write a type the schema has. TypeScript stays on 5.x while `openapi-typescript` requires it
- **URLs:** requests follow `<base href>`, so never write an absolute `/api` URL; a cover is `api/books/{id}/cover`, relative
- **Theme:** `index.html` sets `data-theme` before first paint; `lib/theme.ts` and `useTheme` are the same rule, so keep them in step
- **CSS:** shared classes are global in `styles/controls.css`, anything else a CSS Module. A Module class and a global one tie on specificity, so a card needing other padding defines its own background and border rather than overriding `.card`
- **Polling, not events:** `useStatus` is the one polled query (2 s during a run, else 10 s). `useRefreshDuringRun` refetches when a run takes up a book *and* when that book's download starts, because the run names a book just before claiming it (found live: the Downloading filter stayed empty all download). A screen's query key should start with one of `AFTER_RUN`'s
- **Expiry of a login code is the service's word alone:** the countdown is display only, or a browser clock running ahead calls a good code expired. Copy buttons exist only where `navigator.clipboard` does, which excludes plain http on a LAN
- **Optimistic switches** are off for a row with a change in flight (`usePendingIds`), or a second change saves the first's optimistic value as the one to roll back to. Library selection survives paging but not a filter change, so an action never reaches books out of view; "Select all N matching" sends the filter, not ids
- **Library filters live in the URL.** Status chips count from `GET /api/stats`, so they count the library, not the search
- **Redownload and Delete files both confirm**, a step beyond the design: `library.redownload` deletes before fetching, so a refused licence would leave the book without files
- **A `running` row that is not `current_run` is Interrupted**, but only once `GET /api/status` has answered
- **Settings:** the form is what is saved overlaid with what was edited, so a save elsewhere never loses a change. Number boxes are `type="text"`, since a number input reads `5e` as `""`, which would save as no limit. Only one save runs at a time, as the service writes every setting on each. Leaving with unsaved changes asks, except on the way to `/login`, or a signed-out page would ask at every poll
- **The naming preview is a port of `src/naming.py`.** Both suites run `tests/fixtures/naming_cases.json`, whose values `src/naming.py` computed: change the module, regenerate the cases and port the change
- **Check each screen in a real browser too**, at desktop and 390px, in both themes, against the built image

## Workflow

Branch from `dev`; never work on it directly. Changes come with unit tests. Run the checks in the quick reference. For anything touching the download pipeline, or a screen, go through [`docs/manual-testing.md`](docs/manual-testing.md), and tick off what you check there. PRs go against `dev`.

After changing dependencies: `uv lock`, then `uv export --no-dev --no-hashes --no-emit-project -o requirements.txt`. FFmpeg must be on PATH.

**Python style:** ruff owns formatting and the tree stays at zero findings, tests included, with no exclusions. `T | None`, never a bare `T = None`. Catch the narrowest exception that the handler deals with. `logging.getLogger(__name__)`, and `print` only for what the CLI shows the person at the terminal. Type hints and docstrings on new public functions. Failures inside per-book processing raise; `download_books` handles them.

## Testing

Every behaviour change ships with tests; a bug fix with one that reproduces it. Break the fix to check the test notices. Never hit Audible, the network or FFmpeg: `tests/conftest.py`'s autouse `no_network` refuses every DNS lookup and fails the test at teardown, since best-effort code around Amazon would swallow the refusal alone.

**Shared:**

- `tests/conftest.py`: `make_book()` (fills in `id` and `account_id` like a row; `id=None` for one straight from the API), `make_settings(**overrides)`, `make_account()`. Import them as `from tests.conftest import ...`. Books hold decoded lists, never JSON strings
- The autouse `secret_key` fixture configures a fresh key per test and moves `crypto.DEFAULT_KEY_FILE` under `tmp_path`. Reconfigure with `crypto.configure(crypto.generate_key())` to play the wrong key
- Database tests point `src.database.DB_FILE` at a temp file and call `init_db()`; `tests/test_database.py`'s `_set` reaches states the API cannot

**By area:**

- **`download_books`:** `_patch_pipeline` in `tests/test_downloader.py`. Everything is looked up as an attribute of `src.downloader`, so patch it there: miss one and the test writes to the real library database
- **`run_pipeline`:** `tests/test_main.py`'s `_patch_pipeline`, whose `synced=` and `stats=` may be exceptions, and `accounts=`. Tests call `main([])`, since `parse_args(None)` would read pytest's argv
- **`decrypt_aaxc`:** the `fake_ffmpeg` fixture records the argv and must read the FFMETADATA file before it is deleted. `write_m4b_extra_tags` needs a real MP4: copy `tests/fixtures/silence.m4b`. For `read_embedded_asin`, set `©cmt` to `ASIN: ...` with mutagen
- **Progress:** a fake response's `num_bytes_downloaded` must advance, as `_stream_to_file` takes deltas from it, not `len(chunk)`
- **API:** `create_app(scheduler=FakeScheduler(), state=RunState(), api_token=ApiToken(TOKEN, "stored"), version="test")` with `TestClient` and **no** context manager, so the scheduler is not started. The client's jar sends its cookie with bearer requests too, so a test about the token alone uses a fresh client, and a second client is a second browser. A non-ASCII bearer header must be bytes. scrypt costs 50 ms, so keep it out of loops. `httpx2` is in the dev group because Starlette's TestClient needs it
- **Scheduler:** everything is injected. Call `_settle(scheduler)` before re-triggering, since `FakeRun` signals `finished` a moment before `running` flips
- **Login:** `tests/test_audible_login.py`'s `FakeAmazon` on `httpx.MockTransport` serves scripted answers (`PENDING`, `REJECTED`, `registered_response()`); import it rather than building another. `join(login_id)` before reading a code login's outcome. Never call the real `register`
- **Anything replacing credentials** patches `Authenticator.deregister_device` (`_deregistered` in `tests/test_accounts.py`)
- **Webhook:** `httpx.MockTransport` for the POST; `tests/test_main.py` patches it on `src.main`
- **`RingBufferHandler`** on a logger of its own with the level set: the default WARNING drops INFO before the handler sees it

**Web:**

- `src/test/fakeApi.ts` stubs `fetch` with routes keyed `"METHOD /path"` (`ok`, `fail`, `unreachable`, `json`, `signedIn()`, `api.sent(route)`); `src/test/fixtures.ts` builds typed data; `renderApp(path)` mounts the whole app on a memory router, which is how screens are tested
- jsdom lacks `<dialog>` methods and `matchMedia` (`src/test/setup.ts`). Escape is `fireEvent(dialog, new Event("cancel", {cancelable: true}))`
- Tests run in UTC but the machine's locale, so match times loosely (`/(18:00|6:00 PM)/`)
- `findBy` and `waitFor` wait 5 s (`setup.ts`), since a file's first render can pass a second on a busy runner; the test timeout stays above that, so a failure says what it was looking for. With fake timers use `vi.useFakeTimers({ shouldAdvanceTime: true })` and `userEvent.setup({ advanceTimers: vi.advanceTimersByTime })`

## Docker

- `.dockerignore` applies to a directory context only: `git archive HEAD | docker build -` sends everything in the tar. Build from `git archive HEAD` to catch a file git does not hold, but check an exclusion with a directory build
- Node is only in the build stage; `web/dist` is copied across. The healthcheck is the unauthenticated `/api/health`
- The config file is in the image, so committed values are the defaults of a fresh database

```bash
# Read the database (the sqlite3 CLI may be missing)
uv run python -c "import sqlite3; c=sqlite3.connect('data/audible_sync.db'); print(c.execute('select status,count(*) from library group by status').fetchall())"
```

## Known issues

- `requirements.txt` drifts if `uv lock` runs without re-exporting
- Two books rendering to one name are filed side by side with ` [{asin}]`; the template is what needs disambiguating
- Activity's offset paging can skip runs removed between pages; a cursor would need the API's help

## "Sync"

Means keeping the local collection in step with the Audible library: metadata into the database, then files onto disk, in one run, started on a schedule or by Sync now.

---

**Last updated:** 2026-11-06. **Primary branch:** `dev`
