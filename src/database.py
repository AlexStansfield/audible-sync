import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from src.model import Account, Book, BookStatus, SyncOutcome, SyncRun
from src.paths import REPO_ROOT

DB_FILE = str(REPO_ROOT / "data" / "audible_sync.db")

# A download can outlive its process: a container restart or a kill -9 leaves the row in
# `downloading` with nothing working on it. Six hours is comfortably longer than the
# slowest real book (a licence, several gigabytes over a CDN, then an Opus re-encode) and
# short enough that a crashed run recovers on the next scheduler tick rather than by hand.
STALE_CLAIM_SECONDS = 6 * 60 * 60


def _get_connection() -> sqlite3.Connection:
    """
    Connection whose rows come back as `sqlite3.Row`, so reads can be mapped by
    column name.

    `_migrate_schema` appends columns to an older database in whatever order it
    finds them missing, so the position of a column in `SELECT *` was never a
    reliable thing to index.
    """
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _get_connection()
    cursor = conn.cursor()

    # A reader no longer blocks the writer, which matters now a scheduler tick can run
    # while another process is downloading. The mode is a property of the database file
    # rather than of the connection, so setting it once here holds for every later
    # connection - including ones opened by a process that never calls init_db.
    cursor.execute("PRAGMA journal_mode=WAL")

    # One row per marketplace login. `auth` is the Authenticator blob as JSON, NULL for
    # an account that has no working credentials yet (see `Account`).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            country_code TEXT NOT NULL DEFAULT '',
            customer_name TEXT,
            auth JSON,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            monitor_existing BOOLEAN NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_synced_at TEXT
        )
    """)

    # A book is one ASIN in one account's library. The same ASIN can be owned in two
    # marketplaces, so the identity is the pair and `id` is what the API addresses.
    cursor.execute(_library_ddl())

    # One row per pipeline run of one account, covering both halves of it: the library
    # sync and the downloads that followed. The newest start time of a run whose sync
    # completed is that account's next incremental cursor, which the library table
    # could never provide - `MAX(date_added)` says what was purchased, not when we
    # last looked. `account_id` is NULL on rows from before there were accounts.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER REFERENCES accounts(id),
            started_at TEXT NOT NULL,
            -- NULL while the run is in flight. A row still `running` long after its
            -- started_at is a run that was killed, which is the state the todo item
            -- existed to make visible.
            finished_at TEXT,
            outcome TEXT NOT NULL,
            books_seen INTEGER NOT NULL DEFAULT 0,
            books_added INTEGER NOT NULL DEFAULT 0,
            books_downloaded INTEGER NOT NULL DEFAULT 0,
            books_failed INTEGER NOT NULL DEFAULT 0,
            error TEXT
        )
    """)
    # Runtime settings as text, one row per key. Seeded from config.ini on the first run
    # and changed through the API after that; `src.settings` owns the parsing.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()

    # Migrate existing databases to add new columns
    _migrate_schema(conn)
    # ... and to the per-account shape, which needs the table rebuilt
    _migrate_library_to_accounts(conn)

    _create_indexes(conn)

    conn.close()


