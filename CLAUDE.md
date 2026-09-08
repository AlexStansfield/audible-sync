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
| **Lines of Code** | ~1200 lines across 8 Python modules, plus ~550 lines of tests |
| **Logging** | Python `logging`, configured in `src/main.py` |
| **Testing** | pytest (`tests/`), run with `uv run pytest`; unit tests required for new code |
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

Milestone 3 (API service, scheduler, web UI) is next. See `todo.md`.

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
│   ├── main.py               # Entry point, logging setup, orchestration
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
│   ├── test_naming.py        # Template rendering, optional groups, default layout, validation
│   └── test_encoding.py      # Format validation, JPEG/PNG header parsing, picture block, chapter tags
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
  ├─→ database.py   (persistence)
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
- Config and DB paths are relative to the working directory, so run from the repo root

## Key Modules

### main.py

- Configures logging (INFO level, hardcoded; the `debug` config flag is not read)
- Loads `config/config.ini`, initialises the DB, creates the download and audiobook folders
- Resolves the auth file: `[sync] audible-auth-file`, else `~/.audible/audible.json`
- Runs `sync_library()` then `download_books()` with the optional `max-download` limit

This is a one-shot run: sync + download, then exit.

### model.py

`Book` is a plain data container. Fields: `asin`, `title`, `subtitle`, `authors`, `narrators`, `series` (list of `{"title", "sequence"}`), `genres`, `length` (minutes), `is_finished`, `percent_complete`, `date_added`, `release_date`, `cover_url`, `has_pdf`. Status is stored only in the database.

### database.py

Single `library` table. `init_db()` creates it and then runs `_migrate_schema()`, which adds any missing columns to existing databases (currently `pdf_path`, `cover_path`, `annotations_path`, `has_pdf`, `encoding_format`, `downloaded_at`).

**Functions:**
- `init_db()`
- `update_books(books)` - insert new ASINs only, status `waiting_download`; returns count inserted
- `get_books(limit=None)` - all books, newest `date_added` first
- `get_books_to_download()` - status `waiting_download`, oldest first
- `get_book_by_asin(asin)`
- `mark_book_downloaded(asin, encoding_format=None)` - sets status `downloaded`, `encoding_format` and `downloaded_at` (ISO 8601 UTC from `_utcnow()`, monkeypatch it in tests)
- `update_book_accessories(asin, pdf_path=, cover_path=, annotations_path=)`

**Rows are tuples, not Book objects.** Column indices:

```
0:  asin              9:  percent_complete
1:  title             10: date_added
2:  subtitle          11: release_date
3:  authors (JSON)    12: cover_url
4:  narrators (JSON)  13: status
5:  series (JSON)     14: pdf_path
6:  genres (JSON)     15: cover_path
7:  length            16: annotations_path
8:  is_finished       17: has_pdf
                      18: encoding_format
                      19: downloaded_at
```

Read functions do not close their connections; write functions do.

### audible.py

`Audible(auth_file)` wraps `audible.Authenticator` and `audible.Client`.

- `get_library(purchased_after=None)` - up to 1000 items sorted by purchase date, with an extensive `response_groups` list
- `get_book(asin)`
- `_prepare_book(item)` - maps an API item to `Book`. Prefers the 1215px cover, falls back to 500px. Sets `has_pdf` from `pdf_url`.

### sync.py

`sync_library(audible) -> int`. If the DB is empty, fetch everything; otherwise fetch books purchased after the newest `date_added` in the DB. Returns the number of new books inserted.

### downloader.py

The largest module. Key pieces:

**Path helpers**
- `sanitize_filename(name, fallback="Unknown")` - makes a single path segment safe on Linux, macOS, Windows and SMB. `:` becomes ` -`, `/` and `\` become `-`, other invalid characters are dropped, whitespace collapsed, length capped at 150. Use it for every title, author, series or sequence that becomes part of a path.
- `temp_book_folder(download_folder, asin, title)` - the per-book working folder `downloads/{asin}_{safe_title}/`

**`Downloader(audible)`**
- `get_license_response(asin, quality)` - quality is `"High"` (the correct value for AAXC downloads)
- `get_download_link(license_response)` (static)
- `download_file(url, filename)` (static) - streams with a tqdm bar, raises on HTTP errors, tolerates a missing Content-Length
- `get_chapter_info(asin)` - `content/{asin}/metadata` with `chapter_info`
- `download_book(book, folder)` - license, AAXC download, decrypted voucher JSON, chapters. Returns `{"book", "voucher", "chapters"}` or `None` if no license.
- `download_pdf(asin, path)` - `https://www.audible.{domain}/companion-file/{asin}` via the authenticated session; checks content type
- `download_cover(url, path)`
- `download_annotations(asin, path)` - Amazon sidecar endpoint; only writes a file if clips or bookmarks exist

