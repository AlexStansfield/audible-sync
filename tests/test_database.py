import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import pytest

import src.database as database
from src.model import Book
from tests.conftest import make_book


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the database module at a fresh temporary file."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_FILE", str(db_file))
    database.init_db()
    return db_file


def test_init_db_creates_library_table_with_all_columns(db):
    conn = sqlite3.connect(db)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(library)")]
    conn.close()
    assert columns[0] == "asin"
    # By name, not position: nothing indexes a row positionally any more.
    assert set(columns) == {
        "asin",
        "title",
        "subtitle",
        "authors",
        "narrators",
        "series",
        "genres",
        "length",
        "is_finished",
        "percent_complete",
        "date_added",
        "release_date",
        "cover_url",
        "status",
        "pdf_path",
        "cover_path",
        "annotations_path",
        "has_pdf",
        "encoding_format",
        "downloaded_at",
    }


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

    books = database.get_books()
    assert {book.asin for book in books} == {"B001", "B002", "B003"}
    assert all(book.status == "waiting_download" for book in books)


def test_update_books_serialises_lists_as_json(db):
    """The columns really hold JSON text, which `Book.from_row` hides from the reader."""
    database.update_books([make_book(authors=["A", "B"], series=[{"title": "S", "sequence": "1"}], has_pdf=True)])

    conn = sqlite3.connect(database.DB_FILE)
    row = conn.execute("SELECT authors, series, has_pdf FROM library WHERE asin='B001'").fetchone()
    conn.close()

    assert row[0] == '["A", "B"]'
    assert row[1] == '[{"title": "S", "sequence": "1"}]'
    assert row[2] == 1


def test_get_book_by_asin_decodes_json_columns(db):
    database.update_books([make_book(authors=["A", "B"], series=[{"title": "S", "sequence": "1"}], has_pdf=True)])

    book = database.get_book_by_asin("B001")

    assert book.authors == ["A", "B"]
    assert book.series == [{"title": "S", "sequence": "1"}]
    assert book.has_pdf is True


def test_get_books_orders_newest_first_and_respects_limit(db):
    database.update_books(
        [
            make_book("OLD", date_added="2020-01-01"),
            make_book("NEW", date_added="2024-01-01"),
            make_book("MID", date_added="2022-01-01"),
        ]
    )
    assert [book.asin for book in database.get_books()] == ["NEW", "MID", "OLD"]
    assert [book.asin for book in database.get_books(limit=1)] == ["NEW"]


def test_mark_book_downloaded_removes_from_waiting(db):
    database.update_books([make_book("B001"), make_book("B002")])
    database.mark_book_downloaded("B001")

    waiting = [book.asin for book in database.get_books_to_download()]
    assert waiting == ["B002"]
    assert database.get_book_by_asin("B001").status == "downloaded"


def test_mark_book_downloaded_records_format_and_timestamp(db, monkeypatch):
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-01-02T03:04:05+00:00")
    database.update_books([make_book("B001")])
    database.mark_book_downloaded("B001", encoding_format="oga")

    book = database.get_book_by_asin("B001")
    assert book.encoding_format == "oga"
    assert book.downloaded_at == "2026-01-02T03:04:05+00:00"


def test_mark_book_downloaded_default_timestamp_is_utc_iso(db):
    database.update_books([make_book("B001")])
    before = datetime.now(UTC).replace(microsecond=0)
    database.mark_book_downloaded("B001")

    book = database.get_book_by_asin("B001")
    assert book.encoding_format is None
    stamp = datetime.fromisoformat(book.downloaded_at)
    assert stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0
    assert before <= stamp <= datetime.now(UTC)


def test_update_book_accessories_only_sets_given_paths(db):
    database.update_books([make_book("B001")])
    database.update_book_accessories("B001", pdf_path="/p.pdf", cover_path="/c.jpg")
    book = database.get_book_by_asin("B001")
    assert book.pdf_path == "/p.pdf"
    assert book.cover_path == "/c.jpg"
    assert book.annotations_path is None

    database.update_book_accessories("B001", annotations_path="/a.json")
    book = database.get_book_by_asin("B001")
    assert book.pdf_path == "/p.pdf"
    assert book.annotations_path == "/a.json"


