import sqlite3
from datetime import UTC, datetime

import pytest

import src.database as database
from src.model import Book


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the database module at a fresh temporary file."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_FILE", str(db_file))
    database.init_db()
    return db_file


def make_book(asin="B001", title="Title", date_added="2024-01-01T00:00:00", **kwargs):
    return Book(asin=asin, title=title, date_added=date_added, **kwargs)


def test_init_db_creates_library_table_with_all_columns(db):
    conn = sqlite3.connect(db)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(library)")]
    conn.close()
    assert columns[:2] == ["asin", "title"]
    assert columns[13] == "status"
    assert columns[17] == "has_pdf"
    assert columns[18] == "encoding_format"
    assert columns[19] == "downloaded_at"


def test_migrate_schema_adds_missing_columns(tmp_path, monkeypatch):
    db_file = tmp_path / "old.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE library (asin TEXT PRIMARY KEY, title TEXT, status TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(database, "DB_FILE", str(db_file))
    database.init_db()

    conn = sqlite3.connect(db_file)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(library)")}
    conn.close()
    expected = {"pdf_path", "cover_path", "annotations_path", "has_pdf", "encoding_format", "downloaded_at"}
    assert expected <= columns


def test_update_books_inserts_new_and_skips_existing(db):
    assert database.update_books([make_book("B001"), make_book("B002")]) == 2
    assert database.update_books([make_book("B001"), make_book("B003")]) == 1

    rows = database.get_books()
    assert {row[0] for row in rows} == {"B001", "B002", "B003"}
    assert all(row[13] == "waiting_download" for row in rows)


def test_update_books_serialises_lists_as_json(db):
    database.update_books([make_book(authors=["A", "B"], series=[{"title": "S", "sequence": "1"}], has_pdf=True)])
    row = database.get_book_by_asin("B001")
    assert row[3] == '["A", "B"]'
    assert row[5] == '[{"title": "S", "sequence": "1"}]'
    assert row[17] == 1


def test_get_books_orders_newest_first_and_respects_limit(db):
    database.update_books(
        [
            make_book("OLD", date_added="2020-01-01"),
            make_book("NEW", date_added="2024-01-01"),
            make_book("MID", date_added="2022-01-01"),
        ]
    )
    assert [row[0] for row in database.get_books()] == ["NEW", "MID", "OLD"]
    assert [row[0] for row in database.get_books(limit=1)] == ["NEW"]


def test_mark_book_downloaded_removes_from_waiting(db):
    database.update_books([make_book("B001"), make_book("B002")])
    database.mark_book_downloaded("B001")

    waiting = [row[0] for row in database.get_books_to_download()]
    assert waiting == ["B002"]
    assert database.get_book_by_asin("B001")[13] == "downloaded"


def test_mark_book_downloaded_records_format_and_timestamp(db, monkeypatch):
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-01-02T03:04:05+00:00")
    database.update_books([make_book("B001")])
    database.mark_book_downloaded("B001", encoding_format="oga")

    row = database.get_book_by_asin("B001")
    assert row[18] == "oga"
    assert row[19] == "2026-01-02T03:04:05+00:00"


def test_mark_book_downloaded_default_timestamp_is_utc_iso(db):
    database.update_books([make_book("B001")])
    before = datetime.now(UTC).replace(microsecond=0)
    database.mark_book_downloaded("B001")

    row = database.get_book_by_asin("B001")
    assert row[18] is None
    stamp = datetime.fromisoformat(row[19])
    assert stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0
    assert before <= stamp <= datetime.now(UTC)


def test_update_book_accessories_only_sets_given_paths(db):
    database.update_books([make_book("B001")])
    database.update_book_accessories("B001", pdf_path="/p.pdf", cover_path="/c.jpg")
    row = database.get_book_by_asin("B001")
    assert row[14] == "/p.pdf"
    assert row[15] == "/c.jpg"
    assert row[16] is None

    database.update_book_accessories("B001", annotations_path="/a.json")
    row = database.get_book_by_asin("B001")
    assert row[14] == "/p.pdf"
    assert row[16] == "/a.json"
