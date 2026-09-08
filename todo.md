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

- [ ] **Return objects from the database, not raw tuples.** Every read returns a
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

- [ ] **Give the pipeline a settings object instead of loose keyword arguments.**
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

- [ ] **Replace the two-value status string with a real state machine.**
  `waiting_download` and `downloaded` are string literals inlined in three SQL
  statements with no enum and no terminal failure state, so a book that can never
  succeed is re-licensed, re-downloaded and re-failed on every run forever. There
  is no `downloading` state either, so a scheduler tick that starts while a
  download is running picks the same rows. Add a `BookStatus` enum, `attempts`,
  `last_error` and `last_attempt_at` columns, a claim step, and an upsert in
  `update_books` so `is_finished` and `percent_complete` refresh after first sync.
  *Blocks: the scheduler, and any UI that shows why a book failed.*

- [ ] **Record sync runs and stream download progress.** The incremental cursor is
  derived from the newest `date_added` in the library table, so there is no source
  of truth for "last synced" and a run that died half way cannot be told from one
  that found nothing. Add the `sync_runs` table (`started_at`, `finished_at`,
  `books_seen`, `books_added`, `outcome`) and use the previous successful run's
  start time as the cursor. Progress is currently wired straight into a `tqdm`
  bar, which writes carriage returns into the service log and gives the UI nothing
  to read; `_stream_to_file` already takes the response and path, so give it an
  injected progress callback. *Blocks: async downloader to monitor progress.*

Smaller items to fold in while doing the above:

- [ ] Delete `src/api.py` and the root `main.py` scaffold and start the service
  fresh; `POST /sync` currently raises `TypeError`. Move `fastapi` and `uvicorn`
  out of the runtime dependencies until the service actually exists.
- [ ] Rename `src/audible.py` so it stops shadowing the `audible` dependency
  (`known-third-party` in `pyproject.toml` is the workaround holding it together).
- [ ] Enable `PRAGMA journal_mode=WAL` before two processes share the database.
- [ ] Add tests for the network-facing `Downloader` methods, which have none.

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

