# CLAUDE.md - AI Assistant Guide for Audible-Sync

Guidance for AI assistants working with the audible-sync codebase.

## Quick Reference

| Aspect | Details |
|--------|---------|
| **Language** | Python 3.12 (`.python-version`, `pyproject.toml`) |
| **Type** | CLI application (service + API planned for Milestone 3) |
| **Entry Point** | `python -m src.main` (or `uv run python -m src.main`) |
| **Database** | SQLite 3 (`data/audible_sync.db`) |
| **Config** | INI format (`config/config.ini`) |
| **Lines of Code** | ~1900 lines across 11 Python modules, plus ~1700 lines of tests |
| **Logging** | Python `logging`, configured in `src/main.py` |
| **Testing** | pytest (`tests/`, binary fixtures in `tests/fixtures/`), run with `uv run pytest`; unit tests required for new code |
| **Linting / Formatting** | ruff (config in `pyproject.toml`), run with `uv run ruff check .` and `uv run ruff format .` |
| **CI** | GitHub Actions: lint, format check and tests on every push/PR to `dev` and `main` |
| **Package Manager** | uv (`pyproject.toml` + `uv.lock`); `requirements.txt` is generated for pip users |

## Project Overview

**Purpose:** Sync an Audible library, download owned audiobooks, and decrypt them to DRM-free M4B or Ogg Opus files with embedded metadata, cover art and chapters.

