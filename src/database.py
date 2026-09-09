import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from src.model import Book, BookStatus
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS library (
            asin TEXT PRIMARY KEY,
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
            pdf_path TEXT,
            cover_path TEXT,
            annotations_path TEXT,
            has_pdf BOOLEAN DEFAULT 0,
            encoding_format TEXT,
            downloaded_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            last_attempt_at TEXT
        )
    """)
    conn.commit()

    # Migrate existing databases to add new columns
    _migrate_schema(conn)

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

    Guarded by the columns actually present so an older database that predates a column
    is still upgradable: the composite raises on a schema with no `date_added`.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(library)")
    existing_columns = {row[1] for row in cursor.fetchall()}

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
        "attempts": "INTEGER NOT NULL DEFAULT 0",
        "last_error": "TEXT",
        "last_attempt_at": "TEXT",
    }

    for column, column_type in new_columns.items():
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE library ADD COLUMN {column} {column_type}")

    conn.commit()


def update_books(books: list[Book]) -> int:
    """
    Insert new books, refresh the mutable API fields on the ones already stored, and
    return how many rows were genuinely added.

    `asin` is the primary key, so one upsert does the de-duplication. Checking first on
    a second connection could not see the rows this transaction had already inserted, so
    a repeated ASIN inside a single API response raised IntegrityError and rolled the
    whole sync back.

    The conflict clause deliberately refreshes only what Audible owns. It never writes
    `date_added` (the incremental sync cursor - moving it would skip or re-fetch
    purchases), and never `status`, `attempts`, `last_error`, `last_attempt_at`, the
    three accessory paths, `encoding_format` or `downloaded_at`, all of which belong to
    the downloader. Before this it was an INSERT OR IGNORE, so `is_finished` and
    `percent_complete` stayed frozen at whatever they were the day a book was first seen.
    """
    # Decoded again by `Book.from_row`; keep the two sides in step.
    rows = [
        (
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
            # Insert only: the conflict clause below leaves an existing status alone
            BookStatus.WAITING_DOWNLOAD,
            book.has_pdf,
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
            INSERT INTO library (asin, title, subtitle, authors, narrators, series, genres, length,
                                 is_finished, percent_complete, date_added, release_date, cover_url,
                                 status, has_pdf)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asin) DO UPDATE SET
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
                has_pdf = excluded.has_pdf
            """,
            rows,
        )
        books_added = cursor.execute("SELECT COUNT(*) FROM library").fetchone()[0] - books_before
        conn.commit()

    return books_added


def get_books(limit: int | None = None) -> list[Book]:
    """All books, newest `date_added` first."""
    sql = "SELECT * FROM library ORDER BY date_added DESC"
    params: tuple = ()
    if limit is not None:
        sql = f"{sql} LIMIT ?"
        params = (limit,)

    with closing(_get_connection()) as conn:
        return [Book.from_row(row) for row in conn.execute(sql, params)]


def get_books_to_download() -> list[Book]:
    """Books still waiting to be downloaded, oldest first."""
    with closing(_get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM library WHERE status = ? ORDER BY date_added ASC", (BookStatus.WAITING_DOWNLOAD,)
        )
        return [Book.from_row(row) for row in rows]


def get_book_by_asin(asin: str) -> Book | None:
    with closing(_get_connection()) as conn:
        row = conn.execute("SELECT * FROM library WHERE asin=?", (asin,)).fetchone()
    return Book.from_row(row) if row is not None else None


def latest_date_added() -> str | None:
    """
    The newest `date_added` in the library, or None when it is empty.

    Sync uses this as its incremental cursor. Reading it directly keeps that
    cursor independent of how `get_books` happens to sort or paginate.
    """
    with closing(_get_connection()) as conn:
        return conn.execute("SELECT MAX(date_added) FROM library").fetchone()[0]


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


def claim_book_for_download(asin: str, *, stale_after: int = STALE_CLAIM_SECONDS) -> bool:
    """
    Take ownership of one book, moving it to `downloading` and counting the attempt.

    The whole decision is a single UPDATE, so two processes - a scheduler tick starting
    while a manual run is still going - cannot both take the same book: the second
    matches no rows, because the first has already moved it out of `waiting_download`.

    A row abandoned in `downloading` by a process that died is claimable again once its
    `last_attempt_at` is older than `stale_after`. A NULL timestamp counts as stale too,
    since such a row would otherwise never be picked up by anything again.

    Args:
        asin: The book to claim
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
             WHERE asin = ?
               AND (status = ?
                    OR (status = ? AND (last_attempt_at IS NULL OR last_attempt_at < ?)))
            """,
            (
                BookStatus.DOWNLOADING,
                _utcnow(),
                asin,
                BookStatus.WAITING_DOWNLOAD,
                BookStatus.DOWNLOADING,
                _stale_cutoff(stale_after),
            ),
        )
        conn.commit()

    return cursor.rowcount == 1


def mark_book_failed(asin: str, error: str, *, max_attempts: int, terminal: bool = False) -> BookStatus | None:
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
        asin: The book that failed
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
             WHERE asin = ?
         RETURNING status
            """,
            (terminal, max_attempts, BookStatus.FAILED, BookStatus.WAITING_DOWNLOAD, error, asin),
        ).fetchone()
        conn.commit()

    return None if row is None else BookStatus(row["status"])


def release_book(asin: str) -> None:
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
             WHERE asin = ? AND status = ?
            """,
            (BookStatus.WAITING_DOWNLOAD, asin, BookStatus.DOWNLOADING),
        )
        conn.commit()


def mark_book_downloaded(
    asin: str,
    encoding_format: str | None = None,
    *,
    pdf_path: str | None = None,
    cover_path: str | None = None,
    annotations_path: str | None = None,
) -> None:
    """
    Set the book to downloaded and record when, in which format, and where its
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
                   pdf_path = COALESCE(?, pdf_path),
                   cover_path = COALESCE(?, cover_path),
                   annotations_path = COALESCE(?, annotations_path)
             WHERE asin = ?
            """,
            (BookStatus.DOWNLOADED, encoding_format, _utcnow(), pdf_path, cover_path, annotations_path, asin),
        )
        conn.commit()


def update_book_accessories(
    asin: str,
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

    values.append(asin)
    with closing(_get_connection()) as conn:
        conn.execute(f"UPDATE library SET {', '.join(updates)} WHERE asin = ?", values)
        conn.commit()
