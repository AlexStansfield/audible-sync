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

## Milestone 4.5: Fixes

A quick release of fixes before Milestone 5: a followed podcast no longer ends up in the library as a failed book, and the items carried over from Milestones 3 and 4.

### Tasks

- [x] Web tests: the first test of a file could fail on a busy CI runner, as the first render took longer than `findBy`'s one second (reproduced by sharing one core). The wait is now 5 s
- [x] Backend: leave podcasts out of the sync. Following a podcast puts the show in the library listing as one item with `content_delivery_type` `PodcastParent`, `content_type` `Podcast` and no length; its episodes (`PodcastEpisode`) only come back when asked for with `parent_asin` (checked live, 2026-11-06). Nothing filters it, so it is stored as a book and queued, though the show itself has no audio. Skip these items when mapping the library, by the podcast values rather than by allowing only `Product`, since the library also holds real books with `content_type` `Performance`. Remove any row an earlier sync stored, with nothing on disk to delete. Downloading episodes is a feature of its own, not part of this fix
- [ ] Backend (from the 2026-11-01 review): `mark_book_downloaded`/`_failed`/`_unavailable` are not guarded on the claim, so after a stale reclaim (a book taking over `STALE_CLAIM_SECONDS` while a second process runs) the two runs can overwrite each other's outcome. Deferred: the service runs one sync at a time, so it takes a CLI run beside the service, and the fix is a claim token on every transition
- [ ] Backend: a plain `customer_id` column on `accounts` with a unique index on it and the marketplace, backfilled at start. The one-login-one-account rule reads the customer id from the encrypted credentials, so it cannot see an account whose credentials are unreadable (after a lost key, Add account adds a second row and the library is downloaded again) and holds only within one process (a CLI login beside the service). To decide: what becomes of any duplicates already there, which a unique index would refuse
- [ ] Backend: replacing the credentials of the account a run is on deregisters the device that run is using, so the run ends `aborted` saying to log in again although a login just succeeded; the next run recovers. Defer the deregistration until the run has ended
- [ ] Split `src/api.py` (967 lines, about 40 routes inside `create_app`) into one router per area, as the routes already group. `src/` itself stays flat: 26 modules with tidy dependencies do not need folders yet. `downloader.py` is the next candidate, and `database.py` the last, since tests point `src.database.DB_FILE` at a temp file and that setting has to stay in one place
- [ ] Trim CLAUDE.md (about 24,600 words, loaded into every session) to what the code cannot say: decisions, gotchas and test patterns. Much of it retells the code line by line, which is why it goes stale
- [x] Remove `database.update_book_accessories`, which nothing calls

## Milestone 5: Wishlist and Catalogue Search

Manage the Audible wishlist from the web app: search the catalogue, add books and remove them. Both are part of the API as well, so another application can read the wishlist and look books up with the bearer token. The web app also becomes installable as a PWA.

### Requirements

- View the wishlist of every account in one list
- Search the Audible catalogue by the filters its API offers
- Add a book to the wishlist and remove one
- The wishlist and the catalogue search in the API, for other applications to use: the wishlist as a list to read, the catalogue as a source of audiobook data
- Install the web app on a phone or a desktop (PWA)

### What the Audible API gives

Checked against the live UK account, 2026-11-03 to 2026-11-06. The API is undocumented, so these are observations, not promises.

- **The wishlist** is one per marketplace login, read 50 a page with the date each book was added. Adding and removing are by ASIN, and both succeed silently when there is nothing to do - a book already there, a book not there - so "already on the wishlist" has to come from our own table
- **Catalogue search** takes `keywords`, `title`, `author`, `narrator`, `category_id`, a sort and a page of up to 50. `title` only comes back with the `product_desc` response group. `publisher` matches loosely (it returned podcasts) and is left out. Deep pages are clamped without an error, at about 1,000 results sorted by release date and 5,000 by bestsellers
- **A book** has its title, authors, narrators, every series with the position, length, release date, language (as a word, `english`), publisher, abridged or not, description (HTML), covers at 500 and 1215 px, genres and ratings. **It has no ISBN**, and an ISBN finds nothing in a search
- **A series** ASIN is itself a product that lists its books with their positions
- **Authors are thin**: an id and a name, no biography or picture, and the same author can carry two ids (one on a book, another in the search suggestions)
- **Plus**: every result carries its `plans`, so whether a book is included in the membership costs nothing extra. The Plus catalogue itself cannot be listed in the UK (the `plan` filter does not accept its plan name), so there is no browsing it
- **The catalogue answers without credentials.** What only Audible Sync can add is what an account knows: owned, on the wishlist, included in Plus, the member price