def _create_indexes(conn: sqlite3.Connection) -> None:
    """
    Index the columns every run filters or sorts on.

    Sync reads the newest `date_added`; the download queue selects on `status` and
    orders by `date_added`, so that one is composite and the ordering comes from the
    index rather than from sorting every waiting row. SQLite can serve a plain `status`
    lookup from the composite's leading column too, which makes the old single-column
    `idx_library_status` redundant - it is dropped rather than left alongside.

    `sync_runs` gets the shape of the cursor query: filter on `outcome`, take the
    newest `started_at`.

    Guarded by the columns actually present so an older database that predates a column
    is still upgradable: the composite raises on a schema with no `date_added`.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(library)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    cursor.execute("PRAGMA table_info(sync_runs)")
    run_columns = {row[1] for row in cursor.fetchall()}
    if run_columns:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_runs_outcome_started_at ON sync_runs(outcome, started_at)")
    if "account_id" in run_columns:
        # The cursor is per account now: filter on account and outcome, take the newest start
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_runs_account_outcome_started_at "
            "ON sync_runs(account_id, outcome, started_at)"
        )

    if "date_added" in existing_columns:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_date_added ON library(date_added)")

    if {"status", "date_added"} <= existing_columns:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_status_date_added ON library(status, date_added)")
        cursor.execute("DROP INDEX IF EXISTS idx_library_status")
    elif "status" in existing_columns:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_status ON library(status)")

    conn.commit()


def _migrate_schema(conn):
    """Add new columns to existing databases if they don't exist"""
    cursor = conn.cursor()

    # Get existing columns
    cursor.execute("PRAGMA table_info(library)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    # Add missing columns
    new_columns = {
        "pdf_path": "TEXT",
        "cover_path": "TEXT",
        "annotations_path": "TEXT",
        "has_pdf": "BOOLEAN DEFAULT 0",
        "encoding_format": "TEXT",
        "downloaded_at": "TEXT",
        "is_consumable": "BOOLEAN",
        "attempts": "INTEGER NOT NULL DEFAULT 0",
        "last_error": "TEXT",
        "last_attempt_at": "TEXT",
        "monitored": "BOOLEAN NOT NULL DEFAULT 1",
        "file_path": "TEXT",
    }

    for column, column_type in new_columns.items():
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE library ADD COLUMN {column} {column_type}")

    cursor.execute("PRAGMA table_info(sync_runs)")
    if "account_id" not in {row[1] for row in cursor.fetchall()}:
        cursor.execute("ALTER TABLE sync_runs ADD COLUMN account_id INTEGER REFERENCES accounts(id)")

    conn.commit()


# The columns a rebuilt `library` carries, in DDL order; `_migrate_library_to_accounts`
# copies whichever of them the old table has.
_LIBRARY_COLUMNS = (
    "asin", "title", "subtitle", "authors", "narrators", "series", "genres", "length",
    "is_finished", "percent_complete", "date_added", "release_date", "cover_url", "status",
    "monitored", "file_path", "pdf_path", "cover_path", "annotations_path", "has_pdf", "encoding_format",
    "downloaded_at", "is_consumable", "attempts", "last_error", "last_attempt_at",
)  # fmt: skip


def _migrate_library_to_accounts(conn: sqlite3.Connection) -> None:
    """
    Rebuild a `library` written before there were accounts.

    The old table was keyed by `asin` alone; the new one has a surrogate `id` and is
    unique on `(account_id, asin)`. SQLite cannot change a primary key in place, so the
    rows are copied into a fresh table under a single account. That account is created
    here with no credentials if none exists - this module cannot read the auth file
    (that is settings' business, and settings imports this module), so
    `accounts.ensure_account_from_auth_file` fills it in from the configured file on the
    next start. Until then it reads as needing a login rather than blocking the start.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(library)")
    old_columns = [row[1] for row in cursor.fetchall()]
    if not old_columns or "account_id" in old_columns:
        return

    cursor.execute("SELECT id FROM accounts ORDER BY id LIMIT 1")
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "INSERT INTO accounts (name, country_code, auth, created_at) VALUES (?, '', NULL, ?)",
            (LEGACY_ACCOUNT_NAME, _utcnow()),
        )
        account_id = cursor.lastrowid
    else:
        account_id = row[0]

    copied = [column for column in _LIBRARY_COLUMNS if column in old_columns]
    columns = ", ".join(copied)
    cursor.execute("ALTER TABLE library RENAME TO library_legacy")
    # The CREATE TABLE in init_db is IF NOT EXISTS, and the renamed table no longer
    # answers to `library`, so running init_db's DDL again creates the new shape
    cursor.execute(_library_ddl())
    cursor.execute(
        f"INSERT INTO library (account_id, {columns}) SELECT ?, {columns} FROM library_legacy", (account_id,)
    )
    cursor.execute("DROP TABLE library_legacy")
    # Indexes went with the old table; `_create_indexes` recreates them
    conn.commit()


# What the migration names the account it has to invent for a pre-accounts library
LEGACY_ACCOUNT_NAME = "Audible"


def _library_ddl() -> str:
    """The `library` DDL, shared by `init_db` and the rebuild so the two cannot drift."""
    return """
        CREATE TABLE IF NOT EXISTS library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            asin TEXT NOT NULL,
            title TEXT,
            subtitle TEXT,
            authors JSON,
            narrators JSON,
            series JSON,
            genres JSON,
            length INTEGER,
            is_finished BOOLEAN,
            percent_complete REAL,
            date_added TEXT,
            release_date TEXT,
            cover_url TEXT,
            status TEXT,
            -- Whether the book is wanted at all; an unmonitored book is never queued
            monitored BOOLEAN NOT NULL DEFAULT 1,
            -- Where the finished audio was filed; the accessories follow
            file_path TEXT,
            pdf_path TEXT,
            cover_path TEXT,
            annotations_path TEXT,
            has_pdf BOOLEAN DEFAULT 0,
            encoding_format TEXT,
            downloaded_at TEXT,
            -- Nullable on purpose: NULL means a row written before this column
            -- existed, i.e. consumability has never been read from the API for it.
            is_consumable BOOLEAN,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            last_attempt_at TEXT,
            UNIQUE (account_id, asin)
        )
    """


def update_books(account_id: int, books: list[Book], *, monitor_new: bool = True) -> int:
    """
    Insert new books into one account's library, refresh the mutable API fields on the
    ones already stored, and return how many rows were genuinely added.

    `(account_id, asin)` is unique, so one upsert does the de-duplication. Checking first
    on a second connection could not see the rows this transaction had already inserted,
    so a repeated ASIN inside a single API response raised IntegrityError and rolled the
    whole sync back.

    `monitor_new` is what a book inserted by this call gets for `monitored`; it is never
    written on conflict, because whether a book already in the library is wanted is the
    user's decision, not the sync's.

    The conflict clause deliberately refreshes only what Audible owns. It never writes
    `date_added` (the incremental sync cursor - moving it would skip or re-fetch
    purchases), and never `status`, `attempts`, `last_error`, `last_attempt_at`, the
    three accessory paths, `encoding_format` or `downloaded_at`, all of which belong to
    the downloader. Before this it was an INSERT OR IGNORE, so `is_finished` and
    `percent_complete` stayed frozen at whatever they were the day a book was first seen.

    The one exception is `status`, and only between `waiting_download` and `unavailable`.
    Audible withdraws Plus titles the customer still holds and later offers them again,
    so consumability is the sync's business: a withdrawn book leaves the queue, and a
    restored one rejoins it without anybody having to do anything.
    """
    # Decoded again by `Book.from_row`; keep the two sides in step.
    rows = [
        (
            account_id,
            book.asin,
            book.title,
            book.subtitle,
            json.dumps(book.authors),
            json.dumps(book.narrators),
            json.dumps(book.series),
            json.dumps(book.genres),
            book.length,
            book.is_finished,
            book.percent_complete,
            book.date_added,
            book.release_date,
            book.cover_url,
            # Insert only; the conflict clause below decides an existing book's status.
            # A title Audible has withdrawn goes straight to `unavailable` so it never
            # enters the queue and never costs a licence request.
            BookStatus.WAITING_DOWNLOAD if book.is_consumable else BookStatus.UNAVAILABLE,
            monitor_new,
            book.has_pdf,
            book.is_consumable,
            # Bound for the CASE in the conflict clause below
            BookStatus.WAITING_DOWNLOAD,
            BookStatus.UNAVAILABLE,
            BookStatus.UNAVAILABLE,
            BookStatus.WAITING_DOWNLOAD,
        )
        for book in books
    ]

    with closing(_get_connection()) as conn:
        cursor = conn.cursor()
        # `cursor.rowcount` after an `executemany` of an upsert is -1, not a count, and
        # `RETURNING` cannot be used with `executemany` at all. Counting either side of
        # the write on the same cursor is the one thing that stays honest, and both reads
        # sit inside the same transaction so no other writer can slip between them.
        books_before = cursor.execute("SELECT COUNT(*) FROM library").fetchone()[0]
        cursor.executemany(
            """
            INSERT INTO library (account_id, asin, title, subtitle, authors, narrators, series, genres,
                                 length, is_finished, percent_complete, date_added, release_date,
                                 cover_url, status, monitored, has_pdf, is_consumable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, asin) DO UPDATE SET
                title = excluded.title,
                subtitle = excluded.subtitle,
                authors = excluded.authors,
                narrators = excluded.narrators,
                series = excluded.series,
                genres = excluded.genres,
                length = excluded.length,
                is_finished = excluded.is_finished,
                percent_complete = excluded.percent_complete,
                release_date = excluded.release_date,
                cover_url = excluded.cover_url,
                has_pdf = excluded.has_pdf,
                is_consumable = excluded.is_consumable,
                -- The only status the sync may change, in either direction. A book
                -- Audible has withdrawn leaves the queue; one it has offered again
                -- rejoins it, which is how a restored Plus title comes back on its own.
                -- Every other state - downloading, downloaded, failed - is left alone.
                status = CASE
                    WHEN excluded.is_consumable = 0 AND library.status = ? THEN ?
                    WHEN excluded.is_consumable = 1 AND library.status = ? THEN ?
                    ELSE library.status
                END
            """,
            rows,
        )
        books_added = cursor.execute("SELECT COUNT(*) FROM library").fetchone()[0] - books_before
        conn.commit()

    return books_added


def get_books(limit: int | None = None, *, account_id: int | None = None) -> list[Book]:
    """All books, newest `date_added` first; one account's or everyone's."""
    sql = "SELECT * FROM library"
    params: tuple = ()
    if account_id is not None:
        sql = f"{sql} WHERE account_id = ?"
        params = (account_id,)
    sql = f"{sql} ORDER BY date_added DESC"
    if limit is not None:
        sql = f"{sql} LIMIT ?"
        params = (*params, limit)

    with closing(_get_connection()) as conn:
        return [Book.from_row(row) for row in conn.execute(sql, params)]


def get_books_to_download(*, account_id: int | None = None, stale_after: int = STALE_CLAIM_SECONDS) -> list[Book]:
    """
    Books the downloader should try, oldest first: one account's, or everyone's.

    Only monitored books: an unmonitored one is not wanted, whatever its status says.

    Both the rows still `waiting_download` and any left in `downloading` by a process
    that died. Selecting only `waiting_download` made `STALE_CLAIM_SECONDS` unreachable:
    `claim_book_for_download` has always been able to reclaim an abandoned row, but
    nothing ever offered it one, so a book a killed run had claimed stayed `downloading`
    forever, its part-file with it. It uses the same `stale_after` and the same
    NULL-counts-as-stale rule as the claim, so the query and that UPDATE cannot disagree
    about what "abandoned" means.

    This still does **not** claim anything - the caller claims each book individually, so
    a row a live run holds is offered here and then simply fails to claim. That is the
    same race the claim already settles, and the reason this can afford to be generous.

    `last_attempt_at` is not in the `(status, date_added)` index, but the `downloading`
    rows are a handful at most, so the extra test costs nothing.

    Args:
        stale_after: Seconds after which a `downloading` row is treated as abandoned

    Returns:
        Books to attempt, oldest `date_added` first
    """
    sql = """
        SELECT * FROM library
         WHERE monitored = 1
           AND (status = ?
                OR (status = ? AND (last_attempt_at IS NULL OR last_attempt_at < ?)))
    """
    params: tuple = (BookStatus.WAITING_DOWNLOAD, BookStatus.DOWNLOADING, _stale_cutoff(stale_after))
    if account_id is not None:
        sql = f"{sql} AND account_id = ?"
        params = (*params, account_id)
    with closing(_get_connection()) as conn:
        rows = conn.execute(f"{sql} ORDER BY date_added ASC", params)
        return [Book.from_row(row) for row in rows]


def get_book(book_id: int) -> Book | None:
    """One book by its row id, which is how the API addresses one."""
    with closing(_get_connection()) as conn:
        row = conn.execute("SELECT * FROM library WHERE id = ?", (book_id,)).fetchone()
    return Book.from_row(row) if row is not None else None


def get_book_by_asin(asin: str, *, account_id: int | None = None) -> Book | None:
    """
    One book by ASIN: in one account's library, or the first found in any.

    Without an account this is only unambiguous while one account holds the ASIN, so
    anything addressing a specific book should use `get_book`.
    """
    sql = "SELECT * FROM library WHERE asin = ?"
    params: tuple = (asin,)
    if account_id is not None:
        sql = f"{sql} AND account_id = ?"
        params = (asin, account_id)
    with closing(_get_connection()) as conn:
        row = conn.execute(f"{sql} ORDER BY id LIMIT 1", params).fetchone()
    return Book.from_row(row) if row is not None else None


def latest_date_added(account_id: int) -> str | None:
    """
    The newest `date_added` in one account's library, or None when it is empty.

    Sync uses this as its incremental cursor. Reading it directly keeps that
    cursor independent of how `get_books` happens to sort or paginate.
    """
    with closing(_get_connection()) as conn:
        return conn.execute("SELECT MAX(date_added) FROM library WHERE account_id = ?", (account_id,)).fetchone()[0]


def needs_consumability_refresh(account_id: int) -> bool:
    """
    Whether one account's library holds books whose availability the incremental sync
    cannot see.

    The incremental sync only fetches what was purchased after the newest `date_added`,
    so a book already in the library is never re-read and its `is_consumable` never
    changes. That matters in both directions: a parked title Audible has offered again
    would stay parked forever, and a row written before the column existed has never had
    its availability read at all.

    True when either is present, which is the signal for `sync_library` to re-read the
    whole library once. It goes back to False as soon as nothing is parked.
    """
    with closing(_get_connection()) as conn:
        row = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM library WHERE account_id = ? AND (is_consumable IS NULL OR status = ?))",
            (account_id, BookStatus.UNAVAILABLE),
        ).fetchone()
    return bool(row[0])


def _utcnow() -> str:
    """Current UTC time as an ISO 8601 string with second precision, e.g. 2026-09-08T05:16:15+00:00."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _stale_cutoff(seconds: int) -> str:
    """
    The timestamp `seconds` in the past, formatted exactly as `_utcnow` writes them.

    Every stored timestamp carries the same `+00:00` offset and second precision, so
    "older than" is a plain string comparison in SQL and no column has to be parsed.

    Deliberately its own function rather than an argument on `_utcnow`, whose
    zero-argument signature the tests monkeypatch.
    """
    return (datetime.now(UTC) - timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def claim_book_for_download(book_id: int, *, stale_after: int = STALE_CLAIM_SECONDS) -> bool:
    """
    Take ownership of one book, moving it to `downloading` and counting the attempt.

    The whole decision is a single UPDATE, so two processes - a scheduler tick starting
    while a manual run is still going - cannot both take the same book: the second
    matches no rows, because the first has already moved it out of `waiting_download`.

    A row abandoned in `downloading` by a process that died is claimable again once its
    `last_attempt_at` is older than `stale_after`. A NULL timestamp counts as stale too,
    since such a row would otherwise never be picked up by anything again.

    Args:
        book_id: The book to claim
        stale_after: Seconds after which a `downloading` row is treated as abandoned

    Returns:
        True if this caller now owns the book, False if somebody else does
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            """
            UPDATE library
               SET status = ?,
                   attempts = attempts + 1,
                   last_attempt_at = ?
             WHERE id = ?
               AND (status = ?
                    OR (status = ? AND (last_attempt_at IS NULL OR last_attempt_at < ?)))
            """,
            (
                BookStatus.DOWNLOADING,
                _utcnow(),
                book_id,
                BookStatus.WAITING_DOWNLOAD,
                BookStatus.DOWNLOADING,
                _stale_cutoff(stale_after),
            ),
        )
        conn.commit()

    return cursor.rowcount == 1


def mark_book_failed(book_id: int, error: str, *, max_attempts: int, terminal: bool = False) -> BookStatus | None:
    """
    Record why a download failed and decide whether the book gets another go.

    The retry decision is made inside the UPDATE, from the `attempts` the claim has
    already incremented, so it cannot race another process reading the row between a
    SELECT and an UPDATE. A book that has used up `max_attempts` becomes `failed` and is
    never selected again; before this, a book that could not possibly succeed was
    re-licensed, re-downloaded and re-failed on every run forever.

    `terminal` short-circuits that count for a failure already known to be permanent: a
    licence Audible refuses is not going to be granted on the third ask.

    Args:
        book_id: The book that failed
        error: Message stored in `last_error` for a later run, or a UI, to show
        max_attempts: Attempts allowed before the book is given up on
        terminal: Fail the book now, whatever `attempts` says

    Returns:
        The status the book ended up in, or None if there is no such book
    """
    with closing(_get_connection()) as conn:
        row = conn.execute(
            """
            UPDATE library
               SET status = CASE WHEN ? OR attempts >= ? THEN ? ELSE ? END,
                   last_error = ?
             WHERE id = ?
         RETURNING status
            """,
            (terminal, max_attempts, BookStatus.FAILED, BookStatus.WAITING_DOWNLOAD, error, book_id),
        ).fetchone()
        conn.commit()

    return None if row is None else BookStatus(row["status"])


def mark_book_unavailable(book_id: int, error: str) -> None:
    """
    Park a book Audible will not currently license, without failing it.

    Deliberately not terminal, and it does not count towards `max_attempts`. A Plus
    title withdrawn after the customer added it cannot be downloaded today but can be
    offered again, so the book leaves the queue - it stops costing a licence request
    every run - and the next sync that sees it consumable puts it straight back.

    This covers the race where the rights change between a sync and the download; the
    common case is caught at sync time from `customer_rights.is_consumable`.

    `attempts` is left where the claim put it: the attempt really did happen, and
    nothing terminal is decided from it while the book sits here.
    """
    with closing(_get_connection()) as conn:
        conn.execute(
            """
            UPDATE library
               SET status = ?,
                   is_consumable = 0,
                   last_error = ?
             WHERE id = ?
            """,
            (BookStatus.UNAVAILABLE, error, book_id),
        )
        conn.commit()


def release_book(book_id: int) -> None:
    """
    Put a claimed book back exactly as it was found, attempt count and all.

    Used when a run is abandoned for a reason that has nothing to do with the book:
    expired credentials fail every book equally, so charging that to whichever book
    happened to be next would eventually mark a perfectly good one `failed`.

    `last_attempt_at` is deliberately left where the claim put it - it is a record of
    when the book was last touched, and nothing decides anything from it once the status
    is back to `waiting_download`.
    """
    with closing(_get_connection()) as conn:
        conn.execute(
            """
            UPDATE library
               SET status = ?,
                   attempts = MAX(attempts - 1, 0)
             WHERE id = ? AND status = ?
            """,
            (BookStatus.WAITING_DOWNLOAD, book_id, BookStatus.DOWNLOADING),
        )
        conn.commit()


def mark_book_downloaded(
    book_id: int,
    encoding_format: str | None = None,
    *,
    file_path: str | None = None,
    pdf_path: str | None = None,
    cover_path: str | None = None,
    annotations_path: str | None = None,
) -> None:
    """
    Set the book to downloaded and record when, in which format, and where it and its
    accessories were filed.

    The accessory paths are written in the same statement as the status so a book
    cannot end up with its paths recorded but its status left behind (or the
    reverse) if the process stops between two commits.

    `last_error` is cleared here: a book that succeeded on its second attempt must not
    keep showing the first attempt's failure. `attempts` is kept on purpose - it is a
    true record of what the book cost.
    """
    with closing(_get_connection()) as conn:
        conn.execute(
            """
            UPDATE library
               SET status = ?,
                   last_error = NULL,
                   encoding_format = ?,
                   downloaded_at = ?,
                   file_path = COALESCE(?, file_path),
                   pdf_path = COALESCE(?, pdf_path),
                   cover_path = COALESCE(?, cover_path),
                   annotations_path = COALESCE(?, annotations_path)
             WHERE id = ?
            """,
            (
                BookStatus.DOWNLOADED,
                encoding_format,
                _utcnow(),
                file_path,
                pdf_path,
                cover_path,
                annotations_path,
                book_id,
            ),
        )
        conn.commit()


def update_book_accessories(
    book_id: int,
    pdf_path: str | None = None,
    cover_path: str | None = None,
    annotations_path: str | None = None,
) -> None:
    """Update the paths for downloaded accessories (PDF, cover, annotations)"""
    updates = []
    values: list[str] = []

    if pdf_path is not None:
        updates.append("pdf_path = ?")
        values.append(pdf_path)
    if cover_path is not None:
        updates.append("cover_path = ?")
        values.append(cover_path)
    if annotations_path is not None:
        updates.append("annotations_path = ?")
        values.append(annotations_path)

    if not updates:
        return

    values.append(book_id)
    with closing(_get_connection()) as conn:
        conn.execute(f"UPDATE library SET {', '.join(updates)} WHERE id = ?", values)
        conn.commit()


# --- book management ---------------------------------------------------------------
# What the API's per-book actions change. The file deletion itself is `library.py`'s;
# these record the outcome.


def clear_book_files(book_id: int) -> None:
    """Forget where a book was filed, after its files have been removed."""
    with closing(_get_connection()) as conn:
        conn.execute(
            """
            UPDATE library
               SET file_path = NULL, pdf_path = NULL, cover_path = NULL, annotations_path = NULL,
                   encoding_format = NULL, downloaded_at = NULL
             WHERE id = ?
            """,
            (book_id,),
        )
        conn.commit()


def set_monitored(book_id: int, monitored: bool) -> bool:
    """Whether the book is wanted. Returns False for an unknown book."""
    with closing(_get_connection()) as conn:
        cursor = conn.execute("UPDATE library SET monitored = ? WHERE id = ?", (monitored, book_id))
        conn.commit()
    return cursor.rowcount == 1


def reset_for_download(book_id: int, *, monitored: bool) -> bool:
    """
    Put a book back at the start of the state machine: `waiting_download`, no attempts,
    no error, and wanted or not as asked.

    Refuses a book that is `downloading` - a run holds it, and yanking the row from
    under it would have the run's own bookkeeping overwrite this. Returns whether the
    row changed, so the caller can tell "busy" from "no such book" by looking first.
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            """
            UPDATE library
               SET status = ?, attempts = 0, last_error = NULL, monitored = ?
             WHERE id = ? AND status != ?
            """,
            (BookStatus.WAITING_DOWNLOAD, monitored, book_id, BookStatus.DOWNLOADING),
        )
        conn.commit()
    return cursor.rowcount == 1


