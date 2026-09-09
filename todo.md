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

- Login to audible via website
- Convert to an API service
- API must be designed with a frontend in mind (milestone 4)
- Get library of books by API
- Get and Change Settings by API
- Monitor Download progress
- Ability to "delete" a download or force a redownload

### Step 0: Structural groundwork (do this first)

A pre-pivot code review (2026-09-08) fixed the correctness problems on
`fix/pre-api-review`. It also found four structural issues that are cheap to
change now and expensive once endpoints, a scheduler and a UI depend on them.
Each one blocks a Milestone 3 requirement, so they come before the endpoints.

- [x] **Return objects from the database, not raw tuples.** Every read returns a
  positional `sqlite3` tuple and `downloader.py`, `naming.py` and the tests
  hard-code indices (`book[12]` is the cover URL, `book[17]` is `has_pdf`) with
  the same `json.loads` boilerplate repeated in each. `CLAUDE.md` has to carry a
  column-index table so contributors can read the code. Adding a column in the
  middle silently shifts every index with no exception, and FastAPI response
  models need attribute access anyway. Set `conn.row_factory = sqlite3.Row` and
  add a `Book.from_row()` that owns the JSON decode and the database-only fields
  (`status`, paths, `encoding_format`, `downloaded_at`); make `Book` a dataclass
  while there, and fix `date_added`, which is annotated `datetime` but always
  holds an ISO string. *Blocks: get library of books by API.*

- [x] **Give the pipeline a settings object instead of loose keyword arguments.**
  Config parsing, validation, folder creation and the sync-then-download sequence
  live inline under `if __name__ == "__main__"`, and the values are threaded as
  eight keyword arguments through `download_books` into `_process_book`. Nothing
  importable knows how to build an `Audible` client or read the config, which is
  exactly why `api.py` calls `sync_library()` with no argument. Add a frozen
  `Settings` dataclass with `Settings.from_ini(path)` that runs the validators
  (later `Settings.from_db`), a `main()` function, and pass `settings` to the
  pipeline. Also resolve `DB_FILE` and the config path against the repo root
  rather than the working directory, or `uvicorn` started elsewhere creates an
  empty database somewhere else. *Blocks: get and change settings by API.*