### Tasks

- [ ] Design: the Wishlist and Search screens in `docs/design/`, desktop and phone, both themes: the combined list, the search filters, a result's owned, wishlisted and Plus markers, adding and removing with their in-flight and failed states, an empty wishlist and a search that finds nothing
- [ ] Backend: `Audible` client methods for the wishlist (every page, add, remove) and the catalogue (search, a book by ASIN, a series by ASIN, the categories), mapped to a catalogue book that orders its series by the library's rule (`sort_series`, `primary_series`)
- [ ] Backend: a `wishlist` table keyed by `(account_id, asin)` holding the book's data and the date added, synced for each account in its run after the library sync and on demand. A wishlist that cannot be read is logged and leaves the run's outcome alone
- [ ] Backend: `GET /api/wishlist`: every account's or one's, paged and sorted (date added, title, release date), each book saying whether a library already holds it
- [ ] Backend: `POST /api/wishlist` (`account_id`, `asin`) and `DELETE /api/wishlist/{account_id}/{asin}`: Audible first, then the table. A book the account owns is refused; one already there, or already gone, is said from the table, since Audible does not say; 409 when the account needs a sign-in, 502 when Audible does not answer
- [ ] Backend: `GET /api/catalog/search` (the account whose marketplace to search, `keywords`, `title`, `author`, `narrator`, `category_id`, `sort`, `page`), `GET /api/catalog/books/{asin}`, `GET /api/catalog/series/{asin}` and `GET /api/catalog/categories`. Every book is marked owned, wishlisted and included in Plus, with the member price. Answers are cached for a few minutes, so a client that polls does not become a stream of requests to Audible in the account's name
- [ ] Backend: the wishlist's and the catalogue's response models, made to be read by another application as well as the web app: the ASIN as the key of every book; the title, description, cover, release date, language as a code, genres, rating, narrators, length in seconds, abridged or not, and the series with positions; no ISBN, since Audible has none. To decide: which marketplace answers when there are several accounts, and whether to offer author search and lookup from the little Audible has
- [ ] Web: the Wishlist page: every account's books in one list with the account filter and the sort in the URL, Remove with its confirmation, a marker on a book a library already holds, and a refresh from Audible
- [ ] Web: the Search page: the filters in the URL, results with the owned, wishlisted and Plus markers and the member price, Add to wishlist and Remove on each (the account chosen when there are several), paging that stops where Audible's clamp does
- [ ] Web: both pages in the sidebar and the phone's drawer, and their paths in the base path's reserved segments (`_RESERVED_SEGMENTS`, which a test checks against `web/src/app/routes.tsx`)
- [ ] Web: PWA - a web app manifest (name, short name, `display: standalone`, the theme's colours, icons at 192 and 512 px with a maskable one, an `apple-touch-icon`), its `start_url` and `scope` relative, so one build installs at the root and under `AUDIBLE_SYNC_BASE_PATH`
- [ ] Web: PWA - a service worker that caches the app shell (`index.html` and the build's hashed `assets/`) and never anything under `api/`. A new build is offered as "Update available" rather than swapped under an open page; with the service down the shell still loads and shows Cannot reach Audible Sync
- [ ] Backend: PWA - serve the manifest and the service worker with the right content type and `no-cache` (only `assets/` is `immutable`), at the root and under a base path
- [ ] Docs: `docs/api.md` for the wishlist and the catalogue; the requests in `opencollection/`; the README; CLAUDE.md; and in the installation guide, that a browser offers Install only over https or on `localhost`, so a plain http address on a LAN stays an ordinary page
- [ ] Checked live: a book added from Search appears in Audible's own wishlist and one removed leaves it; the Wishlist page matches Audible's for two accounts; the wishlist and catalogue endpoints with the bearer token alone; the app installed on a phone over https, at the root and under a base path, and updated by a new build