# The columns a listing may be sorted by, keyed by the name the API accepts. `author`
# sorts on the JSON text of the list, which puts the first author first - good enough
# for a shelf.
BOOK_SORT_COLUMNS = {
    "date_added": "date_added",
    "title": "title",
    "author": "authors",
    "release_date": "release_date",
    "downloaded_at": "downloaded_at",
}


def list_books(
    *,
    account_id: int | None = None,
    status: BookStatus | str | None = None,
    monitored: bool | None = None,
    q: str | None = None,
    sort: str = "date_added",
    descending: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Book], int]:
    """
    A page of the library and the total that matched, for the API.

    `q` matches the title, any author or any series, case-insensitively for ASCII (a
    `LIKE` over the JSON text is enough at the size of a personal library).

    Raises:
        ValueError: for a `sort` not in `BOOK_SORT_COLUMNS`
    """
    if sort not in BOOK_SORT_COLUMNS:
        raise ValueError(f"sort must be one of {', '.join(BOOK_SORT_COLUMNS)}, got {sort!r}")

    where = []
    params: list = []
    if account_id is not None:
        where.append("account_id = ?")
        params.append(account_id)
    if status is not None:
        where.append("status = ?")
        params.append(status)
    if monitored is not None:
        where.append("monitored = ?")
        params.append(monitored)
    if q:
        like = f"%{q}%"
        where.append("(title LIKE ? OR authors LIKE ? OR series LIKE ?)")
        params.extend([like, like, like])
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    order = f" ORDER BY {BOOK_SORT_COLUMNS[sort]} {'DESC' if descending else 'ASC'}, id"

    with closing(_get_connection()) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM library{clause}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM library{clause}{order} LIMIT ? OFFSET ?", (*params, limit, offset))
        return [Book.from_row(row) for row in rows], total