**Inspiration:** [BALD (Bash Audible Library Downloader)](https://github.com/damajor/BALD). This Python implementation uses the same underlying `audible` library but aims to become a user-friendly service with a web UI and automated scheduling.

**Important:** This app does NOT crack DRM. It only decrypts audiobooks the user owns.

**Current State (Milestone 2 complete):**
- Incremental library sync from the Audible API
- Download and decrypt to M4B (stream copy) or re-encode to Ogg Opus (`.oga`) at a configurable bitrate, with metadata, cover art and chapters embedded in either format
- Companion PDF, high-res cover and annotations downloaded alongside the book
- Path-safe file naming from configurable templates, and per-book error handling (a failing book is skipped, not fatal)
- Docker image built by GitHub Actions on version tags

Milestone 3 (API service, scheduler, web UI) is next. A code review before that pivot fixed the correctness problems listed below and recorded the structural work as **Step 0** in `todo.md`; do that before adding endpoints.

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
│   ├── main.py               # Entry point: main(), run_pipeline(), logging setup
│   ├── settings.py           # Frozen Settings dataclass, Settings.from_ini, validation
│   ├── paths.py              # REPO_ROOT and resolve_path: every path is anchored here
│   ├── model.py              # Book data model
│   ├── database.py           # SQLite schema, migrations, queries
│   ├── audible.py            # Audible API client and response mapping
│   ├── sync.py               # Incremental library sync
│   ├── naming.py             # Sanitizer and folder/filename templates ([naming] in config.ini)
│   ├── encoding.py           # Output formats, Opus picture block and chapter tags ([encoding] in config.ini)
│   ├── downloader.py         # Download, accessories, metadata, decryption, filing
│   └── api.py                # FastAPI stub (broken, Milestone 3)
├── tests/
│   ├── test_database.py      # Schema, migration, queries against a temp DB
│   ├── test_downloader.py    # Sanitizer, metadata, FFMETADATA writer, per-book error handling
│   ├── test_naming.py        # Sanitizer, templates, optional groups, default layout, validation
│   ├── test_encoding.py      # Format validation, JPEG/PNG header parsing, picture block, chapter tags, M4B freeform tags, ASIN readback
│   ├── test_audible.py       # Field mapping, missing keys, per-item skip, pagination
│   ├── test_settings.py      # Config parsing, defaults, path anchoring, validation
│   ├── test_paths.py         # Repo-root anchoring, absolute paths, ~ expansion
│   ├── test_main.py          # Log level, run_pipeline wiring, main ordering
│   └── fixtures/silence.m4b  # 900-byte silent AAC M4B for tag-writing tests
├── main.py                   # Leftover uv scaffold ("Hello from audible-sync!"), unused
├── compose.yml
├── Dockerfile
├── pyproject.toml / uv.lock  # Direct dependencies, dev group, ruff and pytest config
├── requirements.txt          # Generated: uv export --no-dev --no-hashes --no-emit-project -o requirements.txt
├── README.md
└── todo.md                   # Milestone roadmap
```

## Architecture

```
main.py (orchestrator)
  ├─→ settings.py   (config, validation)  ─→ paths.py
  ├─→ database.py   (persistence)         ─→ paths.py
  ├─→ audible.py    (API integration)
  ├─→ sync.py       (library sync)
  └─→ downloader.py (download, metadata, decrypt, file)
        ├─→ naming.py   (paths)
        └─→ encoding.py (format, Opus tags)
```

**Data flow:**
```
Audible API → audible.py → sync.py → database.py → SQLite
                                          ↓
                                    downloader.py → FFmpeg → M4B or OGA + PDF/cover/annotations
```

- No `__init__.py` files; flat `src/` directory run as modules (`python -m src.main`)
- Absolute imports: `from src.module import ...`
- Config and DB paths are anchored to the repo root by `src/paths.py`, so the app can be started from any working directory. Never build a path from a relative string; put it through `resolve_path`
- `settings.py` must never import `downloader.py`: the downloader imports `Settings`, so the reverse edge is a circular import. This is why `validate_max_download` lives in `settings.py`

## Key Modules

### main.py

Three functions, no module-level work:

- `configure_logging(debug)` sets the root logger from `settings.debug`. Called from the entry point, not at import: configuring logging on import would also reconfigure any host process that imports this module, which the Milestone 3 service will do
- `run_pipeline(settings)` creates the folders, initialises the DB, builds the `Audible` client from `settings.auth_file`, then runs `sync_library()` and `download_books(audible, settings)`. Importable and free of logging side effects, so the service and scheduler can run the same pipeline
- `main()` is the CLI entry point: `Settings.from_ini()`, then `configure_logging`, then `run_pipeline`. Settings are read (and therefore validated) first, so a bad template or bitrate fails before any folder is created - the old inline code created the folders before it validated anything

This is a one-shot run: sync + download, then exit.

### settings.py

`Settings` is a frozen (`slots=True`) dataclass holding every configured value: `debug`, `max_download`, `max_attempts`, `auth_file`, `download_folder`, `audiobook_folder`, `folder_template`, `filename_template`, `encoding_format`, `bitrate`. It is the only thing that reads configuration, so the CLI, the coming API and the scheduler all work from the same validated object rather than loose keyword arguments.

- `__post_init__` runs `validate_templates`, `validate_encoding`, `validate_max_download` and `validate_max_attempts`, so **no** route in - `from_ini`, `dataclasses.replace`, a direct call - can produce settings that would fail part way through a run. It only validates; normalisation belongs to the builders, which keeps the field types honest
- `Settings.from_ini(path=DEFAULT_CONFIG_FILE)` reads the INI, passes every configured path through `resolve_path` and falls back to the documented default for each key. It raises `FileNotFoundError` on a missing file, because `configparser.read` ignores one and a mistyped path would otherwise run silently on defaults. `Settings.from_db` follows with the settings table
- Both `[folders]` keys have fallbacks. They used to have none, so an incomplete config died with a bare `KeyError` after the folders had already been created
- `max_attempts` is a plain `int`, not `int | None`: unlike `max_download` there is no "unlimited" reading, because retrying forever is the bug the cap exists to fix
- `auth_file` uses a `default_factory`, so `$HOME` is read when the settings are built rather than when the module is imported
- The two folder defaults are module constants (`DEFAULT_DOWNLOAD_FOLDER`, `DEFAULT_AUDIOBOOK_FOLDER`), already resolved, because ruff's RUF009 forbids a function call in a dataclass default
- `create_folders()` is the one side-effecting method: the two `mkdir(parents=True, exist_ok=True)` calls

### paths.py

`REPO_ROOT` (the directory containing `src/`, `/app` in the image) and `resolve_path(value)`, which expands `~` and anchors a relative path to `REPO_ROOT`. Absolute paths pass through unchanged and the result is deliberately **not** `resolve()`d: the audiobook folder is a bind mount under Docker and often a symlink to a network share.

It is its own module rather than part of `settings.py` because `database.py` needs `REPO_ROOT` too, and `Settings.from_db` will import `database.py`.

### model.py

`Book` is a dataclass and models both an Audible API item and a row of `library`.

**API fields:** `asin`, `title`, `subtitle`, `authors`, `narrators`, `series` (list of `{"title", "sequence"}`, either of which may be `None`), `genres`, `length` (minutes), `is_finished`, `percent_complete`, `date_added`, `release_date`, `cover_url`, `has_pdf`.

**Database-only fields**, `None` on a book that came straight from the API: `status`, `attempts`, `last_error`, `last_attempt_at`, `pdf_path`, `cover_path`, `annotations_path`, `encoding_format`, `downloaded_at`. `attempts` defaults to `0`, which must stay in step with the column default - a database test compares a whole freshly inserted `Book` for equality.

`BookStatus` is a `StrEnum` living here beside `Book`: `waiting_download`, `downloading`, `downloaded` and terminal `failed`. A `StrEnum` because a member *is* the text the column already stores, so no row had to be rewritten and a comparison against a plain string still holds. `_book_status` maps the column onto the enum and returns `None` for anything unrecognised rather than raising - a database written by another version has to stay readable, and nothing selects on the Python value because the queue is a SQL predicate.

`date_added` is an **ISO 8601 string** exactly as Audible returns it (e.g. `2024-01-01T00:00:00Z`), stored, sorted and compared as text. It is never parsed into a `datetime`.

`Book.from_row(row)` is the single owner of the JSON decode and the SQLite 0/1 to bool coercion. It reads by column name through `dict(row).get(...)`, so a column the row does not carry falls back to the field default and a legacy database still reads; only `asin` is required. The hand-written `__repr__` is kept on purpose - the generated one would put the cover URL and three file paths into every log line that formats a book. Dataclass field order deliberately does not mirror the table (`has_pdf` sits with the API fields); nothing is positional against a row any more.

### database.py

Single `library` table. `init_db()` creates it and then runs `_migrate_schema()`, which adds any missing columns to existing databases (currently `pdf_path`, `cover_path`, `annotations_path`, `has_pdf`, `encoding_format`, `downloaded_at`, `attempts`, `last_error`, `last_attempt_at`).

**Functions:**
- `init_db()`
- `update_books(books)` - an upsert: inserts new books at `waiting_download` and, `ON CONFLICT(asin)`, refreshes only the mutable API fields (title, subtitle, the four JSON lists, length, `is_finished`, `percent_complete`, `release_date`, `cover_url`, `has_pdf`). It deliberately never writes `date_added` - that is the incremental sync cursor, and moving it would skip or re-fetch purchases - nor `status`, the three retry columns, the accessory paths, `encoding_format` or `downloaded_at`, all of which belong to the downloader. Returns the count **inserted**, taken as `SELECT COUNT(*)` either side of the write on the same cursor: `cursor.rowcount` after an `executemany` of an upsert is `-1`, not a count, and `RETURNING` cannot be used with `executemany` at all. Handles a duplicate ASIN inside a single batch, which a check-then-insert could not. Keep the explicit column list rather than generating one from the dataclass; its `json.dumps` must stay in step with `Book.from_row`'s decode
- `get_books(limit=None) -> list[Book]` - all books, newest `date_added` first
- `get_books_to_download() -> list[Book]` - status `waiting_download`, oldest first. Does **not** claim; the caller claims each book individually before working on it
- `get_book_by_asin(asin) -> Book | None`
- `latest_date_added()` - `MAX(date_added)`, the incremental sync cursor. Independent of how `get_books` sorts
- `mark_book_downloaded(asin, encoding_format=None, *, pdf_path=, cover_path=, annotations_path=)` - sets status `downloaded`, `encoding_format` and `downloaded_at` (ISO 8601 UTC from `_utcnow()`, monkeypatch it in tests) and records the accessory paths in the same statement. Paths not given keep their current value. Clears `last_error` - a book that succeeded on its second attempt must not keep showing the first failure - but keeps `attempts`, a true record of what the book cost
- `claim_book_for_download(asin, *, stale_after=STALE_CLAIM_SECONDS) -> bool` - takes ownership in a single UPDATE: status to `downloading`, `attempts + 1`, `last_attempt_at` now, matching `waiting_download` **or** a `downloading` row whose `last_attempt_at` is older than `stale_after` (a NULL timestamp counts as stale, or such a row would never be picked up again). Returns whether this caller won. Two processes cannot both take one book: the second matches no rows. `STALE_CLAIM_SECONDS` is 6 hours - longer than the slowest real book, short enough that a crashed run recovers on the next tick rather than by hand
- `mark_book_failed(asin, error, *, max_attempts, terminal=False) -> BookStatus | None` - records `last_error` and decides retry-or-give-up **inside** the UPDATE (`CASE WHEN ? OR attempts >= ?`), from the `attempts` the claim already incremented, so it cannot race another process between a SELECT and an UPDATE. `terminal` short-circuits the count for a failure already known to be permanent. Returns the status the book landed in, or `None` for an unknown ASIN
- `release_book(asin)` - status back to `waiting_download` and `attempts - 1`, guarded on `downloading`. For an abort that is not the book's fault: expired credentials fail every book equally, so charging it to whichever book was next would eventually mark a good one `failed`
- `update_book_accessories(asin, pdf_path=, cover_path=, annotations_path=)` - accessory paths only; unused by the pipeline, kept for the API

**Reads return `Book` objects, not tuples.** `_get_connection()` sets `row_factory = sqlite3.Row` and every reader maps rows through `Book.from_row()`. Access is by column name, which is why `SELECT *` stays correct even on an older database where `_migrate_schema` appended columns in a different order than the DDL - positional indexing was silently wrong there. Never index a row positionally.

Note `_migrate_schema` only adds the nine later columns, so a database predating the rest of the schema still lacks `date_added`; `get_books`, `get_books_to_download` and `latest_date_added` all reference it and raise on that schema. `get_book_by_asin` works, because `from_row` falls back to field defaults. Widening the migration belongs with the status/state-machine work.

All functions close their connections (`contextlib.closing`). `init_db` also indexes `date_added`, and the download queue with a composite `(status, date_added)` matching the shape of its query - SQLite serves a plain status lookup from the leading column, so the superseded single-column `idx_library_status` is dropped rather than kept alongside. Guarded by the columns actually present so an old database still migrates: the composite raises on a schema with no `date_added`.

### audible.py

`Audible(auth_file)` wraps `audible.Authenticator` and `audible.Client`.

- `get_library(purchased_after=None)` - follows pagination until a short page comes back, so a library over 1000 titles syncs fully
- `get_book(asin)` - single book, same response groups. No caller yet
- `RESPONSE_GROUPS` - the nine groups `_prepare_book` actually reads, shared by both calls
- `_prepare_book(item)` - maps an API item to `Book`, reading every optional field defensively. Prefers the 1215px cover, falls back to 500px. Sets `has_pdf` from `pdf_url`
- `_prepare_books(items)` - maps a page and skips (with a logged traceback) any single item that cannot be read, so one odd podcast or unnumbered series entry does not abort the run

### sync.py

`sync_library(audible) -> int`. If the DB is empty, fetch everything; otherwise fetch books purchased after the newest `date_added` in the DB. Returns the number of new books inserted.

### downloader.py

The largest module. Key pieces:

**`Downloader(audible)`**
- `get_license_response(asin, quality)` - quality is `"High"` (the correct value for AAXC downloads). Raises `LicenseError` unless the response says `Granted`; a denied license is a normal 200 with no `content_metadata`
- `get_download_link(license_response)` (static)
- `download_file(url, filename)` (static) - streams through the shared client; raises on HTTP errors
- `get_chapter_info(asin)` - `content/{asin}/metadata` with `chapter_info`. Returns `None` on `httpx.HTTPError`
- `download_book(book, temp_dir)` - license, decrypted voucher (written before the download so a key problem is found early), AAXC download, chapters. Returns a `DownloadedBook(aaxc, voucher, chapters)`
- `download_pdf(asin, path)` - `https://www.audible.{domain}/companion-file/{asin}` via the authenticated session; checks content type
- `download_cover(url, path)` - via the **unauthenticated** shared client: the audible session signs every request, which would send the account's ADP token to the image CDN
- `download_annotations(asin, path)` - Amazon sidecar endpoint; only writes a file if clips or bookmarks exist

**Accessory contract:** the three accessory methods return `False` only when the thing is genuinely absent (404, non-PDF content type, no clips or bookmarks). Every other failure raises, so the book goes back to the queue and is retried (up to `max-attempts`) instead of being filed as complete with a `NULL` path that nothing would ever fix.

**HTTP:** `get_http_client()` is a shared `httpx.Client` with a 30s connect / 120s read timeout and redirects followed. httpx defaults to 5s, which aborted a part-finished multi-gigabyte download on any brief CDN stall. `_stream_to_file(response, path, desc)` is the one streaming loop for all three downloads; it writes to a `.part` file and renames on completion.

**Chapters:** `flatten_chapters(chapters)` descends into the nested `chapters` list Audible returns for books split into parts. Taking only the top level left a multi-part book with a few hours-long "Part One" markers instead of its real chapters.

**Metadata and decryption**
- `generate_metadata(book)` - takes a `Book`; title/album, artist/album_artist/author, composer (narrators), series, genre, year, ASIN comment
- `write_ffmpeg_metadata_file(metadata, path, chapters=None)` - writes an FFMETADATA1 file including `[CHAPTER]` blocks, escaping through `_escape_ffmetadata`. A newline is escaped as a backslash followed by the real newline; writing the two characters `\` and `n` would be read back as a literal `n`
- `decrypt_aaxc(book, voucher, book_data=None, cover_path=None, chapters=None, *, encoding_format="m4b", bitrate=64)` - runs `ffmpeg` via `subprocess` with `-audible_key`/`-audible_iv` and raises on non-zero exit. The argv comes from one of two helpers:
  - `_m4b_ffmpeg_args` - `-c:a copy`, cover mapped as `attached_pic`, metadata + `[CHAPTER]` blocks from the FFMETADATA file via `-map_metadata`/`-map_chapters`. `-map_chapters` is only passed when there **are** chapters: pointing it at a chapterless metadata file discards the chapter track the AAXC itself carries, which FFmpeg would otherwise have copied. The MP4 muxer only writes the keys it knows and drops `series`, `series-part`, `author` and `media_type`; after FFmpeg succeeds `decrypt_aaxc` calls `write_m4b_extra_tags` (in `src/encoding.py`) to add those as iTunes freeform atoms. Do not use `-movflags use_metadata_tags` for this: it keeps every key but removes the embedded cover (verified 2026-09-08)
  - `_opus_ffmpeg_args` - `-c:a libopus -b:a {bitrate}k -vbr on` into the `oga` muxer. Ogg has no picture stream or chapter track, so the cover goes in as a `METADATA_BLOCK_PICTURE` tag and chapters as `CHAPTERxxx`/`CHAPTERxxxNAME` tags (built by `src/encoding.py`) inside the FFMETADATA file, and `-map_chapters -1` stops FFmpeg copying the AAXC's own chapters on top of them. FFmpeg renames `comment` to `DESCRIPTION` and `album_artist` to `ALBUMARTIST` in Ogg

**Orchestration**
- `_process_book(downloader, book, temp_dir, settings)` - the full pipeline for one book: download, accessories, decrypt, release the AAXC, file into the library, mark downloaded with the accessory paths in one statement. Raises on any failure.
- `_download_accessories(downloader, book, temp_dir, safe_title)` - PDF, cover and annotations, keyed by the database column they belong to. The cover extension comes from the image bytes (`image_info`), not the URL, because plenty of cover URLs carry no extension and the name is kept permanently.
- `_resolve_output_path(final_folder, stem, extension, asin)` - keeps two books that render to the same name apart. Every output carries its own ASIN in the comment tag, so a file belonging to this book is reused and anything else gets ` [{asin}]` appended. Without this the second book silently overwrote the first (the live library has two *Red Rising* ASINs that collide).
- The finished book is **moved**, not copied, and the AAXC and voucher are deleted straight after decryption, which keeps peak disk at roughly one copy of the book rather than three.
- `download_books(audible, settings)` - loops over waiting books, up to `settings.max_download`. Each book is **claimed** (`claim_book_for_download`) before it is touched, one at a time rather than as a batch, so a scheduler tick starting mid-run skips a book another process holds and only books really tried spend an attempt. A lost claim still costs a slot in the `max_download` slice: that is a cap on work attempted, not a quota. Three failure arms, and order matters - `LicenseError` subclasses `RuntimeError`, so its arm must sit above `except Exception`:
  - `Unauthorized`/`NoRefreshToken`/`AuthFlowError` **breaks** the loop and calls `release_book` first: every remaining book would fail the same way, and an expiring token must not burn a good book's attempts
  - `LicenseError` calls `mark_book_failed(..., terminal=True)` - a refused licence will not be granted on the third ask, and each retry re-downloaded the whole AAXC before failing again
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
- `book_template_values(book)`, `render_template`, `validate_templates`, `book_output_paths` - the template engine. A sequence with no series title is treated as empty, so a book never lands in a bare `{author}/2 - {title}/` folder.

### encoding.py

Everything format-specific that is not an ffmpeg flag. `FORMATS` maps `m4b`/`oga` to extensions; `write_m4b_extra_tags(path, metadata)` opens a finished M4B with mutagen and adds every metadata key outside `MP4_NATIVE_KEYS` as a `----:com.apple.iTunes:<key>` freeform atom (plus `stik=2` for audiobooks), returning the keys written; `validate_encoding(format, bitrate)` raises `ValueError` at startup for an unknown format (via `output_extension`, so the message is defined once) or a bitrate outside 1..256 kbps (libopus rejects more). `read_embedded_asin(path)` reads the ASIN back out of a finished M4B or OGA from the `comment` tag every output carries, which is how `_resolve_output_path` tells a retry of the same book from a different book with the same name. `image_info(bytes)` reads MIME, width, height and depth from JPEG (SOF marker) or PNG (IHDR) headers with the stdlib, returning zeros for anything else. `picture_block(path)` packs the FLAC-style picture block (type 3, MIME, "Cover Artwork", dimensions, data) and returns unwrapped base64 for the `METADATA_BLOCK_PICTURE` tag. `chapter_tags(chapters)` turns Audible chapters into `CHAPTER000=HH:MM:SS.mmm` / `CHAPTER000NAME=` pairs. No escaping here; `write_ffmpeg_metadata_file` escapes when writing.

### api.py

FastAPI stub with `GET /` and `POST /sync`. The sync endpoint calls `sync_library()` without a client and crashes. Milestone 3 work; do not build on it yet.

## Configuration

`config/config.ini`:

```ini
[general]
debug = true            ; sets the log level to DEBUG

[sync]
; max-download = 10     ; limit books processed per run, 1 or more; unset = all waiting
; max-attempts = 3      ; tries before a book is marked failed, 1 or more; default 3
; audible-auth-file = audible.json   ; default ~/.audible/audible.json

[folders]
downloads = data/downloads
audiobooks = audiobooks

[encoding]
; format = oga          ; opt-in Ogg Opus re-encode; unset = m4b stream copy, no re-encoding
; bitrate = 64          ; kbps, oga only, 1-256
```

Read in `src/settings.py` with `configparser`, into a frozen `Settings`. Relative paths are anchored to the repo root, so the app can be started from any working directory. The config file is copied into the Docker image, so committed values become the image defaults. Users override by mounting `./config` (see `compose.yml`).

## Development

### Setup

```bash
git clone https://github.com/alexstansfield/audible-sync.git
cd audible-sync
uv sync                       # installs runtime + dev tools (pytest, ruff); pip users: pip install -r requirements.txt
pip install audible-cli && audible quickstart   # creates ~/.audible/audible.json
uv run python -m src.main
```

**Dependencies:** `pyproject.toml` lists only direct dependencies (`audible`, `httpx`, `tqdm`, `mutagen`, `fastapi`, `uvicorn`) plus a `dev` group. After changing it run `uv lock` and regenerate `requirements.txt` with the export command shown in the tree above. The Dockerfile installs with `--no-dev`.

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
- For `download_books`, patch `get_books_to_download`, `claim_book_for_download`, `mark_book_downloaded`, `mark_book_failed`, `release_book`, `Downloader.download_book` and `decrypt_aaxc`, then assert on the resulting files and calls (see `_patch_pipeline`). All of these are looked up as attributes of `src.downloader`, so patch them there - miss one and the test writes to the real library database. `_patch_pipeline` takes optional `claimed=` and `failures=` recorder lists. It takes `(audible, settings)`; build the settings with `make_settings(download_folder=..., audiobook_folder=...)`. This is how the per-book error handling was verified. `Downloader.download_book` takes `(book: Book, temp_dir)` and returns a `DownloadedBook`.
- For `Audible`, build items with a helper and pass a fake client that replays canned pages; never construct a real `Authenticator`. See `tests/test_audible.py`.
- `tests/conftest.py` holds the shared `make_book()` and `make_settings()` factories. `make_settings(**overrides)` is `dataclasses.replace(Settings(), **overrides)`, so an override that would not survive `from_ini` still raises; import it as `from tests.conftest import make_book` (`tests` is in ruff's `known-first-party`). It returns a real `Book` with decoded lists - never JSON strings, which is what the old row-tuple helpers built. Keep other fixtures small and inline.

The suite currently covers the sanitizer, metadata generation, the FFMETADATA writer, the ffmpeg argv for both formats, the encoding helpers, the per-book error handling in `download_books`, and the database layer. If you touch a module that has no tests yet, add the tests for the part you touched rather than for the whole module.

### Manual integration checklist

- [ ] Full sync on an empty database
- [ ] Incremental sync on an existing database
- [ ] Download with and without `max-download`
- [ ] Series and non-series books land in the right folders
- [ ] A title with `:` or `/` produces a sane path
- [ ] A failing book is skipped, its temp folder removed, and the run continues
- [ ] A book that fails `max-attempts` times is marked `failed`, with `last_error` set, and is not picked up on the next run
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

- `Dockerfile`: `python:3.12-slim-trixie`, installs uv and ffmpeg, `uv sync --locked`, `CMD uv run python -m src.main`
- `compose.yml`: mounts `~/.audible`, `./data`, `./audiobooks`; optional `./config` mount
- Run with `docker compose run audible-sync`. Note `restart: unless-stopped` is set, so `docker compose up` would restart the one-shot process in a loop.
- `.dockerignore` does not exclude `data/`, `audiobooks/`, `venv/` or `audible.json`; keep those out of the build context when building locally.
- Image: `ghcr.io/alexstansfield/audible-sync:latest` and `:vX.Y.Z`, published on `v*` tags

## Continuous Integration

Two workflows in `.github/workflows/`:

- `ci.yaml` runs on every push and pull request to `dev` and `main`: installs with `uv sync --locked`, then `ruff check .`, `ruff format --check .` and `pytest`. A PR is not mergeable until it passes.
- `docker-publish.yaml` runs on `v*` tags: builds the image, pushes `latest` and the version tag to ghcr.io, and creates a GitHub release.

## Code Conventions

- `snake_case` functions, `PascalCase` classes, `_leading_underscore` for private helpers
- Import order: standard library, third-party, local (`from src.x import ...`)
- Use `logging.getLogger(__name__)`; never `print`
- Type hints and docstrings on new public functions
- Anything that becomes a path goes through `sanitize_filename`
- Failures inside per-book processing should raise; `download_books` handles them
- Every behaviour change ships with pytest unit tests (see Testing)
- Code is ruff-formatted and ruff-clean before commit (see Linting and formatting)

## Known Issues and Cleanup Candidates

- `src/api.py` is broken (see above)
- Root `main.py` is a uv scaffold leftover
- `requirements.txt` is generated from the lockfile and will drift if `uv lock` runs without re-exporting
- Test coverage is thin outside `downloader.py` and `database.py`; `audible.py` and `sync.py` have no tests yet
- Two books whose templates render to the same name are filed side by side (` [{asin}]` suffix) rather than merged; the naming template is what actually needs disambiguating
- `Downloader`'s network-facing methods still have no unit tests
- The structural work still listed under **Milestone 3 Step 0** in `todo.md`: no `sync_runs` table

## Roadmap

See `todo.md` for the authoritative list.

**Milestone 1 - complete:** sync, download, decrypt, config, Docker, CI.

**Milestone 2 - complete:** logging, PDF/cover/annotations, metadata and chapter embedding, configurable file naming, and Ogg Opus encoding with a bitrate setting. Async download progress is still listed under it in `todo.md` but only pays off with a web UI; recommended to move to Milestone 3.

**Milestone 3 - planned:** settings table, background scheduler, FastAPI service, Audible login flow, web UI. Planned schema additions: a `settings` table and a `sync_runs` table (`encoding_format` and `downloaded_at` already exist). `todo.md` opens the milestone with a **Step 0** section of structural changes that each block one of the requirements; start there.

## Understanding "Sync"

"Sync" means keeping the local audiobook collection in step with the Audible library: metadata into the database, then files onto disk. Both happen in one run. Milestone 3 turns this into a scheduled background service with a manual trigger.

---

**Document Version:** 4.3
**Last Updated:** 2026-09-09
**Codebase Version:** Milestone 2 complete; Milestone 3 Step 0 in progress (database returns objects, settings object, book state machine)
**Primary Branch:** `dev`
