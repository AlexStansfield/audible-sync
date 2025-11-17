# CLAUDE.md - AI Assistant Guide for Audible-Sync

This document provides comprehensive guidance for AI assistants working with the audible-sync codebase.

## Quick Reference

| Aspect | Details |
|--------|---------|
| **Language** | Python 3.11 |
| **Type** | CLI Application (API planned for Milestone 3) |
| **Entry Point** | `python -m src.main` |
| **Database** | SQLite 3 (`data/audible_sync.db`) |
| **Config** | INI format (`config/config.ini`) |
| **Lines of Code** | ~435 lines across 7 Python modules |
| **Testing** | None (manual testing required) |
| **Package Manager** | pip + requirements.txt |

## Project Overview

**Purpose:** Sync Audible library, download audiobooks, and decrypt them to DRM-free M4B/OGA files with full metadata.

**Inspiration:** This project is inspired by [BALD (Bash Audible Library Downloader)](https://github.com/damajor/BALD), a bash script that uses audible-cli. This Python implementation uses the same underlying `audible` library but aims to create a more user-friendly service with a web UI and automated scheduling.

**Current State (Milestone 1):**
- CLI application that runs sync + download in single operation
- Incremental library synchronization from Audible API
- Automatic download and DRM decryption of owned audiobooks
- File organization by author/series
- Docker support for easy deployment

**Vision (Future Milestones):**
- Background service with scheduled automatic syncs
- Web UI for library viewing, settings management, and manual sync triggers
- Comprehensive metadata including PDFs, covers, annotations
- OGA encoding option for smaller file sizes
- Concurrent downloads for faster processing

**Key Features:**
- Incremental library synchronization from Audible API
- Automatic download of owned audiobooks
- DRM decryption (legal - only for user's owned content)
- Progress tracking and status management
- Organized file structure by author/series
- Docker support for easy deployment

**Important:** This app does NOT crack DRM illegally. It only decrypts audiobooks the user owns.

## Codebase Structure

```
/home/user/audible-sync/
├── .github/workflows/
│   └── docker-publish.yaml      # CI/CD: Build & push Docker images on version tags
├── config/
│   └── config.ini              # Application configuration (INI format)
├── data/
│   ├── audible_sync.db         # SQLite database (created at runtime)
│   └── downloads/              # Temporary download directory
├── src/                        # Source code (all modules)
│   ├── main.py                 # Entry point & orchestration (35 lines)
│   ├── model.py                # Book data model (24 lines)
│   ├── database.py             # SQLite operations (87 lines)
│   ├── audible.py              # Audible API client (74 lines)
│   ├── sync.py                 # Library sync logic (16 lines)
│   ├── downloader.py           # Download & decryption (187 lines)
│   └── api.py                  # FastAPI service (12 lines, incomplete)
├── compose.yml                 # Docker Compose configuration
├── Dockerfile                  # Container build instructions
├── requirements.txt            # Python dependencies (pinned versions)
├── README.md                   # User documentation
└── todo.md                     # Development roadmap
```

## Architecture

### Design Pattern
**Simple Modular Architecture** with clear separation of concerns:

```
main.py (Orchestrator)
  ├─→ database.py (Persistence Layer)
  ├─→ audible.py (API Integration Layer)
  ├─→ sync.py (Business Logic Layer)
  └─→ downloader.py (Download & Conversion Layer)
```

### Data Flow
```
Audible API → audible.py → sync.py → database.py → SQLite
                                    ↓
                            downloader.py → FFmpeg → M4B Files
```

### Module Organization
- **No `__init__.py` files** - Not a packaged library, just a runnable application
- **Flat structure** - All modules in `src/` directory
- **Absolute imports** - Use `from src.module import ...`
- **Module execution** - Run via `python -m src.main`

## Key Modules

### 1. main.py (Entry Point)
**Location:** `/home/user/audible-sync/src/main.py`

**Responsibilities:**
- Load configuration from INI file
- Initialize database and create directories
- Set up Audible API client (handles auth file path resolution)
- Orchestrate unified sync and download workflow
- Respect `max-download` configuration limit

**Execution Flow:**
```python
1. Load config.ini
2. Initialize database (create tables if needed)
3. Create folders (downloads, audiobooks)
4. Initialize Audible client with auth file
5. Sync library metadata (incremental)
6. Download books with status='waiting_download' (up to max-download limit)
```

**Important Notes:**
- This is a **unified operation** - sync + download happens in one run
- Uses print statements for user feedback (logging planned for Milestone 2)
- Currently a one-shot CLI execution (service mode planned for Milestone 3)

### 2. model.py (Data Model)
**Location:** `/home/user/audible-sync/src/model.py`

**Class: Book**
```python
class Book:
    asin: str                    # Primary identifier (Audible Standard ID Number)
    title: str                   # Book title
    subtitle: str = ""           # Optional subtitle
    authors: List[str] = []      # Author names
    narrators: List[str] = []    # Narrator names
    series: List[Dict] = []      # [{"title": "...", "sequence": "..."}]
    genres: List[str] = []       # Genre classifications
    length: int = 0              # Runtime in minutes
    is_finished: bool = False    # User completion status
    percent_complete: float = 0.0 # Progress percentage
    date_added: datetime         # When added to library
    release_date: str            # Publication date
    cover_url: str = ""          # Cover image URL (500px version)
```

**No Database Logic:** Pure data container (status field stored separately in DB)

### 3. database.py (Persistence)
**Location:** `/home/user/audible-sync/src/database.py`

**Database:** SQLite 3 file-based database

**Schema:**
```sql
CREATE TABLE library (
    asin TEXT PRIMARY KEY,
    title TEXT,
    subtitle TEXT,
    authors JSON,              -- Serialized list
    narrators JSON,            -- Serialized list
    series JSON,               -- Serialized list of objects
    genres JSON,               -- Serialized list
    length INTEGER,
    is_finished BOOLEAN,
    percent_complete REAL,
    date_added TEXT,           -- ISO format
    release_date TEXT,
    cover_url TEXT,
    status TEXT                -- 'waiting_download' or 'downloaded'
)
```

**Key Functions:**
- `init_db()` - Create library table if not exists
- `update_books(books: List[Book])` - Insert new books (skip existing ASINs)
- `get_books(limit=None)` - Fetch books sorted by date_added DESC
- `get_books_to_download()` - Filter books with status='waiting_download'
- `mark_book_downloaded(asin)` - Update status to 'downloaded'

**IMPORTANT:** Functions return tuples, not Book objects. Access by index:
- `book[0]` = asin
- `book[1]` = title
- etc.

**Connection Management:** Private `_get_connection()` function, closes after each operation

### 4. audible.py (API Integration)
**Location:** `/home/user/audible-sync/src/audible.py`

**Class: Audible**
```python
Audible(auth_file: str)
```

**Methods:**
- `get_library(purchased_after: datetime = None)` - Fetch library with optional date filter
- `get_book(asin: str)` - Fetch single book details
- `_prepare_book(item)` - Transform API response to Book object (private helper)

**API Configuration:**
- **Extensive metadata:** Uses comprehensive response_groups for full book details
- **Sorting:** By PurchaseDate descending
- **Pagination:** Max 1000 results (Audible API limit)
- **Image Quality:** Requests 500px cover images

**Authentication:** Requires `audible.json` file (generated via audible-cli tool)

### 5. sync.py (Business Logic)
**Location:** `/home/user/audible-sync/src/sync.py`

**Function:**
```python
sync_library(audible: Audible) -> int
```

**Logic:**
1. Check if database has existing books
2. **If empty:** Fetch entire library (initial sync)
3. **If not empty:** Fetch only books purchased after latest book's date_added (incremental)
4. Update database with new books
5. Return count of newly synced books

**Optimization:** Incremental sync reduces API calls and data transfer

### 6. downloader.py (Download & Conversion)
**Location:** `/home/user/audible-sync/src/downloader.py`

**Largest module (187 lines)** - Core download and decryption functionality

**Class: Downloader**
```python
Downloader(audible: Audible)
```

**Methods:**
- `get_license_response(asin, quality)` - Request DRM license from Audible
- `download_book(book, folder)` - Download AAXC file and voucher JSON
- `get_download_link(license_response)` (static) - Extract download URL
- `download_file(url, destination)` (static) - Download with progress bar (tqdm)

**Functions:**
- `decrypt_aaxc(book, voucher_file, output_folder)` - FFmpeg-based decryption to M4B
- `download_books(audible, download_folder, audiobook_folder, max_download)` - Main orchestrator

**File Organization:**
- **With series:** `audiobooks/{author}/{series_title}/{sequence} - {title}/{title}.m4b`
- **Without series:** `audiobooks/{author}/{title}/{title}.m4b`

**Current Process Flow (Milestone 1):**
1. Get books to download from database
2. For each book:
   - Request license (DRM key/IV)
   - Download AAXC file + voucher JSON
   - Decrypt AAXC to M4B using FFmpeg
   - Move M4B to organized folder structure
   - Cleanup temporary files
   - Mark as downloaded in database

**Planned Enhancements (Milestone 2):**
The download process will be enhanced to include:
- Download PDFs (companion materials) if available
- Download high-resolution cover images
- Download user annotations and bookmarks
- Generate comprehensive metadata
- Embed metadata and cover art into M4B files
- Optional OGA encoding (smaller file size with Opus codec)
- Store accessories (PDF, cover, annotations) alongside audiobook

**Inspiration:** See [BALD's approach](https://github.com/damajor/BALD/blob/master/BALD.sh) for reference implementation

**Progress Tracking:** Uses tqdm for visual download progress

**Quality:** Fixed at "Extreme" (highest available)

### 7. api.py (Future Feature)
**Location:** `/home/user/audible-sync/src/api.py`

**Status:** Incomplete (Milestone 3)

**Framework:** FastAPI + Uvicorn

**Current Endpoints:**
- `GET /` - Welcome message
- `POST /sync` - Trigger sync (BROKEN - missing audible parameter)

**Not Production Ready:** Do not rely on this module

## Development Workflows

### Setting Up Development Environment

**Prerequisites:**
- Python 3.11+
- FFmpeg (system dependency)
- Audible account with `audible.json` auth file

**Steps:**
```bash
# Clone repository
git clone https://github.com/alexstansfield/audible-sync.git
cd audible-sync

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up auth file (using audible-cli)
pip install audible-cli
audible quickstart  # Creates ~/.audible/audible.json

# Run application
python -m src.main
```

### Running the Application

**Method 1: Direct Python**
```bash
python -m src.main
```

**Method 2: Docker Compose**
```bash
docker compose run audible-sync
```

### Making Changes

**Workflow:**
1. Create feature branch from main
2. Make code changes
3. **Manual testing required** (no automated tests)
4. Commit with clear message
5. Push to feature branch
6. Create pull request

**No Pre-commit Hooks:** No automated checks before commit

### Building Docker Image

**Manual Build:**
```bash
docker build -t audible-sync:dev .
```

**Automated Build:**
- Push git tag matching `v*` (e.g., `v1.0.0`)
- GitHub Actions will build and push to ghcr.io

## Configuration Management

### config.ini Structure

**Location:** `/home/user/audible-sync/config/config.ini`

```ini
[general]
debug = true                    # Debug mode (currently unused)

[sync]
# max-download = 10             # Limit downloads per run (optional)
# audible-auth-file = /path     # Custom auth file location (optional)

[folders]
downloads = data/downloads      # Temporary download directory
audiobooks = audiobooks         # Final audiobook storage
```

### Configuration Loading

**In main.py:**
```python
import configparser

config = configparser.ConfigParser()
config.read("config/config.ini")

# Access values
max_download = config.get("sync", "max-download", fallback=None)
if max_download:
    max_download = int(max_download)
```

### Default Behaviors

**Auth File Resolution:**
1. Check config: `[sync] audible-auth-file`
2. If not set, use: `~/.audible/audible.json`

**Download Limit:**
- If `max-download` commented out: Download ALL waiting books
- If set: Download up to specified number

**Folders:**
- Created automatically if they don't exist
- Relative paths resolved from application root

## Database Schema Details

### Library Table

**Primary Key:** `asin` (Audible Standard Identification Number)

**Status Values:**
- `waiting_download` - Book synced but not yet downloaded
- `downloaded` - Download and decryption complete

**JSON Fields:**
- `authors` - Example: `["Author One", "Author Two"]`
- `narrators` - Example: `["Narrator Name"]`
- `series` - Example: `[{"title": "Series Name", "sequence": "1"}]`
- `genres` - Example: `["Fiction", "Science Fiction"]`

**Date Format:**
- `date_added` - ISO 8601 format (e.g., "2024-01-15T10:30:00")
- `release_date` - String format from Audible API

### Planned Schema Changes (Milestone 2)

**New Columns for Enhanced Metadata:**
```sql
ALTER TABLE library ADD COLUMN pdf_path TEXT;           -- Path to companion PDF
ALTER TABLE library ADD COLUMN cover_path TEXT;         -- Path to high-res cover image
ALTER TABLE library ADD COLUMN annotations_path TEXT;   -- Path to annotations JSON
ALTER TABLE library ADD COLUMN metadata_embedded BOOLEAN DEFAULT 0;  -- Has metadata been embedded
ALTER TABLE library ADD COLUMN encoding_format TEXT DEFAULT 'm4b';   -- 'm4b' or 'oga'
ALTER TABLE library ADD COLUMN downloaded_at TEXT;      -- Timestamp of download completion
```

**Future Schema (Milestone 3):**
```sql
-- Settings table (move from config.ini)
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);

-- Sync history tracking
CREATE TABLE sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,     -- 'running', 'success', 'failed'
    books_added INTEGER DEFAULT 0,
    books_downloaded INTEGER DEFAULT 0,
    error_message TEXT
);
```

### Common Database Operations

**Get Latest Book Date (for incremental sync):**
```python
books = get_books(limit=1)  # Sorted by date_added DESC
if books:
    latest_date = books[0][10]  # Index 10 is date_added
```

**Mark Multiple Books as Downloaded:**
```python
for book in books_to_download:
    asin = book[0]
    # ... download process ...
    mark_book_downloaded(asin)
```

## Common Development Tasks

### Adding a New Book Attribute

1. **Update model.py:** Add field to Book class
2. **Update database.py:**
   - Add column to CREATE TABLE statement
   - Update INSERT statement in `update_books()`
   - Update SELECT statement in `get_books()`
3. **Update audible.py:** Add field mapping in `_prepare_book()`
4. **Migration:** Manual ALTER TABLE required for existing databases

### Adding a New Configuration Option

1. **Update config/config.ini:** Add setting to appropriate section
2. **Update main.py:** Read config value with appropriate fallback
3. **Pass to module:** Add parameter to relevant function calls
4. **Update README.md:** Document new configuration option

### Adding a New Endpoint (api.py)

**Wait for Milestone 3** - Current API structure is incomplete

### Improving Error Handling

**Current State:** Minimal try/except blocks

**Best Practices:**
- Wrap network calls (Audible API, downloads) in try/except
- Handle file I/O errors (disk full, permissions)
- Log errors (once logging is implemented)
- Provide user-friendly error messages

### Adding Tests (Future)

**Recommended Framework:** pytest

**Test Structure:**
```
tests/
├── test_database.py
├── test_audible.py
├── test_sync.py
├── test_downloader.py
└── fixtures/
    └── sample_responses.json
```

**Mocking:** Use unittest.mock for Audible API calls

## Code Conventions

### Naming
- **Functions:** `snake_case` (e.g., `get_library`, `download_book`)
- **Classes:** `PascalCase` (e.g., `Book`, `Audible`, `Downloader`)
- **Constants:** `SCREAMING_SNAKE_CASE` (e.g., `DB_FILE`)
- **Private:** `_leading_underscore` (e.g., `_prepare_book`, `_get_connection`)

### Imports
```python
# Standard library
import os
from datetime import datetime

# Third-party
import audible
from tqdm import tqdm

# Local
from src.model import Book
from src.database import get_books
```

**Order:** Standard library → Third-party → Local

### Type Hints
**Current:** Partial usage (some functions have type hints, others don't)

**Preferred:**
```python
def get_books(limit: int = None) -> List[Tuple]:
    pass
```

### Documentation
**Current:** Minimal docstrings

**Preferred:** Add docstrings to public functions
```python
def sync_library(audible: Audible) -> int:
    """
    Sync library from Audible API to local database.

    Performs incremental sync - only fetches books purchased
    after the latest book in the database.

    Args:
        audible: Authenticated Audible API client

    Returns:
        Number of new books synced
    """
```

### Error Handling
**Current:** Minimal

**Pattern to Follow:**
```python
try:
    response = audible.get_library()
except Exception as e:
    print(f"Error syncing library: {e}")
    return 0
```

## Docker Deployment

### Dockerfile Explanation

**Base Image:** `python:3.11-slim` (Debian-based, minimal size)

**Build Steps:**
1. Install FFmpeg system package
2. Set working directory to `/app`
3. Copy and install Python dependencies
4. Copy application code
5. Set entrypoint: `python -m src.main`

**No Multi-stage Build:** Simple single-stage build (435 lines of code doesn't warrant complexity)

### Docker Compose

**Volume Mounts:**
```yaml
volumes:
  - ~/.audible:/root/.audible:ro    # Auth file (read-only)
  - ./data:/app/data                # Database persistence
  - ./audiobooks:/app/audiobooks    # Output files
```

**Important:** Ensure host directories exist and have correct permissions

**Running:**
```bash
docker compose run audible-sync
```

**Not a Service:** Single execution per run (not a long-running daemon)

### GitHub Container Registry

**Image:** `ghcr.io/alexstansfield/audible-sync:latest`

**Tags:**
- `latest` - Most recent release
- `v1.0.0` - Version-specific tags

**Pull:**
```bash
docker pull ghcr.io/alexstansfield/audible-sync:latest
```

## Important Warnings & Gotchas

### 1. Database Returns Tuples, Not Objects

**CRITICAL:** `get_books()` and `get_books_to_download()` return tuples, not Book objects.

**Wrong:**
```python
books = get_books()
print(books[0].title)  # AttributeError!
```

**Correct:**
```python
books = get_books()
print(books[0][1])  # Index 1 is title
```

**Index Reference:**
```python
0:  asin
1:  title
2:  subtitle
3:  authors (JSON string)
4:  narrators (JSON string)
5:  series (JSON string)
6:  genres (JSON string)
7:  length
8:  is_finished
9:  percent_complete
10: date_added
11: release_date
12: cover_url
13: status
```

**Recommendation:** Consider refactoring to return Book objects or use namedtuples

### 2. No Automated Testing

**Impact:** All changes must be manually tested

**Testing Checklist:**
- [ ] Run full sync (empty database)
- [ ] Run incremental sync (existing database)
- [ ] Download books (with and without max-download limit)
- [ ] Verify file organization (series vs non-series books)
- [ ] Check database updates (status changes)
- [ ] Test Docker container
- [ ] Verify error handling (network errors, missing files)

### 3. FFmpeg Dependency

**Required:** FFmpeg must be installed on system or in container

**macOS:** `brew install ffmpeg`
**Ubuntu:** `sudo apt install ffmpeg`
**Docker:** Already included in Dockerfile

**Failure Symptom:** Decryption fails silently or with FFmpeg errors

### 4. Audible Auth File Required

**User Must Create:** Cannot be generated by this application (yet - Milestone 3 feature)

**Tool:** audible-cli (`pip install audible-cli`, then `audible quickstart`)

**Location:** Default `~/.audible/audible.json`, or custom via config

**Format:** JSON with credentials, tokens, and device info

**Security:** Keep this file private (gitignored automatically)

### 5. API Module is Broken

**File:** `src/api.py`

**Issue:** `/sync` endpoint missing required `audible` parameter

**Status:** Incomplete - Milestone 3 feature

**Do Not Use:** Not production ready

### 6. Download Progress Feature

**Recent Addition:** Download progress bars added in PR #1

**Implementation:** Uses tqdm library in `downloader.py`

**Display:** Shows progress for each book download

### 7. JSON Serialization in Database

**Fields:** authors, narrators, series, genres stored as JSON strings in SQLite

**Reading:**
```python
import json

books = get_books()
authors = json.loads(books[0][3])  # Parse JSON string to list
```

**Writing:** Handled automatically in `update_books()`

### 8. File Cleanup

**Important:** `downloader.py` cleans up temporary files after decryption

**Location:** `data/downloads/{asin}_{title}/` folders

**Cleanup:** Uses `shutil.rmtree()` after moving M4B to final location

**Failure Case:** If decryption fails, temporary files may remain

## Development Roadmap

### Milestone 1: COMPLETE ✅
- [x] Library sync and incremental updates
- [x] Download and decrypt to M4B
- [x] SQLite database
- [x] Configuration file
- [x] Docker support
- [x] GitHub Actions CI/CD
- [x] Download progress tracking (PR #1)

### Milestone 2: IN PROGRESS 🚧 - Enhanced Download Processing

**Goal:** Complete the book download pipeline with all metadata and encoding options

**Strategy:** Enhance the existing download process in CLI before converting to service architecture

**Tasks:**
- [ ] Add logging framework (foundation for all features)
- [ ] Download PDFs during book processing (companion materials)
- [ ] Download high-res cover images during processing
- [ ] Download user annotations during processing
- [ ] Generate comprehensive metadata from book data
- [ ] Embed metadata into M4B files using FFmpeg
- [ ] Embed cover art into M4B files
- [ ] Add OGA encoding option (Opus codec for smaller files)
- [ ] Configurable encoding bitrate
- [ ] Enhanced file organization (store PDFs, covers, annotations with audiobook)

**Why This Order:** Perfect the core "download a book" logic in the simple CLI context before adding service infrastructure complexity. All features here enhance the `downloader.py` module.

**Reference Implementation:** See [BALD](https://github.com/damajor/BALD/blob/master/BALD.sh) for how these features work

### Milestone 3: PLANNED 📋 - Service Architecture & Web UI

**Goal:** Transform from CLI to always-on service with web interface

**Vision:**
- Background service with scheduled automatic syncs (e.g., every 6 hours)
- Web UI for library viewing, settings management, manual sync triggers
- Concurrent downloads for faster processing
- Real-time progress monitoring via WebSocket
- User-friendly login flow (no manual audible.json setup)

**Infrastructure Tasks:**
- [ ] Refactor to async/await for concurrency
- [ ] Add background scheduler (APScheduler)
- [ ] Add sync run tracking (database table for history)
- [ ] Add progress tracking hooks for real-time updates
- [ ] Move settings from config.ini to database
- [ ] Settings migration script

**API Tasks:**
- [ ] Build FastAPI service layer
- [ ] Audible login flow via API (generate audible.json)
- [ ] RESTful endpoints (GET /library, POST /sync, GET/PUT /settings)
- [ ] WebSocket endpoint for live progress updates
- [ ] Sync status endpoint (is sync running, progress, history)

**Web UI Tasks:**
- [ ] Login page (authenticate with Audible)
- [ ] Library view (filterable, searchable, sortable)
- [ ] Settings management page
- [ ] Sync history timeline
- [ ] Manual sync trigger with real-time progress
- [ ] Book detail modal (show PDFs, annotations, metadata)

**Why After Milestone 2:** All the "what to do with a book" logic will be complete and tested. Service layer just wraps it in scheduled execution and HTTP endpoints.

**Current Focus:** Milestone 2 features (logging, metadata, PDFs, covers, annotations, OGA encoding)

## Understanding "Sync" in This Project

**Important Concept Clarification:**

"Sync" in this project means **keeping your local audiobook collection synchronized with your Audible library**, not just metadata updates.

### Current Behavior (Milestone 1):
```bash
python -m src.main
  1. Sync metadata: Update database with new books from Audible
  2. Download books: Get any books with status='waiting_download'
  3. Exit
```

This is a **one-shot operation** - manual execution required.

### Future Behavior (Milestone 3):
```bash
docker compose up  # Starts service
  → Background scheduler runs every N hours (configurable)
  → Each scheduled run:
      1. Fetch library updates from Audible
      2. Download PDFs, covers, annotations for new books
      3. Download and convert any books not yet downloaded
      4. Update database status
  → Manual trigger available via web UI: POST /sync
```

This is a **continuous service** - automatic, scheduled execution.

### Key Insight:
"Sync" is **both** operations:
1. **Metadata sync:** Audible library → local database
2. **File sync:** Audible audiobooks → local filesystem (as M4B/OGA)

They happen together in one unified operation, not as separate commands.

## Working with This Codebase as an AI Assistant

### Initial Analysis Checklist
When starting work on this codebase:

1. [ ] Check current git branch
2. [ ] Review recent commits for context
3. [ ] Read `todo.md` to understand current milestone
4. [ ] Check `config/config.ini` for current settings
5. [ ] Verify Python environment (3.11+)
6. [ ] Confirm FFmpeg availability
7. [ ] Review relevant module based on task

### Before Making Changes

1. **Understand the module:** Read the entire file (all modules are small)
2. **Check dependencies:** What other modules does it interact with?
3. **Consider database impact:** Will this change the schema or queries?
4. **Plan testing:** How will you verify the change works?
5. **Review conventions:** Follow existing naming and structure patterns

### Code Review Focus Areas

1. **Error handling:** Are network errors handled gracefully?
2. **File paths:** Are paths properly resolved (absolute vs relative)?
3. **Database operations:** Are connections closed properly?
4. **Cleanup:** Are temporary files removed?
5. **Configuration:** Are config values properly validated?
6. **Type safety:** Add type hints where missing
7. **Documentation:** Add docstrings for new functions

### Common Patterns to Follow

**Database Operations:**
```python
from src.database import get_books, mark_book_downloaded

books = get_books_to_download()
for book in books:
    asin = book[0]
    # ... do work ...
    mark_book_downloaded(asin)
```

**Audible API Calls:**
```python
from src.audible import Audible

auth_file = config.get("sync", "audible-auth-file",
                       fallback=os.path.expanduser("~/.audible/audible.json"))
audible = Audible(auth_file)
library = audible.get_library()
```

**File Organization:**
```python
import os

# Create directories if needed
os.makedirs(folder, exist_ok=True)

# Clean paths
safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_'))
```

**Progress Tracking:**
```python
from tqdm import tqdm

with tqdm(total=total_size, unit='B', unit_scale=True) as pbar:
    # ... download chunks ...
    pbar.update(len(chunk))
```

### Debugging Tips

**Database Inspection:**
```bash
sqlite3 data/audible_sync.db
.tables
.schema library
SELECT * FROM library WHERE status='waiting_download';
.quit
```

**Docker Debugging:**
```bash
docker compose run --rm -it audible-sync bash
# Then inspect container filesystem
```

**Audible API Testing:**
```python
from src.audible import Audible

audible = Audible("~/.audible/audible.json")
library = audible.get_library()
print(f"Found {len(library)} books")
```

### Security Considerations

**Do Not Commit:**
- `audible.json` auth file
- `data/audible_sync.db` database
- Downloaded audiobooks
- Any files in `data/downloads/`

**Already Gitignored:**
- `.env` files
- `venv/` directory
- `*.db` files
- `audiobooks/` directory
- `data/` directory

**User Privacy:**
- Library contents are private
- Download history is private
- No telemetry or external reporting

## Useful File Paths Reference

### Application Files
- Entry point: `/home/user/audible-sync/src/main.py`
- Config: `/home/user/audible-sync/config/config.ini`
- Database: `/home/user/audible-sync/data/audible_sync.db`
- Dependencies: `/home/user/audible-sync/requirements.txt`
- Roadmap: `/home/user/audible-sync/todo.md`

### Docker Files
- Build: `/home/user/audible-sync/Dockerfile`
- Compose: `/home/user/audible-sync/compose.yml`
- Ignore: `/home/user/audible-sync/.dockerignore`

### Source Modules
- `/home/user/audible-sync/src/main.py` (35 lines)
- `/home/user/audible-sync/src/model.py` (24 lines)
- `/home/user/audible-sync/src/database.py` (87 lines)
- `/home/user/audible-sync/src/audible.py` (74 lines)
- `/home/user/audible-sync/src/sync.py` (16 lines)
- `/home/user/audible-sync/src/downloader.py` (187 lines)
- `/home/user/audible-sync/src/api.py` (12 lines, incomplete)

### External Dependencies
- Auth file: `~/.audible/audible.json` (user must create)
- FFmpeg: System package (required for decryption)

## Conclusion

This is a **well-structured, focused Python CLI application** with clear separation of concerns and straightforward execution flow. The codebase is small (435 lines), maintainable, and actively developed with a clear vision for evolution into a background service with web UI.

**Key Strengths:**
- Simple, understandable architecture
- Clear module responsibilities
- Good documentation (README, todo.md, CLAUDE.md)
- Docker support for easy deployment
- Incremental sync optimization
- Unified sync+download workflow (one operation)
- Clear roadmap with phased milestones

**Current Development Strategy:**
1. **Milestone 2:** Enhance download pipeline with metadata/PDFs/covers/annotations/OGA in simple CLI context
2. **Milestone 3:** Convert to background service with scheduler, API, and web UI
3. **Philosophy:** Perfect features in simple context before adding architectural complexity

**Areas for Future Enhancement:**
- Add automated testing (pytest) - planned after core features complete
- Implement logging framework (first Milestone 2 task)
- Refactor database to return objects instead of tuples (nice-to-have)
- Add comprehensive error handling
- Add type hints consistently

**For AI Assistants:**
When working with this codebase:
- Understand that "sync" means both metadata + file synchronization
- Milestone 2 features enhance the download process, not add new operations
- Service architecture (Milestone 3) comes after download pipeline is complete
- Prioritize **simplicity and clarity** over complex abstractions
- Reference BALD project for implementation patterns of metadata/PDF/cover features
- Test manually and thoroughly (no automated test suite yet)

**Inspiration & Context:**
This project reimagines [BALD](https://github.com/damajor/BALD) (Bash Audible Library Downloader) as a Python service with better UX, web UI, and automated scheduling while maintaining the same core functionality.

---

**Document Version:** 2.0
**Last Updated:** 2025-11-17
**Codebase Version:** Post-Milestone 1, beginning Milestone 2
**Primary Branch:** `main`
**Key Changes:** Clarified sync concept, updated roadmap strategy, added BALD reference