# --- sync_runs -------------------------------------------------------------------
# Grouped by table rather than split across the readers and writers above: the four
# functions below are only meaningful together, and only `latest_successful_sync_start`
# is read by the pipeline itself.

# A run whose sync finished is a valid cursor even if its downloads did not, so all three
# count: a cancel is only honoured after the sync (see `SyncOutcome`). A run still
# `running`, or one that failed before reading the library, does not.
_CURSOR_OUTCOMES = (SyncOutcome.SUCCESS, SyncOutcome.PARTIAL, SyncOutcome.CANCELLED)


def start_sync_run(account_id: int | None = None) -> int:
    """
    Open a run row for one account and return its id.

    Written before anything is fetched, so a run that is killed leaves a row that is
    still `running` with no `finished_at` - the record that tells a run which died half
    way from one that completed and found nothing.
    """
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            "INSERT INTO sync_runs (account_id, started_at, outcome) VALUES (?, ?, ?)",
            (account_id, _utcnow(), SyncOutcome.RUNNING),
        )
        conn.commit()
        return cursor.lastrowid


def finish_sync_run(
    run_id: int,
    *,
    outcome: SyncOutcome,
    books_seen: int = 0,
    books_added: int = 0,
    books_downloaded: int = 0,
    books_failed: int = 0,
    error: str | None = None,
) -> None:
    """
    Close a run row with its outcome and counters.

    Args:
        run_id: The id `start_sync_run` returned
        outcome: How the run ended. Only `success` and `partial` become a cursor for
            the next run, because only those read the library through
        books_seen: Books the incremental fetch returned
        books_added: Books that fetch inserted
        books_downloaded: Books downloaded, decrypted and filed
        books_failed: Books that were tried and did not finish
        error: The exception that stopped the run, if one did
    """
    with closing(_get_connection()) as conn:
        conn.execute(
            """
            UPDATE sync_runs
            SET finished_at = ?,
                outcome = ?,
                books_seen = ?,
                books_added = ?,
                books_downloaded = ?,
                books_failed = ?,
                error = ?
            WHERE id = ?
            """,
            (_utcnow(), outcome, books_seen, books_added, books_downloaded, books_failed, error, run_id),
        )
        conn.commit()