**Metadata and decryption**
- `generate_metadata(book_tuple)` - title/album, artist/album_artist/author, composer (narrators), series, genre, year, ASIN comment
- `write_ffmpeg_metadata_file(metadata, path, chapters=None)` - writes an FFMETADATA1 file including `[CHAPTER]` blocks
- `decrypt_aaxc(book, voucher, book_data=None, cover_path=None, chapters=None, *, encoding_format="m4b", bitrate=64)` - runs `ffmpeg` via `subprocess` with `-audible_key`/`-audible_iv` and raises on non-zero exit. The argv comes from one of two helpers:
  - `_m4b_ffmpeg_args` - `-c:a copy`, cover mapped as `attached_pic`, metadata + `[CHAPTER]` blocks from the FFMETADATA file via `-map_metadata`/`-map_chapters`
  - `_opus_ffmpeg_args` - `-c:a libopus -b:a {bitrate}k -vbr on` into the `oga` muxer. Ogg has no picture stream or chapter track, so the cover goes in as a `METADATA_BLOCK_PICTURE` tag and chapters as `CHAPTERxxx`/`CHAPTERxxxNAME` tags (built by `src/encoding.py`) inside the FFMETADATA file, and `-map_chapters -1` stops FFmpeg copying the AAXC's own chapters on top of them. FFmpeg renames `comment` to `DESCRIPTION` and `album_artist` to `ALBUMARTIST` in Ogg

**Orchestration**
- `_process_book(downloader, book, temp_dir, audiobook_folder, folder_template, filename_template, encoding_format, bitrate)` - the full pipeline for one book: download, accessories, decrypt, file into the library, record accessory paths, mark downloaded. Raises on any failure.
- `download_books(audible, download_folder, audiobook_folder, max=None, *, folder_template, filename_template, encoding_format, bitrate)` - loops over waiting books. Each book runs inside try/except/finally: failures are logged with a traceback, the temp folder is always removed, the book keeps `waiting_download` and is retried next run. Ends with a succeeded/failed summary.

**Final layout** comes from the `[naming]` templates in `config.ini`, rendered by `src/naming.py`:
- `folder` (default `{author}/[{series}/][{sequence} - ]{title}`) and `filename` (default `{title}`); `[...]` groups are dropped when any placeholder inside is empty, empty segments are skipped, every value is sanitized
- Defaults give `audiobooks/{author}/{series}/{sequence} - {title}/{title}.m4b`, or `audiobooks/{author}/{title}/{title}.m4b` without a series (`.oga` when `[encoding] format = oga`)
- Missing author → `Unknown Author`; empty folder or filename → ASIN; unknown placeholder → `ValueError` at startup (`validate_templates` in `main.py`)
- PDF, `{filename}_cover.jpg` and `{filename}_annotations.json` sit next to the audio file; the extension comes from `output_extension()` in `src/encoding.py`

### encoding.py

Everything format-specific that is not an ffmpeg flag. `FORMATS` maps `m4b`/`oga` to extensions; `validate_encoding(format, bitrate)` raises `ValueError` at startup for an unknown format or a bitrate outside 1..256 kbps (libopus rejects more). `image_info(bytes)` reads MIME, width, height and depth from JPEG (SOF marker) or PNG (IHDR) headers with the stdlib, returning zeros for anything else. `picture_block(path)` packs the FLAC-style picture block (type 3, MIME, "Cover Artwork", dimensions, data) and returns unwrapped base64 for the `METADATA_BLOCK_PICTURE` tag. `chapter_tags(chapters)` turns Audible chapters into `CHAPTER000=HH:MM:SS.mmm` / `CHAPTER000NAME=` pairs. No escaping here; `write_ffmpeg_metadata_file` escapes when writing.

### api.py

