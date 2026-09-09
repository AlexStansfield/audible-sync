import sqlite3

import pytest

from src.model import Book, BookStatus, sort_series
from tests.conftest import make_book

ALL_COLUMNS = (
    "asin TEXT PRIMARY KEY, title TEXT, subtitle TEXT, authors JSON, narrators JSON, series JSON, "
    "genres JSON, length INTEGER, is_finished BOOLEAN, percent_complete REAL, date_added TEXT, "
    "release_date TEXT, cover_url TEXT, status TEXT, pdf_path TEXT, cover_path TEXT, "
    "annotations_path TEXT, has_pdf BOOLEAN, encoding_format TEXT, downloaded_at TEXT, "
    "is_consumable BOOLEAN, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, last_attempt_at TEXT"
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


@pytest.mark.parametrize("stored", ["waiting_download", "downloading", "downloaded", "failed"])
def test_from_row_coerces_the_status_to_the_enum(stored):
    row = fetch(ALL_COLUMNS, {"asin": "B001", "title": "T", "status": stored})

    assert Book.from_row(row).status is BookStatus(stored)


def test_from_row_falls_back_to_none_on_an_unrecognised_status():
    """A database written by another version must stay readable, not raise on one column."""
    row = fetch(ALL_COLUMNS, {"asin": "B001", "title": "T", "status": "archived"})

    assert Book.from_row(row).status is None


def test_book_status_members_compare_equal_to_their_stored_text():
    """Why `StrEnum`: the column keeps the text it already holds, so no row was rewritten."""
    assert BookStatus.DOWNLOADED == "downloaded"
    assert BookStatus.WAITING_DOWNLOAD == "waiting_download"


def test_from_row_reads_the_retry_columns():
    row = fetch(
        ALL_COLUMNS,
        {
            "asin": "B001",
            "title": "T",
            "attempts": 2,
            "last_error": "LicenseError: denied",
            "last_attempt_at": "2026-01-02T03:04:05+00:00",
        },
    )

    book = Book.from_row(row)

    assert book.attempts == 2
    assert book.last_error == "LicenseError: denied"
    assert book.last_attempt_at == "2026-01-02T03:04:05+00:00"


def test_from_row_defaults_the_retry_columns_when_the_row_lacks_them():
    """`attempts` must default to 0, matching the column default the round-trip test relies on."""
    row = fetch(ALL_COLUMNS, {"asin": "B001", "title": "T"}, select="asin, title")
    book = Book.from_row(row)

    assert book.attempts == 0
    assert book.last_error is None
    assert book.last_attempt_at is None


def test_from_row_reads_the_consumable_flag():
    row = fetch(ALL_COLUMNS, {"asin": "B001", "title": "T", "is_consumable": 0})

    assert Book.from_row(row).is_consumable is False


@pytest.mark.parametrize("values", [{"is_consumable": None}, {}])
def test_from_row_treats_an_unset_consumable_column_as_available(values):
    """NULL means the row predates the column, not that the book was withdrawn."""
    row = fetch(ALL_COLUMNS, {"asin": "B001", "title": "T", **values})

    assert Book.from_row(row).is_consumable is True


def entry(title, sequence, series_asin=None):
    return {"title": title, "sequence": sequence, "series_asin": series_asin}


def test_primary_series_is_none_when_the_book_is_in_no_series():
    assert make_book().primary_series is None


def test_primary_series_prefers_the_lowest_sequence():
    """
    Real case: *Dune* is in "Dune" at 1 and "The Dune Sequence" at 12.

    Taking index 0 filed it under the omnibus at sequence 12; the book is early in
    "Dune", which is the series a reader means.
    """
    book = make_book(series=[entry("The Dune Sequence", "12"), entry("Dune", "1")])

    assert book.primary_series["title"] == "Dune"


def test_primary_series_does_not_depend_on_the_order_the_api_used():
    """The same two series in either order must resolve identically."""
    forwards = make_book(series=[entry("Ringworld", "1"), entry("Known Space", "12")])
    backwards = make_book(series=[entry("Known Space", "12"), entry("Ringworld", "1")])

    assert forwards.primary_series == backwards.primary_series == entry("Ringworld", "1")


def test_primary_series_breaks_a_tied_sequence_on_the_title():
    """
    Real case: Audible lists the His Dark Materials trilogy under a typo'd
    "His Dark Materialsik" as well, at the same sequence. The list endpoint returns
    the typo first for *Northern Lights* and the correct title first for books 2
    and 3, which split one trilogy across two folders.
    """
    typo_first = make_book(series=[entry("His Dark Materialsik", "1"), entry("His Dark Materials", "1")])
    typo_second = make_book(series=[entry("His Dark Materials", "3"), entry("His Dark Materialsik", "3")])

    assert typo_first.primary_series["title"] == "His Dark Materials"
    assert typo_second.primary_series["title"] == "His Dark Materials"


def test_primary_series_breaks_a_fully_tied_entry_on_the_series_asin():
    book = make_book(series=[entry("Same", "1", "B002"), entry("Same", "1", "B001")])

    assert book.primary_series["series_asin"] == "B001"


@pytest.mark.parametrize("sequence", [None, "", "Book Two"])
def test_primary_series_ranks_an_unusable_sequence_last(sequence):
    """No sequence is no evidence, so such an entry only wins if nothing else is offered."""
    book = make_book(series=[entry("Unnumbered", sequence), entry("Numbered", "7")])

    assert book.primary_series["title"] == "Numbered"


def test_primary_series_still_returns_an_unnumbered_entry_when_it_is_the_only_one():
    book = make_book(series=[entry("Companion", None)])

    assert book.primary_series == entry("Companion", None)


def test_primary_series_ignores_an_entry_with_no_title():
    """A sequence alone would render a bare '2 - Title' folder under the author."""
    assert make_book(series=[entry(None, "2")]).primary_series is None
    assert make_book(series=[entry(None, "1"), entry("Real", "9")]).primary_series["title"] == "Real"


def test_primary_series_reads_a_row_written_before_series_asin_was_stored():
    book = make_book(series=[{"title": "Old", "sequence": "2"}, {"title": "Older", "sequence": "1"}])

    assert book.primary_series["title"] == "Older"


def test_sort_series_puts_the_primary_first():
    sorted_series = sort_series([entry("The Dune Sequence", "12"), entry("Dune", "1")])

    assert [s["title"] for s in sorted_series] == ["Dune", "The Dune Sequence"]