def latest_successful_sync_start(account_id: int | None = None) -> str | None:
    """
    When one account's last run that read the library through started, or None.

    This is the incremental sync cursor. It is the run's **start** rather than its
    finish because anything Audible added while the run was reading has to be picked up
    next time; `sync.py` widens it further to absorb clock skew.

    `partial` counts alongside `success`: its sync completed, and a book that failed to
    download is tracked by the library state machine, not by the cursor.

    Rows written before there were accounts carry no `account_id`, and they count for
    the migrated account too: they were that library's runs. So the lookup takes rows
    with a NULL account alongside the account's own.
    """
    placeholders = ", ".join("?" for _ in _CURSOR_OUTCOMES)
    with closing(_get_connection()) as conn:
        return conn.execute(
            f"""
            SELECT MAX(started_at) FROM sync_runs
             WHERE outcome IN ({placeholders})
               AND (account_id = ? OR account_id IS NULL)
            """,
            (*_CURSOR_OUTCOMES, account_id),
        ).fetchone()[0]


def latest_sync_run_start() -> str | None:
    """
    When the newest run started, whatever became of it, or None if there has never been one.

    This is what the scheduler paces itself from: a run that failed or was cancelled
    still counts as "we tried", so the next attempt waits the full interval rather than
    hammering Audible every tick while something is broken.
    """
    with closing(_get_connection()) as conn:
        return conn.execute("SELECT MAX(started_at) FROM sync_runs").fetchone()[0]


