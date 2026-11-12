# Todo

The roadmap. From Milestone 5 on, a milestone is split into the releases that ship it, each with its summary, requirements and tasks; the milestones before it are kept as they were done. A version number is settled when its release is cut, so a fix release in between or a change of order only renames a heading.

## Milestone 1: Library Sync, Download and Decrypt (v0.1.0)

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

## Milestone 2: Download Progress, Metadata and OGA Encoding (v1.0.0)

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

## Milestone 3: API Service (v2.0.0)

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

## Milestone 4: Web App Frontend (v3.0.0)

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
- [x] Web: Accounts with the code and browser sign-in flows
- [x] Backend: `DELETE /api/accounts/login/{login_id}` cancels a pending login (a code login's polling stops under the store's lock; the answer is the login as it ended, so a confirm that won the race is reported). The web app calls it when the sign-in dialog goes, by any way, or switches from the code to the browser sign-in, so a code abandoned there and entered afterwards no longer becomes an account, and a code plus a browser sign-in no longer adds the account twice. A closed tab or an ended session cannot cancel, so its code stays live until it expires
- [x] Web: Activity (runs, log)
- [x] Web: Settings
- [x] Design round 2: first-run dashboard, empty and no-result library, Accounts dialogs (rename, remove, sign in again, code expired, sign-in failed), book downloading and unmonitored, phone layout, common patterns, 404, service unreachable, forgot password
- [x] Backend: `POST /api/books/bulk` (monitor, unmonitor, retry, delete files by ids or by filter; updated and skipped counts)
- [x] Web: Library bulk selection (grid and table), action bar, delete confirmation, result message
- [x] Backend: count the unavailable books Retry unavailable can requeue (licence refusals, not titles Audible withdrew) in `GET /api/stats`, so the Dashboard and the Library stop offering a retry that only says "Nothing to retry"; and monitored counts per status, so the Library's chips match a `monitored` filter
- [x] Backend: API token last four and regenerate; `GET /api/system` (versions, disk space, key source, database size)
- [x] Web: Settings API access and System sections; Dashboard Up next card (Up next done with the Dashboard)
- [x] Backend: per-book run records (`sync_run_books`, `GET /api/sync/runs/{id}/books`)
- [x] Backend: `GET /api/queue` for the Dashboard's Up next card
- [x] Web: the expandable run row in Activity (built with Activity, since `GET /api/sync/runs/{id}/books` exists)
- [x] Close-out (2026-11-03): README and CLAUDE.md for the web app; the image built from the repository and run at the root and under a base path behind nginx (deep links, covers, the cookie's path, Send test to a real receiver); the web tests' helpers, hidden by `.gitignore`, committed
- [x] The code sign-in from the web app against Amazon, end to end (Sign in again in the test container, 2026-11-02: the code confirmed, a new device registered, the old one deregistered, the account kept its id and library)
- [x] Backend: one account per Audible login. A login an account already holds (the same `customer_info.user_id` and marketplace) gives that account new credentials and keeps its library, rather than adding a second row that would download and file the same books

## Milestone 4.5: Fixes (v3.0.1, v3.0.2)

A quick release of fixes before Milestone 5: a followed podcast no longer ends up in the library as a failed book, and the items carried over from Milestones 3 and 4.

### Tasks

- [x] Web tests: the first test of a file could fail on a busy CI runner, as the first render took longer than `findBy`'s one second (reproduced by sharing one core). The wait is now 5 s
- [x] Backend: leave podcasts out of the sync. Following a podcast puts the show in the library listing as one item with `content_delivery_type` `PodcastParent`, `content_type` `Podcast` and no length; its episodes (`PodcastEpisode`) only come back when asked for with `parent_asin` (checked live, 2026-11-06). Nothing filters it, so it is stored as a book and queued, though the show itself has no audio. Skip these items when mapping the library, by the podcast values rather than by allowing only `Product`, since the library also holds real books with `content_type` `Performance`. Remove any row an earlier sync stored, with nothing on disk to delete. Downloading episodes is a feature of its own, not part of this fix
- [x] Backend (from the 2026-11-01 review): `mark_book_downloaded`/`_failed`/`_unavailable` are not guarded on the claim, so after a stale reclaim (a book taking over `STALE_CLAIM_SECONDS` while a second process runs) the two runs can overwrite each other's outcome. Fixed with a claim token: `claim_book_for_download` returns one, every outcome and `release_book` must present it, and the run that lost the book logs that its outcome was left to the other
- [x] Backend: a plain `customer_id` column on `accounts` with a unique index on it and the marketplace, backfilled at start. The one-login-one-account rule reads the customer id from the encrypted credentials, so it cannot see an account whose credentials are unreadable (after a lost key, Add account adds a second row and the library is downloaded again) and holds only within one process (a CLI login beside the service). Duplicates already there: the oldest gets the customer and so keeps receiving its logins, as before; the others keep their books, can still sign in on their own row, and a warning at start says which to remove
- [x] Backend: replacing the credentials of the account a run is on deregisters the device that run is using, so the run ends `aborted` saying to log in again although a login just succeeded; the next run recovers. Defer the deregistration until the run has ended
- [x] Split `src/api.py` (967 lines, about 40 routes inside `create_app`) into one router per area, as the routes already group: `api_auth`, `api_sync`, `api_settings`, `api_system`, `api_accounts` and `api_books`, with the OpenAPI document unchanged. `src/` itself stays flat: 26 modules with tidy dependencies do not need folders yet. `downloader.py` is the next candidate, and `database.py` the last, since tests point `src.database.DB_FILE` at a temp file and that setting has to stay in one place
- [x] Trim CLAUDE.md (about 24,600 words, loaded into every session) to what the code cannot say: decisions, gotchas and test patterns. Much of it retells the code line by line, which is why it goes stale
- [x] Remove `database.update_book_accessories`, which nothing calls
- [x] Backend (from the 2026-11-09 review of `dev`): the start-up warning about duplicate accounts named the account it was filling as the newer, whichever was; a login landing in another process between the backfill's read and write failed the start; the warning was logged before the Logs screen's buffer was attached, so it only reached `docker logs`; and removing the account holding a customer left a duplicate without it until the next start, so a login meanwhile added a third account. The customer-id lookup now lives in one place and treats an empty id as none
- Moved to v3.2.0: renew a claim while its book downloads

## Milestone 4.6: Source layout (v3.1.0)

Restructure `src/` the way a Python project usually is, before Milestone 5 adds the wishlist and the catalogue: a named package inside `src/` (the "src layout"), with its modules grouped by area. One PR from `dev` once 4.5 is released, and nothing in it changes behaviour.

### Target

```
src/audible_sync/
  __init__.py
  __main__.py            python -m audible_sync -> cli.main
  cli.py                 main.py
  service.py
  settings.py  paths.py  model.py  database.py  library.py  system.py  logbuffer.py
  audible_api/           client.py (audible_client)  login.py (audible_login)  accounts.py
  pipeline/              sync.py  downloader.py  naming.py  encoding.py  progress.py
                         scheduler.py  runstate.py  notify.py
  security/              crypto.py  webauth.py  apitoken.py
  api/                   app.py (api.py)  schemas.py  openapi_export.py
    routes/              auth.py  sync.py  settings.py  system.py  accounts.py  books.py (the api_* modules)
```

- `tests/` stays flat; only its imports change
- `database.py` stays one module: tests point its `DB_FILE` at a temp file, so splitting it is a task of its own
- `config/`, `web/dist`, `data/` and `secrets/` stay outside the package: this is a service in an image, not a library on PyPI. `REPO_ROOT` becomes `parents[2]` and is still `/app`
- `audible_api/`, not `audible/`: with absolute imports it would not shadow the dependency, but it would read as if it did

### Tasks

- [x] Commit 1, the named package (mechanical): `git mv src/*.py src/audible_sync/` with an `__init__.py`; `pyproject.toml` gets a `[build-system]` and keeps pytest's `pythonpath = ["."]` (only for `from tests.conftest import`), ruff's `known-first-party` becomes `audible_sync`; `uv lock` and re-export `requirements.txt`
- [x] Commit 1: rewrite `from src.`, `import src.` and `from src import`, and every string naming a module: monkeypatch targets (`"src.database.DB_FILE"`, `"src.audible_client._PAGE_SIZE"`, `"src.apitoken"` and the rest), `caplog` logger names (`logger="src.downloader"` and four more), and the `getLogger("src.main"/"src.service")` of the two entry modules
- [x] Commit 1: `paths.REPO_ROOT` to `parents[2]` and `tests/test_paths.py` with it
- [x] Commit 1: the Dockerfile: `uv sync --locked --no-dev --no-install-project` before `COPY . .` and `uv sync --locked --no-dev` after it, and `CMD` `python -m audible_sync.service`
- [x] Commit 1: everything else naming the module: `web/package.json`'s `gen:api`, `docs/installation.md`, `docs/accounts.md`, `docs/manual-testing.md`, the README and CLAUDE.md (its layout section and the "flat, no `__init__.py`" rule)
- [x] Commit 2, the subpackages: pure `git mv` into the target with an `__init__.py` in each folder and the imports rewritten, no code changed, so the review is files that only moved
- [x] Commit 2: `[project.scripts] audible-sync = "audible_sync.cli:main"` and `__main__.py`, so the CLI is `audible-sync run`, `login` and `reset-password`
- [x] An autouse fixture in `tests/conftest.py` pointing `src.database.DB_FILE` at a temp file by default, so a patch target the rename misses cannot write to the real database
- [x] Separate commits: `run_pipeline` out of `cli.py` into `pipeline/run.py` (the service imported it from the CLI module); `BasePath` and the web app serving out of `api/app.py` into `api/web.py`
- [x] Checked: ruff, pytest and the web checks; `gen:api` gives a byte-identical `openapi.json`; no `src.` left in a string (a `caplog` on a logger that no longer exists lets a test pass while checking nothing); `data/audible_sync.db` unchanged by the test run; the image built from `git archive` runs in the test container, `/api/health` answers and a sync completes; `git log --follow` on a moved file still shows its history (2026-11-09: run #54 in the test container, success)

### Decided (2026-11-09)

- The package is `audible_sync`, built by `uv_build` (the toolchain already in use)
- The command line is `audible-sync` (or `python -m audible_sync`); `python -m src.main` stops working with no shim, and the release notes say so: the image's own command changes with it and `compose.yml` does not name the module
- The version: 3.1.0, since the CLI changes

## Milestone 5: Wishlist and Catalogue Search

Manage the Audible wishlist from the web app: search the catalogue, add books and remove them. Both are part of the API as well, so another application can read the wishlist and look books up with the bearer token. On the way, each library book gets its description and a full refresh from Audible, and the web app becomes installable as a PWA.

Three releases, in this order: the descriptions and the full refresh first, since they need no design work and the catalogue's books reuse the description's cleaning; the PWA next, so the service worker is settled before two new screens exist; the wishlist and the search last, the largest, whose designs can be done while the first is built.

## v3.2.0: Book descriptions and full refresh

Each library book keeps its description from Audible, shown on its page, in the API and written into its files, and every book's data can be refreshed from Audible: by hand, and once a day by default. Also the claim heartbeat left over from Milestone 4.5, which touches the same download code.

### Requirements

- Each library book's description: kept with the book, shown on its page, in the API and written into its files
- Refresh every book's data from Audible: by hand with a button, and once a day when a setting is on
- A download that takes longer than `STALE_CLAIM_SECONDS` is never taken over by a second process while it is still running

### What the Audible API gives

- **The description** is `publisher_summary`, HTML, in the `product_extended_attrs` response group; `product_desc`, despite its name, carries only the title and subtitle. The library endpoint returns it as well (50 of 50 books on a page in 2026-11-08's check), as does `editorial_reviews`, a list of review quotes in HTML. The group made a page of 50 books 289 KiB instead of 201 KiB

### Tasks

- [x] Backend: the library's descriptions: `product_extended_attrs` in `RESPONSE_GROUPS`, `publisher_summary` stored in a new `description` column that `update_books` refreshes on every sync (publishers edit them), and the books already stored get theirs from the first full refresh (below). The same field and the same cleaning as the catalogue's book
- [x] Backend: a full refresh: a run that re-reads each account's whole library (`audible.get_library(None)`) after its incremental fetch, so `update_books` brings every stored book's data up to date: title, authors, series, cover, description, availability. It is a run like any other, not an action of its own, so it has a run record, cannot overlap another run and can be cancelled. The re-read the parked books already trigger becomes the same step. It counts towards neither `books_seen` nor `books_added`, as now. A run says whether it was one
- [x] Backend: `POST /api/sync` takes an optional `{"full": true}`, and the CLI `--full`; `scheduler.trigger` passes it to the run
- [x] Backend: a `daily-full-refresh` setting (on by default; `_PARSERS`, `config.ini`, `docs/configuration.md`): when on, a run is a full refresh if the account has had none yet that UTC day (any run not asked for one, Sync now and the CLI included). Kept per account, so an account added later or one that failed is not skipped, and a manual full refresh counts
- [x] Backend: the description in `GET /api/books/{id}`, as plain text (`description`) and as HTML rebuilt from an allow-list (`description_html`, `htmltext.safe_html`), never Audible's HTML as it came; the HTML is stored as it came and made safe on read; the list leaves both out
- [x] Backend: the description written into the files as plain text: M4B's `description` tag; in Ogg Opus not `DESCRIPTION`, which FFmpeg makes of the `comment` carrying the ASIN that `read_embedded_asin` reads, so another key such as `SYNOPSIS`. A file already downloaded only gets it when downloaded again
- [x] Web: the description on the Book page, folded to a few lines with Show more
- [x] Web: Refresh all from Audible beside Sync now on the Dashboard, which starts a full refresh (disabled during a run, like Sync now); the daily full refresh as a switch in Settings' Schedule section; a full refresh marked as such in Activity
- [x] Backend: renew a claim while its book downloads. `last_attempt_at` is written once, at the claim, so a book taking over `STALE_CLAIM_SECONDS` (6 h) is reclaimable while still in progress, and a second process downloads and files it at the same time. The claim token only settles whose outcome is kept. Done as a thread renewing the claim (`claim_renewed_at`, guarded on the claim) every five minutes for the whole of `_process_book`, the FFmpeg pass included
- [x] Docs: the description and the full refresh in `docs/api.md` and `opencollection/` (`GET /api/books/{id}`, `POST /api/sync`'s `full`); `--full` in the README; CLAUDE.md
- [x] Checked live: a full refresh from the button and from the daily setting each re-read the whole library once per account, and a book edited in the database by hand is put back by it
- [x] Checked live: after the first full refresh every library book with a summary on Audible has its description; a new download carries it in M4B and in Ogg Opus, and its ASIN is still read back from the file
- [x] Checked live: a download taking longer than a few minutes renews its claim, and a second process does not take the book

- Later, not in this release: a refresh changes the database only. A book whose title, author or series changed stays in its old folder with its old tags until it is downloaded again; renaming and re-tagging files on disk would be a feature of its own

## v3.3.0: Installable web app (PWA)

The web app can be installed on a phone or a desktop, opening in its own window, at the root and under a base path.

### Requirements

- Install the web app on a phone or a desktop (PWA)
- A new build is offered, never swapped under an open page; nothing under `api/` is ever cached

### Tasks

- [x] Web: PWA - a web app manifest (name, short name, `display: standalone`, the theme's colours, icons at 192 and 512 px with a maskable one, an `apple-touch-icon`), its `start_url` and `scope` relative, so one build installs at the root and under `AUDIBLE_SYNC_BASE_PATH`
- [x] Web: PWA - a service worker that caches the app shell (`index.html` and the build's hashed `assets/`) and never anything under `api/`. A new build is offered as "Update available" rather than swapped under an open page; with the service down the shell still loads and shows Cannot reach Audible Sync
- [x] Backend: PWA - serve the manifest and the service worker with the right content type and `no-cache` (only `assets/` is `immutable`), at the root and under a base path
- [x] Docs: in the installation guide, that a browser offers Install only over https or on `localhost`, so a plain http address on a LAN stays an ordinary page; CLAUDE.md
- [ ] Checked live: the app installed on a phone over https, at the root and under a base path, and updated by a new build; with the service stopped it opens on Cannot reach Audible Sync

## v3.4.0: Wishlist and catalogue search

The wishlist of every account in one list, a search of the Audible catalogue, and adding and removing books, in the web app and in the API for other applications to use.

### Requirements

- View the wishlist of every account in one list
- Search the Audible catalogue by the filters its API offers
- Add a book to the wishlist and remove one
- The wishlist and the catalogue search in the API, for other applications to use: the wishlist as a list to read, the catalogue as a source of audiobook data

- Search by a prefix in the search box, on Search and on Library: `author:John Scalzi`, `narrator:`, `series:Old Man's War`, `title:`

### Decided (2026-11-12)

- The catalogue is searched through an account: `account_id`, or by default the oldest signed in. Its marketplace is the one searched and its member price the one shown; the Search page offers the choice only when there are several. A book counts as in the library when any account of that marketplace holds it
- Authors: a thin search only (`GET /api/catalog/authors?name=`), in the API and not the web app. Audible has an id, a name and a picture, and the same author can carry another id on their books, so their books are a search by name
- Prefixes: `author:`, `narrator:`, `series:` and `title:`, a value running to the next prefix or the end, so no quotes; anything else is keywords, and only those four words count, so "Star Wars: Trials of the Jedi" searches as written. One parser in the web app for both pages. The API stays structured: the catalogue search already takes `author`, `narrator` and `title`; `GET /api/books` gains `title`, `author`, `narrator` and `series`
- `series:` in the catalogue: Audible has no series filter, so `GET /api/catalog/series?name=` finds the series through the books a keyword search for the name returns, and `GET /api/catalog/series/{asin}` lists one. One match goes straight to its books; several are offered to choose from
- Three stacked branches, each reviewed before the next: `feat/catalog` (the catalogue and the library filters), `feat/wishlist` (the table, its sync and endpoints), then the web pages once the design is in

### What the Audible API gives

Checked against the live UK account, 2026-11-03 to 2026-11-06. The API is undocumented, so these are observations, not promises.

- **The wishlist** is one per marketplace login, read 50 a page with the date each book was added. Adding and removing are by ASIN, and both succeed silently when there is nothing to do - a book already there, a book not there - so "already on the wishlist" has to come from our own table
- **Catalogue search** takes `keywords`, `title`, `author`, `narrator`, `category_id`, a sort and a page of up to 50, **counted from 0**. Sorting by `Relevance` by name leaves results out; no sort is relevance. `title` only comes back with the `product_desc` response group. `publisher` matches loosely (it returned podcasts) and is left out. Deep pages are clamped without an error, at about 1,000 results sorted by release date and 5,000 by bestsellers. The member's price is only told to a signed-in request
- **Plus** is a plan in each title's `plans`, named per marketplace: `Audible-AYCL` in the UK, `US Minerva` in the US
- **A book** has its title, authors, narrators, every series with the position, length, release date, language (as a word, `english`), publisher, abridged or not, description (HTML), covers at 500 and 1215 px, genres and ratings. **It has no ISBN**, and an ISBN finds nothing in a search
- **A series** ASIN is itself a product that lists its books with their positions
- **Authors are thin**: an id and a name, no biography or picture, and the same author can carry two ids (one on a book, another in the search suggestions)
- **Plus**: every result carries its `plans`, so whether a book is included in the membership costs nothing extra. The Plus catalogue itself cannot be listed in the UK (the `plan` filter does not accept its plan name), so there is no browsing it
- **The catalogue answers without credentials.** What only Audible Sync can add is what an account knows: owned, on the wishlist, included in Plus, the member price

### Tasks

- [ ] Design: the Wishlist and Search screens in `docs/design/`, desktop and phone, both themes: the combined list, the search filters, a result's owned, wishlisted and Plus markers, adding and removing with their in-flight and failed states, an empty wishlist and a search that finds nothing; the search box's prefixes (a hint, the recognised filters as chips, on Library too), a series' books by position with their editions, and choosing between series that match a name
- [x] Backend: `Audible` client methods for the catalogue (search, a book by ASIN, a series by ASIN, the categories, authors), mapped to a catalogue book that orders its series by the library's rule (`sort_series`, `primary_series`)
- [ ] Backend: `Audible` client methods for the wishlist (every page, add, remove)
- [ ] Backend: a `wishlist` table keyed by `(account_id, asin)` holding the book's data and the date added, synced for each account in its run after the library sync and on demand. A wishlist that cannot be read is logged and leaves the run's outcome alone
- [ ] Backend: `GET /api/wishlist`: every account's or one's, paged and sorted (date added, title, release date), each book saying whether a library already holds it
- [ ] Backend: `POST /api/wishlist` (`account_id`, `asin`) and `DELETE /api/wishlist/{account_id}/{asin}`: Audible first, then the table. A book the account owns is refused; one already there, or already gone, is said from the table, since Audible does not say; 409 when the account needs a sign-in, 502 when Audible does not answer
- [x] Backend: `GET /api/catalog/search` (the account whose marketplace to search, `keywords`, `title`, `author`, `narrator`, `category_id`, `sort`, `page`), `GET /api/catalog/books/{asin}`, `GET /api/catalog/series?name=`, `GET /api/catalog/series/{asin}`, `GET /api/catalog/categories` and `GET /api/catalog/authors?name=`. Every book is marked in the library and included in Plus, with the member price. Answers are cached for five minutes, so a client that polls does not become a stream of requests to Audible in the account's name
- [ ] Backend: mark every catalogue book wishlisted, once the wishlist table exists
- [x] Backend: the catalogue's response models, made to be read by another application as well as the web app: the ASIN as the key of every book; the title, description, cover, release date, language as a code, genres, rating, narrators, length in seconds, abridged or not, and the series with positions; no ISBN, since Audible has none
- [ ] Backend: the wishlist's response model, the catalogue book with the account and the date added
- [ ] Backend (from the design): `GET /api/wishlist` says, per account, when its wishlist was last read and whether that read failed, for the Wishlist page's "Updated 2 hours ago" and its notice when one account's could not be read; `POST /api/wishlist/refresh` re-reads them on demand (Refresh from Audible), all or one `account_id`; a catalogue book says which accounts' wishlists hold it (`on_wishlist`), as the marker names the account
- [x] Backend: `GET /api/books` (and the bulk filter) takes `title`, `author`, `narrator` and `series`. Every text filter, `q` included, now matches outside ASCII and ignores the kind of apostrophe: the list columns are JSON, which escapes anything else, so "Kaijū" or Audible's "Old Man’s War" found nothing
- [ ] Web: the Wishlist page: every account's books in one list with the account filter and the sort in the URL, Remove with its confirmation, a marker on a book a library already holds, and a refresh from Audible
- [ ] Web: the Search page: the filters in the URL, results with the owned, wishlisted and Plus markers and the member price, Add to wishlist and Remove on each (the account chosen when there are several), paging that stops where Audible's clamp does (`reachable`)
- [ ] Web: the search box's prefixes (`author:`, `narrator:`, `series:`, `title:`), one parser shared by Search and Library, with the hint and the recognised filters as chips; `series:` on Search through the series endpoints, choosing when several match
- [ ] Web: both pages in the sidebar and the phone's drawer, and their paths in the base path's reserved segments (`_RESERVED_SEGMENTS`, which a test checks against `web/src/app/routes.tsx`)
- [ ] Docs: `docs/api.md` for the wishlist and the catalogue; the requests in `opencollection/`; the README; CLAUDE.md
- [ ] Checked live: a book added from Search appears in Audible's own wishlist and one removed leaves it; the Wishlist page matches Audible's for two accounts; the wishlist and catalogue endpoints with the bearer token alone