- [x] **Replace the two-value status string with a real state machine.**
  `waiting_download` and `downloaded` are string literals inlined in three SQL
  statements with no enum and no terminal failure state, so a book that can never
  succeed is re-licensed, re-downloaded and re-failed on every run forever. There
  is no `downloading` state either, so a scheduler tick that starts while a
  download is running picks the same rows. Add a `BookStatus` enum, `attempts`,
  `last_error` and `last_attempt_at` columns, a claim step, and an upsert in
  `update_books` so `is_finished` and `percent_complete` refresh after first sync.
  *Blocks: the scheduler, and any UI that shows why a book failed.*
  Done 2026-09-09: `BookStatus` is a `StrEnum` in `model.py` with `waiting_download`,
  `downloading`, `downloaded` and terminal `failed`, so no stored row had to be
  rewritten. `claim_book_for_download` takes a book in one UPDATE and reclaims a
  download abandoned beyond `STALE_CLAIM_SECONDS`; `mark_book_failed` decides
  retry-or-give-up inside the UPDATE from the attempts the claim incremented, capped by
  the new `[sync] max-attempts` (default 3); a refused licence fails on the first try;
  `release_book` hands the claim back for an auth abort, which is not the book's fault.
  `update_books` is now an upsert that refreshes the mutable API fields only - never
  `date_added`, the sync cursor, nor anything the downloader owns.
  A refused licence is **not** terminal: Audible withdraws Plus titles a customer
  added while they were included (16 of 306 here on 2026-09-09, including *The Time
  Traveler's Wife* and *Red Rising*) and later offers them again. `customer_rights.
  is_consumable` is read at sync time, a withdrawn book is parked as `unavailable`
  so it leaves the queue, and the upsert returns it to `waiting_download` as soon as
  a sync sees it consumable - no manual step.

- [x] **Record sync runs and stream download progress.** The incremental cursor is
  derived from the newest `date_added` in the library table, so there is no source
  of truth for "last synced" and a run that died half way cannot be told from one
  that found nothing. Add the `sync_runs` table (`started_at`, `finished_at`,
  `books_seen`, `books_added`, `outcome`) and use the previous successful run's
  start time as the cursor. Progress is currently wired straight into a `tqdm`
  bar, which writes carriage returns into the service log and gives the UI nothing
  to read; `_stream_to_file` already takes the response and path, so give it an
  injected progress callback. *Blocks: async downloader to monitor progress.*
  Done 2026-09-09: `sync_runs` records one row per `run_pipeline` call, covering both
  halves of it - `books_seen`/`books_added` from the sync and `books_downloaded`/
  `books_failed` from the downloads, plus the `error` that stopped it. `SyncOutcome` is
  a `StrEnum` beside `BookStatus`. The row is opened before Audible is touched and
  closed on every exit including the exception arm, so a killed run is the one thing
  left `running` with a NULL `finished_at` - which is exactly "died half way", and
  distinguishable from `success` with `books_added = 0`.
  The cursor is the previous run's `started_at` **minus an hour**, not the bare start
  time: `purchased_after` is filtered on Audible's clock, so a local clock running even
  slightly fast would step over a purchase and never look at it again. Re-reading an
  hour costs nothing because `update_books` is an upsert. It also needs reformatting -
  `_utcnow` writes `+00:00`, Audible's own `date_added` is the `Z` form. With no run
  recorded it falls back to `MAX(date_added)`, so an existing database behaves exactly
  as it did until its first run is on record. `partial` (sync fine, a download failed)
  counts as a cursor; `failed` and `running` do not.
  Progress is now an injected `Progress` protocol (`src/progress.py`) rather than a
  callback, because a bar has a lifecycle - `start(desc, total)`, `advance`, `finish`,
  the last in a `finally` so a failed download does not leave a bar open across the
  next one. `NullProgress` is the default, `TqdmProgress` is injected by `main()` only,
  and `tqdm` is imported in that one module. `download_file` stopped being static so it
  could report and take a `desc`: the multi-gigabyte AAXC was the one transfer the bar
  could not name.

Smaller items to fold in while doing the above:

- [ ] Delete `src/api.py` and the root `main.py` scaffold and start the service
  fresh; `POST /sync` currently raises `TypeError`. Move `fastapi` and `uvicorn`
  out of the runtime dependencies until the service actually exists.
- [ ] Rename `src/audible.py` so it stops shadowing the `audible` dependency
  (`known-third-party` in `pyproject.toml` is the workaround holding it together).
- [x] Enable `PRAGMA journal_mode=WAL` before two processes share the database.
  Done 2026-09-09: set in `init_db` before the DDL. It is a property of the database
  file, so one call holds for every later connection.
- [ ] Add tests for the network-facing `Downloader` methods, which have none.
- [x] **`download_annotations` turns a 404 into a permanent failure.** The
  accessory contract says the three methods return `False` only when the thing
  is genuinely absent and raise otherwise, and `download_pdf`/`download_cover`
  both special-case 404. `download_annotations` does not: the Amazon sidecar
  endpoint 404s for a book that has never been opened, `audible.Client` raises
  `NotFoundError`, `_process_book` propagates it, and the book stays
  `waiting_download` forever. Every run re-licenses and re-downloads the whole
  AAXC before failing again on the same 404, so it burns a full book of
  bandwidth per retry. Measured 2026-09-09: 4 of a 25-book sample 404 (~16%,
  roughly 49 of the 306-title library), and it is what stopped *Northern
  Lights* in a real run. Catch `NotFoundError` and return `False`, and add a
  test - this is the one accessory method with no 404 path.
  Fixed 2026-09-09: it now catches `NotFoundError` and returns `False`, matching
  `download_pdf` and `download_cover`, with tests covering the 404, a non-404
  error still raising, and the empty and populated responses.

- [ ] **`series[0]` is an arbitrary pick for a book in more than one series.**
  `_prepare_book` keeps every series entry in whatever order the API returned,
  and both `book_template_values` and `generate_metadata` then take `series[0]`.
  11 of the 306 titles here are in two series, and the order is not even stable
  between endpoints: for *Northern Lights* the `library` list endpoint returns
  Audible's typo'd "His Dark Materialsik" first while `library/{asin}` returns
  the real "His Dark Materials" first. The visible result is that books 1 and 2
  of the trilogy file under `His Dark Materialsik/` and book 3 under
  `His Dark Materials/`, splitting one series across two folders. Other picks
  are merely debatable (*Dune* files under "The Dune Sequence" at sequence 12
  rather than "Dune" at 1). Needs a deliberate rule - prefer the series the
  sequence makes sense for, or let the naming template choose - rather than
  index 0. Noticed 2026-09-09 during a real run.

- [ ] Fix `series-part=None` in the embedded metadata. `generate_metadata` reads
  the series entry with `series_info.get("sequence", "")`, but `_prepare_book`
  always creates the key (`entry.get("sequence")`), so a series entry whose
  sequence is null yields `None`, not the intended `""`. That `None` is written
  straight through: the FFMETADATA file gets a literal `series-part=None` line
  and `write_m4b_extra_tags` copies it into the iTunes freeform atom, so the
  book shows "None" as its series number in a player. `.get("title", "")` on
  the line above has the same shape. Fix both with `or ""` and cover the null
  case in `test_generate_metadata`. Reproduced 2026-09-09; currently latent,
  as no book in the 306-title library has a null title or sequence, but
  `naming.py` already guards the same shape and `test_naming` exercises it.

### Tasks

- [ ] Add settings table to database
- [ ] Initialise settings on initial run
- [ ] Convert to an API service
- [ ] Aysnc downloader to monitor progress of download
- [ ] Login and save credentials

## Milestone 4: Web App Frontend

A web application that exposes a UI for the API.

It should support login to Audible via the web and storing returned credentials.

More requirements to be defined