def get_sync_runs(limit: int = 20, offset: int = 0, *, account_id: int | None = None) -> list[SyncRun]:
    """
    Run history, newest first, for one account or all.

    Unused by the pipeline: this is the read the API and the UI are for, and `offset`
    is how a history view pages.
    """
    sql = "SELECT * FROM sync_runs"
    params: tuple = ()
    if account_id is not None:
        sql = f"{sql} WHERE account_id = ?"
        params = (account_id,)
    with closing(_get_connection()) as conn:
        rows = conn.execute(f"{sql} ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?", (*params, limit, offset))
        return [SyncRun.from_row(row) for row in rows]


def get_sync_run(run_id: int) -> SyncRun | None:
    with closing(_get_connection()) as conn:
        row = conn.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,)).fetchone()
    return SyncRun.from_row(row) if row is not None else None


def count_sync_runs(*, account_id: int | None = None) -> int:
    with closing(_get_connection()) as conn:
        if account_id is None:
            return conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM sync_runs WHERE account_id = ?", (account_id,)).fetchone()[0]


# --- accounts ------------------------------------------------------------------
# One row per marketplace login. The credentials are stored as the JSON of
# `Authenticator.to_dict()`; `src.accounts` is the only thing that encodes or decodes
# them, this layer just keeps the text.


