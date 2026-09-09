import sqlite3

import pytest

from src.model import Book

ALL_COLUMNS = (
    "asin TEXT PRIMARY KEY, title TEXT, subtitle TEXT, authors JSON, narrators JSON, series JSON, "
    "genres JSON, length INTEGER, is_finished BOOLEAN, percent_complete REAL, date_added TEXT, "
    "release_date TEXT, cover_url TEXT, status TEXT, pdf_path TEXT, cover_path TEXT, "
    "annotations_path TEXT, has_pdf BOOLEAN, encoding_format TEXT, downloaded_at TEXT"
)


def fetch(columns: str, values: dict, select: str = "*") -> sqlite3.Row:
    """Insert one row into a throwaway table and read it back as a `sqlite3.Row`."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE library ({columns})")
    names = ", ".join(values)
    placeholders = ", ".join("?" * len(values))
    conn.execute(f"INSERT INTO library ({names}) VALUES ({placeholders})", tuple(values.values()))
    return conn.execute(f"SELECT {select} FROM library").fetchone()


def test_from_row_decodes_the_json_columns():
    row = fetch(
        ALL_COLUMNS,
        {
            "asin": "B001",
            "title": "Title",
            "authors": '["Ann", "Bob"]',
            "narrators": '["Nell"]',
            "series": '[{"title": "S", "sequence": null}]',
            "genres": '["Fiction"]',
        },
    )

    book = Book.from_row(row)

    assert book.authors == ["Ann", "Bob"]
    assert book.narrators == ["Nell"]
    assert book.series == [{"title": "S", "sequence": None}]
    assert book.genres == ["Fiction"]


@pytest.mark.parametrize("stored", [None, "", "null", "[]"])
def test_from_row_treats_empty_json_columns_as_empty_lists(stored):
    row = fetch(ALL_COLUMNS, {"asin": "B001", "title": "T", "authors": stored})
    assert Book.from_row(row).authors == []


@pytest.mark.parametrize(
    ("stored", "expected"),
    [(0, False), (1, True), (None, False)],
)
def test_from_row_coerces_sqlite_integers_to_bool(stored, expected):
    row = fetch(ALL_COLUMNS, {"asin": "B001", "title": "T", "has_pdf": stored, "is_finished": stored})
    book = Book.from_row(row)

    assert book.has_pdf is expected
    assert book.is_finished is expected


def test_from_row_reads_the_database_only_fields():
    row = fetch(
        ALL_COLUMNS,
        {
            "asin": "B001",
            "title": "T",
            "status": "downloaded",
            "pdf_path": "/lib/b.pdf",
            "cover_path": "/lib/b.jpg",
            "annotations_path": "/lib/b.json",
            "encoding_format": "oga",
            "downloaded_at": "2026-01-02T03:04:05+00:00",
        },
    )

    book = Book.from_row(row)

    assert book.status == "downloaded"
    assert book.pdf_path == "/lib/b.pdf"
    assert book.cover_path == "/lib/b.jpg"
    assert book.annotations_path == "/lib/b.json"
    assert book.encoding_format == "oga"
    assert book.downloaded_at == "2026-01-02T03:04:05+00:00"


def test_from_row_normalises_null_text_columns():
    row = fetch(ALL_COLUMNS, {"asin": "B001"})
    book = Book.from_row(row)

    assert book.title == ""
    assert book.subtitle == ""
    assert book.cover_url == ""
    assert book.length == 0
    assert book.percent_complete == 0.0
    # These two stay None: sync and the {year} placeholder both distinguish "absent"
    assert book.date_added is None
    assert book.release_date is None


def test_from_row_ignores_column_order():
    """`_migrate_schema` appends columns, so `SELECT *` order need not match the DDL."""
    reordered = "status TEXT, has_pdf BOOLEAN, asin TEXT, series JSON, title TEXT, cover_url TEXT"
    row = fetch(
        reordered,
        {
            "asin": "B001",
            "title": "Right Title",
            "status": "waiting_download",
            "has_pdf": 1,
            "series": '[{"title": "S", "sequence": "1"}]',
            "cover_url": "https://img/c.jpg",
        },
    )

    book = Book.from_row(row)

    assert book.asin == "B001"
    assert book.title == "Right Title"
    assert book.status == "waiting_download"
    assert book.has_pdf is True
    assert book.series == [{"title": "S", "sequence": "1"}]
    assert book.cover_url == "https://img/c.jpg"


def test_from_row_falls_back_to_defaults_for_columns_the_row_does_not_have():
    row = fetch(ALL_COLUMNS, {"asin": "B001", "title": "T"}, select="asin, title")
    book = Book.from_row(row)

    assert book.asin == "B001"
    assert book.authors == []
    assert book.has_pdf is False
    assert book.status is None


def test_from_row_requires_an_asin():
    row = fetch(ALL_COLUMNS, {"asin": "B001", "title": "T"}, select="title")
    with pytest.raises(KeyError):
        Book.from_row(row)


def test_book_defaults_do_not_share_list_instances():
    first = Book("B001", "One")
    first.authors.append("Ann")

    assert Book("B002", "Two").authors == []
