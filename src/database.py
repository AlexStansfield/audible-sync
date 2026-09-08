import json
import sqlite3

from src.model import Book

DB_FILE = "data/audible_sync.db"


def _get_connection():
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
            has_pdf BOOLEAN DEFAULT 0
        )
    """)
    conn.commit()

    # Migrate existing databases to add new columns
    _migrate_schema(conn)

    conn.close()


def _migrate_schema(conn):
    """Add new columns to existing databases if they don't exist"""
    cursor = conn.cursor()

    # Get existing columns
    cursor.execute("PRAGMA table_info(library)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    # Add missing columns
    new_columns = {"pdf_path": "TEXT", "cover_path": "TEXT", "annotations_path": "TEXT", "has_pdf": "BOOLEAN DEFAULT 0"}

    for column, column_type in new_columns.items():
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE library ADD COLUMN {column} {column_type}")

    conn.commit()


def update_books(books: list[Book]):
    conn = _get_connection()
    cursor = conn.cursor()

    books_synced = 0
    for book in books:
        # Check if the book already exists in the database
        existing_book = get_book_by_asin(book.asin)

        # If the book doesn't exist, insert it into the database
        if existing_book is None:
            cursor.execute(
                """
            INSERT INTO library (asin, title, subtitle, authors, narrators, series, genres, length,
                                 is_finished, percent_complete, date_added, release_date, cover_url,
                                 status, has_pdf)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
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
                ),
            )
            books_synced += 1

    conn.commit()
    conn.close()

    return books_synced


def get_books(limit=None):
    conn = _get_connection()
    cursor = conn.cursor()
    sql = "SELECT * FROM library ORDER BY date_added DESC"
    if limit:
        sql = f"{sql} LIMIT {limit}"

    cursor.execute(sql)
    return cursor.fetchall()


def get_books_to_download():
    conn = _get_connection()
    cursor = conn.cursor()
    sql = "SELECT * FROM library WHERE status = 'waiting_download' ORDER BY date_added ASC"
    cursor.execute(sql)
    return cursor.fetchall()


def get_book_by_asin(asin):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM library WHERE asin=?", (asin,))
    return cursor.fetchone()


def mark_book_downloaded(asin):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE library set status = 'downloaded' WHERE asin=?", (asin,))
    conn.commit()
    conn.close()


def update_book_accessories(asin, pdf_path=None, cover_path=None, annotations_path=None):
    """Update the paths for downloaded accessories (PDF, cover, annotations)"""
    conn = _get_connection()
    cursor = conn.cursor()

    updates = []
    values = []

    if pdf_path is not None:
        updates.append("pdf_path = ?")
        values.append(pdf_path)
    if cover_path is not None:
        updates.append("cover_path = ?")
        values.append(cover_path)
    if annotations_path is not None:
        updates.append("annotations_path = ?")
        values.append(annotations_path)

    if updates:
        values.append(asin)
        sql = f"UPDATE library SET {', '.join(updates)} WHERE asin = ?"
        cursor.execute(sql, values)
        conn.commit()

    conn.close()