def add_account(
    name: str,
    country_code: str,
    *,
    auth: dict | None,
    customer_name: str | None = None,
    monitor_existing: bool = True,
) -> int:
    """Create an account and return its id. `auth` None means it still needs a login."""
    with closing(_get_connection()) as conn:
        cursor = conn.execute(
            """
            INSERT INTO accounts (name, country_code, customer_name, auth, monitor_existing, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                country_code,
                customer_name,
                json.dumps(auth) if auth is not None else None,
                monitor_existing,
                _utcnow(),
            ),
        )
        conn.commit()
        return cursor.lastrowid


def get_accounts() -> list[Account]:
    """Every account, oldest first."""
    with closing(_get_connection()) as conn:
        return [Account.from_row(row) for row in conn.execute("SELECT * FROM accounts ORDER BY id")]


def get_account(account_id: int) -> Account | None:
    with closing(_get_connection()) as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return Account.from_row(row) if row is not None else None


def update_account(
    account_id: int,
    *,
    name: str | None = None,
    country_code: str | None = None,
    customer_name: str | None = None,
    enabled: bool | None = None,
) -> None:
    """Change the given fields and leave the rest alone."""
    updates = []
    values: list = []
    for column, value in (
        ("name", name),
        ("country_code", country_code),
        ("customer_name", customer_name),
        ("enabled", enabled),
    ):
        if value is not None:
            updates.append(f"{column} = ?")
            values.append(value)
    if not updates:
        return
    values.append(account_id)
    with closing(_get_connection()) as conn:
        conn.execute(f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?", values)
        conn.commit()


def save_account_auth(account_id: int, auth: dict | None) -> None:
    """Store the credentials, or clear them (None) so the account reads as needing a login."""
    with closing(_get_connection()) as conn:
        conn.execute(
            "UPDATE accounts SET auth = ? WHERE id = ?",
            (json.dumps(auth) if auth is not None else None, account_id),
        )
        conn.commit()


def mark_account_synced(account_id: int) -> None:
    with closing(_get_connection()) as conn:
        conn.execute("UPDATE accounts SET last_synced_at = ? WHERE id = ?", (_utcnow(), account_id))
        conn.commit()


def delete_account(account_id: int) -> None:
    """
    Remove an account with its library rows and run history.

    The files on disk are left alone: they are the user's, and a re-added account files
    the same book to the same path and reuses them.
    """
    with closing(_get_connection()) as conn:
        conn.execute("DELETE FROM library WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM sync_runs WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        conn.commit()


# --- settings ------------------------------------------------------------------
# Text in, text out: `src.settings` is the only reader and writer, and it owns the
# parsing, the defaults and the validation. Keeping this layer typeless means a value
# the running build cannot parse is still stored and read back rather than lost.


def get_settings() -> dict[str, str]:
    """Every stored setting, keyed by name. Empty on a database that has never been seeded."""
    with closing(_get_connection()) as conn:
        return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}


def save_settings(values: dict[str, str]) -> None:
    """
    Write settings, replacing any that already exist.

    An upsert per key rather than a wipe and rewrite, so a partial update from the API
    leaves every other setting exactly as it was. `updated_at` is stamped on the keys
    written, which is what tells a UI when each value last changed.
    """
    if not values:
        return

    now = _utcnow()
    with closing(_get_connection()) as conn:
        conn.executemany(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            [(key, value, now) for key, value in values.items()],
        )
        conn.commit()


def has_settings() -> bool:
    """Whether anything has ever been stored, which is what decides if config.ini is seeded."""
    with closing(_get_connection()) as conn:
        return bool(conn.execute("SELECT EXISTS(SELECT 1 FROM settings)").fetchone()[0])
