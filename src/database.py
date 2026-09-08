import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from src.model import Book

DB_FILE = "data/audible_sync.db"


def _get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_FILE)


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
            downloaded_at TEXT
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

    Sync reads the newest `date_added` and the downloader selects on `status`;
    both are full table scans otherwise. Guarded by the columns actually present
    so an older database that predates a column is still upgradable.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(library)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    for column in ("date_added", "status"):
        if column in existing_columns:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_library_{column} ON library({column})")

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
    }

    for column, column_type in new_columns.items():
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE library ADD COLUMN {column} {column_type}")

    conn.commit()


def update_books(books: list[Book]) -> int:
    """
    Insert books that are not in the library yet and return how many were added.

    `asin` is the primary key, so INSERT OR IGNORE does the de-duplication in one
    statement. Checking first on a second connection could not see the rows this
    transaction had already inserted, so a repeated ASIN inside a single API
    response raised IntegrityError and rolled the whole sync back.
    """
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
            "waiting_download",
            book.has_pdf,
        )
        for book in books
    ]

    with closing(_get_connection()) as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT OR IGNORE INTO library (asin, title, subtitle, authors, narrators, series, genres, length,
                                           is_finished, percent_complete, date_added, release_date, cover_url,
                                           status, has_pdf)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        books_synced = cursor.rowcount
        conn.commit()

    return books_synced


def get_books(limit: int | None = None) -> list[tuple]:
    """All books, newest `date_added` first."""
    sql = "SELECT * FROM library ORDER BY date_added DESC"
    params: tuple = ()
    if limit is not None:
        sql = f"{sql} LIMIT ?"
        params = (limit,)

    with closing(_get_connection()) as conn:
        return conn.execute(sql, params).fetchall()


def get_books_to_download() -> list[tuple]:
    """Books still waiting to be downloaded, oldest first."""
    with closing(_get_connection()) as conn:
        return conn.execute(
            "SELECT * FROM library WHERE status = 'waiting_download' ORDER BY date_added ASC"
        ).fetchall()


def get_book_by_asin(asin: str) -> tuple | None:
    with closing(_get_connection()) as conn:
        return conn.execute("SELECT * FROM library WHERE asin=?", (asin,)).fetchone()


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
    """
    with closing(_get_connection()) as conn:
        conn.execute(
            """
            UPDATE library
               SET status = 'downloaded',
                   encoding_format = ?,
                   downloaded_at = ?,
                   pdf_path = COALESCE(?, pdf_path),
                   cover_path = COALESCE(?, cover_path),
                   annotations_path = COALESCE(?, annotations_path)
             WHERE asin = ?
            """,
            (encoding_format, _utcnow(), pdf_path, cover_path, annotations_path, asin),
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