def test_update_books_handles_a_duplicate_asin_within_one_batch(db):
    """A repeated ASIN in one API response used to abort the sync with nothing committed."""
    inserted = database.update_books([make_book("B001"), make_book("B001"), make_book("B002")])

    assert inserted == 2
    assert sorted(book.asin for book in database.get_books()) == ["B001", "B002"]


def test_update_books_on_an_empty_list_inserts_nothing(db):
    assert database.update_books([]) == 0
    assert database.get_books() == []


def test_latest_date_added_is_none_for_an_empty_library(db):
    assert database.latest_date_added() is None


def test_latest_date_added_returns_the_newest_regardless_of_insert_order(db):
    database.update_books(
        [
            make_book("MID", date_added="2024-06-01T00:00:00Z"),
            make_book("NEW", date_added="2024-12-01T00:00:00Z"),
            make_book("OLD", date_added="2024-01-01T00:00:00Z"),
        ]
    )

    assert database.latest_date_added() == "2024-12-01T00:00:00Z"


def test_mark_book_downloaded_records_accessory_paths_in_one_statement(db):
    database.update_books([make_book("B001")])

    database.mark_book_downloaded(
        "B001",
        encoding_format="m4b",
        pdf_path="/lib/book.pdf",
        cover_path="/lib/book_cover.jpg",
        annotations_path="/lib/book_annotations.json",
    )

    book = database.get_book_by_asin("B001")
    assert book.status == "downloaded"
    assert book.pdf_path == "/lib/book.pdf"
    assert book.cover_path == "/lib/book_cover.jpg"
    assert book.annotations_path == "/lib/book_annotations.json"


def test_mark_book_downloaded_keeps_accessory_paths_it_is_not_given(db):
    database.update_books([make_book("B001")])
    database.mark_book_downloaded("B001", encoding_format="m4b", pdf_path="/lib/book.pdf")

    database.mark_book_downloaded("B001", encoding_format="oga")

    book = database.get_book_by_asin("B001")
    assert book.pdf_path == "/lib/book.pdf"
    assert book.encoding_format == "oga"


def test_init_db_indexes_the_columns_every_run_filters_on(db):
    conn = sqlite3.connect(database.DB_FILE)
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(library)")}
    conn.close()

    assert {"idx_library_date_added", "idx_library_status"} <= indexes


def test_get_books_returns_book_objects(db):
    database.update_books([make_book("B001")])
    assert all(isinstance(book, Book) for book in database.get_books())


def test_get_book_by_asin_round_trips_a_book(db):
    """Proves the `json.dumps` in `update_books` and `Book.from_row`'s decode are symmetric."""
    original = make_book(authors=["A", "B"], series=[{"title": "S", "sequence": "1"}], has_pdf=True)
    database.update_books([original])

    assert database.get_book_by_asin("B001") == replace(original, status="waiting_download")


def test_get_book_by_asin_on_a_legacy_migrated_database(tmp_path, monkeypatch):
    """
    A database that predates most of the schema still reads back.

    `_migrate_schema` only adds the six accessory columns, so `get_books` and
    `latest_date_added` still raise on this schema (they reference `date_added`).
    `get_book_by_asin` works because `Book.from_row` falls back to field defaults
    for columns the row does not carry.
    """
    db_file = tmp_path / "old.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE library (asin TEXT PRIMARY KEY, title TEXT, status TEXT)")
    conn.execute("INSERT INTO library VALUES ('B001', 'Title', 'waiting_download')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(database, "DB_FILE", str(db_file))
    database.init_db()

    book = database.get_book_by_asin("B001")
    assert book.asin == "B001"
    assert book.title == "Title"
    assert book.status == "waiting_download"
    assert book.authors == []
    assert book.has_pdf is False