FastAPI stub with `GET /` and `POST /sync`. The sync endpoint calls `sync_library()` without a client and crashes. Milestone 3 work; do not build on it yet.

## Configuration

`config/config.ini`:

```ini
[general]
debug = true            ; currently unused

[sync]
; max-download = 10     ; limit books processed per run; unset = all waiting
; audible-auth-file = audible.json   ; default ~/.audible/audible.json

[folders]
downloads = data/downloads
audiobooks = audiobooks

[encoding]
format = m4b            ; m4b (stream copy) or oga (Ogg Opus re-encode)
bitrate = 64            ; kbps, oga only, 1-256
```

Read in `main.py` with `configparser`. The config file is copied into the Docker image, so committed values become the image defaults. Users override by mounting `./config` (see `compose.yml`).

## Development

### Setup

```bash
git clone https://github.com/alexstansfield/audible-sync.git
cd audible-sync
uv sync                       # installs runtime + dev tools (pytest, ruff); pip users: pip install -r requirements.txt
pip install audible-cli && audible quickstart   # creates ~/.audible/audible.json
uv run python -m src.main
```

**Dependencies:** `pyproject.toml` lists only direct dependencies (`audible`, `httpx`, `tqdm`, `fastapi`, `uvicorn`) plus a `dev` group. After changing it run `uv lock` and regenerate `requirements.txt` with the export command shown in the tree above. The Dockerfile installs with `--no-dev`.

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
- Prefer pure functions. `sanitize_filename`, `generate_metadata`, `write_ffmpeg_metadata_file`, everything in `encoding.py` and the path-building logic are directly testable with no setup.
- For `decrypt_aaxc`, monkeypatch `downloader.subprocess.run` with a recorder (see the `fake_ffmpeg` fixture) and assert on the argv and on the FFMETADATA file, which the recorder must read before `decrypt_aaxc` deletes it.
- Never hit the Audible API, the network or FFmpeg in tests. Mock `Audible`/`Downloader` methods and `subprocess.run` with `unittest.mock` or `monkeypatch`.
- For database tests point `src.database.DB_FILE` at a temp file (`tmp_path` fixture) and call `init_db()`.
- For `download_books`, patch `get_books_to_download`, `mark_book_downloaded`, `update_book_accessories`, `Downloader.download_book` and `decrypt_aaxc`, then assert on the resulting files and calls. This is how the per-book error handling was verified.
- Keep fixtures small and inline; a shared `conftest.py` is fine once two files need the same one.

The suite currently covers the sanitizer, metadata generation, the FFMETADATA writer, the ffmpeg argv for both formats, the encoding helpers, the per-book error handling in `download_books`, and the database layer. If you touch a module that has no tests yet, add the tests for the part you touched rather than for the whole module.

### Manual integration checklist

- [ ] Full sync on an empty database
- [ ] Incremental sync on an existing database
- [ ] Download with and without `max-download`
- [ ] Series and non-series books land in the right folders
- [ ] A title with `:` or `/` produces a sane path
- [ ] A failing book is skipped, its temp folder removed, and the run continues
- [ ] Cover, chapters and metadata visible in the M4B (e.g. `ffprobe`)
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
- `debug` config flag is unused; log level is hardcoded
- Read functions in `database.py` leak connections (harmless for a one-shot CLI)

## Roadmap

See `todo.md` for the authoritative list.

**Milestone 1 - complete:** sync, download, decrypt, config, Docker, CI.

**Milestone 2 - complete:** logging, PDF/cover/annotations, metadata and chapter embedding, configurable file naming, and Ogg Opus encoding with a bitrate setting. Async download progress is still listed under it in `todo.md` but only pays off with a web UI; recommended to move to Milestone 3.

**Milestone 3 - planned:** settings table, background scheduler, FastAPI service, Audible login flow, web UI. Planned schema additions: a `settings` table and a `sync_runs` table (`encoding_format` and `downloaded_at` already exist).

## Understanding "Sync"

"Sync" means keeping the local audiobook collection in step with the Audible library: metadata into the database, then files onto disk. Both happen in one run. Milestone 3 turns this into a scheduled background service with a manual trigger.

---

**Document Version:** 3.2
**Last Updated:** 2026-09-08
**Codebase Version:** Milestone 2 complete (branch feature/oga-encoding)
**Primary Branch:** `dev`
