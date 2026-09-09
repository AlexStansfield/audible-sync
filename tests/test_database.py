import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import pytest

import src.database as database
from src.model import Book, BookStatus
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
        "is_consumable",
        "attempts",
        "last_error",
        "last_attempt_at",
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
    expected = {
        "pdf_path",
        "cover_path",
        "annotations_path",
        "has_pdf",
        "encoding_format",
        "downloaded_at",
        "is_consumable",
        "attempts",
        "last_error",
        "last_attempt_at",
    }
    assert expected <= columns


def test_update_books_counts_only_the_rows_it_inserts(db):
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


def test_init_db_indexes_the_download_queue(db):
    """The queue selects on `status` and orders by `date_added`, so that index is composite."""
    conn = sqlite3.connect(database.DB_FILE)
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(library)")}
    conn.close()

    assert {"idx_library_date_added", "idx_library_status_date_added"} <= indexes
    # Superseded: the composite serves a plain status lookup from its leading column
    assert "idx_library_status" not in indexes


def test_get_books_returns_book_objects(db):
    database.update_books([make_book("B001")])
    assert all(isinstance(book, Book) for book in database.get_books())


def test_get_book_by_asin_round_trips_a_book(db):
    """
    Proves the `json.dumps` in `update_books` and `Book.from_row`'s decode are symmetric.

    The whole-dataclass equality is also load-bearing on `attempts`: the field default
    and the column default have to agree or a freshly inserted book stops matching.
    """
    original = make_book(authors=["A", "B"], series=[{"title": "S", "sequence": "1"}], has_pdf=True)
    database.update_books([original])

    assert database.get_book_by_asin("B001") == replace(original, status="waiting_download")


def test_get_book_by_asin_on_a_legacy_migrated_database(tmp_path, monkeypatch):
    """
    A database that predates most of the schema still reads back.

    `_migrate_schema` only adds the nine later columns, so `get_books` and
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


def _claimable(asin: str = "B001") -> None:
    """Put one book in the library, waiting to be downloaded."""
    database.update_books([make_book(asin)])


def test_claim_book_for_download_takes_the_book_and_counts_the_attempt(db):
    _claimable()

    assert database.claim_book_for_download("B001") is True

    book = database.get_book_by_asin("B001")
    assert book.status is BookStatus.DOWNLOADING
    assert book.attempts == 1
    assert book.last_attempt_at is not None


def test_claim_book_for_download_refuses_a_book_another_run_already_holds(db):
    """The second caller matches no rows, so two runs can never work on one book."""
    _claimable()
    assert database.claim_book_for_download("B001") is True

    assert database.claim_book_for_download("B001") is False
    assert database.get_book_by_asin("B001").attempts == 1


def test_claim_book_for_download_takes_a_book_out_of_the_queue(db):
    database.update_books([make_book("B001"), make_book("B002")])
    database.claim_book_for_download("B001")

    assert [book.asin for book in database.get_books_to_download()] == ["B002"]


def test_claim_book_for_download_reclaims_a_download_abandoned_long_enough_ago(db):
    """A process killed mid-download must not strand its book forever."""
    _claimable()
    database.claim_book_for_download("B001")

    conn = sqlite3.connect(database.DB_FILE)
    conn.execute("UPDATE library SET last_attempt_at = '2020-01-01T00:00:00+00:00' WHERE asin = 'B001'")
    conn.commit()
    conn.close()

    assert database.claim_book_for_download("B001") is True
    assert database.get_book_by_asin("B001").attempts == 2


def test_claim_book_for_download_reclaims_a_downloading_row_with_no_timestamp(db):
    """A hand-edited row with no `last_attempt_at` would otherwise never be picked up again."""
    _claimable()
    conn = sqlite3.connect(database.DB_FILE)
    conn.execute("UPDATE library SET status = 'downloading', last_attempt_at = NULL WHERE asin = 'B001'")
    conn.commit()
    conn.close()

    assert database.claim_book_for_download("B001") is True


def test_claim_book_for_download_does_not_reclaim_a_download_still_running(db):
    _claimable()
    database.claim_book_for_download("B001")

    assert database.claim_book_for_download("B001", stale_after=3600) is False


@pytest.mark.parametrize("status", [BookStatus.DOWNLOADED, BookStatus.FAILED])
def test_claim_book_for_download_ignores_a_finished_or_failed_book(db, status):
    _claimable()
    conn = sqlite3.connect(database.DB_FILE)
    conn.execute("UPDATE library SET status = ? WHERE asin = 'B001'", (status,))
    conn.commit()
    conn.close()

    assert database.claim_book_for_download("B001") is False


def test_claim_book_for_download_returns_false_for_an_unknown_asin(db):
    assert database.claim_book_for_download("NOPE") is False


def test_mark_book_failed_returns_a_retryable_book_to_the_queue(db):
    _claimable()
    database.claim_book_for_download("B001")

    status = database.mark_book_failed("B001", "HTTPError: 503", max_attempts=3)

    assert status is BookStatus.WAITING_DOWNLOAD
    book = database.get_book_by_asin("B001")
    assert book.last_error == "HTTPError: 503"
    assert book.attempts == 1
    assert [b.asin for b in database.get_books_to_download()] == ["B001"]


def test_mark_book_failed_gives_up_once_the_attempts_are_used_up(db):
    """Three claims at a cap of three: the last one is terminal and leaves the queue empty."""
    _claimable()
    outcomes = []
    for _ in range(3):
        database.claim_book_for_download("B001")
        outcomes.append(database.mark_book_failed("B001", "boom", max_attempts=3))

    assert outcomes == [BookStatus.WAITING_DOWNLOAD, BookStatus.WAITING_DOWNLOAD, BookStatus.FAILED]
    assert database.get_book_by_asin("B001").attempts == 3
    assert database.get_books_to_download() == []


def test_mark_book_failed_terminal_gives_up_on_the_first_attempt(db):
    """A licence Audible refuses will not be granted on the third ask."""
    _claimable()
    database.claim_book_for_download("B001")

    status = database.mark_book_failed("B001", "LicenseError: Denied", max_attempts=3, terminal=True)

    assert status is BookStatus.FAILED
    assert database.get_book_by_asin("B001").attempts == 1
    assert database.get_books_to_download() == []


def test_mark_book_failed_returns_none_for_an_unknown_asin(db):
    assert database.mark_book_failed("NOPE", "boom", max_attempts=3) is None


def test_release_book_hands_the_claim_back_without_burning_an_attempt(db):
    """Expired credentials are not this book's fault, so the attempt is given back."""
    _claimable()
    database.claim_book_for_download("B001")

    database.release_book("B001")

    book = database.get_book_by_asin("B001")
    assert book.status is BookStatus.WAITING_DOWNLOAD
    assert book.attempts == 0
    assert [b.asin for b in database.get_books_to_download()] == ["B001"]


def test_release_book_leaves_a_book_it_does_not_hold_alone(db):
    _claimable()
    database.mark_book_downloaded("B001", encoding_format="m4b")

    database.release_book("B001")

    assert database.get_book_by_asin("B001").status is BookStatus.DOWNLOADED


def test_get_books_to_download_skips_downloading_and_failed_books(db):
    database.update_books([make_book("WAIT"), make_book("BUSY"), make_book("DEAD")])
    database.claim_book_for_download("BUSY")
    database.claim_book_for_download("DEAD")
    database.mark_book_failed("DEAD", "boom", max_attempts=1)

    assert [book.asin for book in database.get_books_to_download()] == ["WAIT"]


def test_get_book_by_asin_returns_the_status_as_an_enum(db):
    _claimable()
    assert database.get_book_by_asin("B001").status is BookStatus.WAITING_DOWNLOAD


def test_mark_book_downloaded_clears_a_stale_error(db):
    """A book that succeeded on its second attempt must not keep showing the first failure."""
    _claimable()
    database.claim_book_for_download("B001")
    database.mark_book_failed("B001", "HTTPError: 503", max_attempts=3)
    database.claim_book_for_download("B001")

    database.mark_book_downloaded("B001", encoding_format="m4b")

    book = database.get_book_by_asin("B001")
    assert book.last_error is None
    assert book.attempts == 2


def test_update_books_refreshes_the_mutable_api_fields_on_a_second_sync(db):
    """`is_finished` and `percent_complete` used to be frozen at first sight of the book."""
    database.update_books([make_book("B001", "Old Title")])

    added = database.update_books(
        [make_book("B001", "New Title", authors=["Ann"], is_finished=True, percent_complete=100.0)]
    )

    assert added == 0
    book = database.get_book_by_asin("B001")
    assert book.title == "New Title"
    assert book.authors == ["Ann"]
    assert book.is_finished is True
    assert book.percent_complete == 100.0


def test_update_books_never_touches_the_download_state(db):
    """Everything the downloader owns survives a re-sync untouched."""
    database.update_books([make_book("B001")])
    database.claim_book_for_download("B001")
    database.mark_book_downloaded(
        "B001",
        encoding_format="oga",
        pdf_path="/lib/book.pdf",
        cover_path="/lib/book_cover.jpg",
        annotations_path="/lib/book_annotations.json",
    )
    before = database.get_book_by_asin("B001")

    database.update_books([make_book("B001", "Renamed", is_finished=True)])

    after = database.get_book_by_asin("B001")
    assert after.status is BookStatus.DOWNLOADED
    assert after.attempts == before.attempts == 1
    assert after.last_error is None
    assert after.last_attempt_at == before.last_attempt_at
    assert after.pdf_path == "/lib/book.pdf"
    assert after.cover_path == "/lib/book_cover.jpg"
    assert after.annotations_path == "/lib/book_annotations.json"
    assert after.encoding_format == "oga"
    assert after.downloaded_at == before.downloaded_at
    # ...while the API fields did refresh
    assert after.title == "Renamed"


def test_update_books_keeps_the_original_date_added(db):
    """`date_added` is the incremental sync cursor; moving it would skip or re-fetch purchases."""
    database.update_books([make_book("B001", date_added="2024-01-01T00:00:00Z")])

    database.update_books([make_book("B001", date_added="2025-01-01T00:00:00Z")])

    assert database.get_book_by_asin("B001").date_added == "2024-01-01T00:00:00Z"
    assert database.latest_date_added() == "2024-01-01T00:00:00Z"


def test_init_db_drops_the_superseded_status_index(db):
    """An existing database carrying the old single-column index is upgraded, not left with both."""
    conn = sqlite3.connect(database.DB_FILE)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_library_status ON library(status)")
    conn.commit()
    conn.close()

    database.init_db()

    conn = sqlite3.connect(database.DB_FILE)
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(library)")}
    conn.close()
    assert "idx_library_status" not in indexes
    assert "idx_library_status_date_added" in indexes


def test_update_books_parks_a_withdrawn_book_instead_of_queueing_it(db):
    """A Plus title Audible has withdrawn must never enter the queue and cost a licence call."""
    database.update_books([make_book("GONE", is_consumable=False)])

    book = database.get_book_by_asin("GONE")
    assert book.status is BookStatus.UNAVAILABLE
    assert book.is_consumable is False
    assert database.get_books_to_download() == []


def test_update_books_returns_a_restored_book_to_the_queue(db):
    """Audible offers withdrawn Plus titles again; the next sync must pick that up on its own."""
    database.update_books([make_book("BACK", is_consumable=False)])
    assert database.get_books_to_download() == []

    database.update_books([make_book("BACK", is_consumable=True)])

    book = database.get_book_by_asin("BACK")
    assert book.status is BookStatus.WAITING_DOWNLOAD
    assert book.is_consumable is True
    assert [b.asin for b in database.get_books_to_download()] == ["BACK"]


def test_update_books_parks_a_waiting_book_that_has_been_withdrawn(db):
    database.update_books([make_book("B001")])

    database.update_books([make_book("B001", is_consumable=False)])

    assert database.get_book_by_asin("B001").status is BookStatus.UNAVAILABLE


@pytest.mark.parametrize("status", [BookStatus.DOWNLOADED, BookStatus.DOWNLOADING, BookStatus.FAILED])
def test_update_books_leaves_every_other_status_alone_when_rights_change(db, status):
    """Withdrawal only moves a book between the queue and `unavailable`, never out of these."""
    database.update_books([make_book("B001")])
    conn = sqlite3.connect(database.DB_FILE)
    conn.execute("UPDATE library SET status = ? WHERE asin = 'B001'", (status,))
    conn.commit()
    conn.close()

    database.update_books([make_book("B001", is_consumable=False)])

    book = database.get_book_by_asin("B001")
    assert book.status is status
    # ...but the flag itself still refreshes, so a UI can say why
    assert book.is_consumable is False


def test_mark_book_unavailable_parks_a_book_without_failing_it(db):
    database.update_books([make_book("B001")])
    database.claim_book_for_download("B001")

    database.mark_book_unavailable("B001", "Audible did not grant a license (status Denied)")

    book = database.get_book_by_asin("B001")
    assert book.status is BookStatus.UNAVAILABLE
    assert book.is_consumable is False
    assert "Denied" in book.last_error
    # The attempt really happened, but nothing terminal is decided from it here
    assert book.attempts == 1
    assert database.get_books_to_download() == []


def test_a_parked_book_comes_back_on_the_next_sync(db):
    """The full round trip: denied at download time, then restored by a later sync."""
    database.update_books([make_book("B001")])
    database.claim_book_for_download("B001")
    database.mark_book_unavailable("B001", "Denied")

    database.update_books([make_book("B001", is_consumable=True)])

    assert database.get_book_by_asin("B001").status is BookStatus.WAITING_DOWNLOAD
    assert [b.asin for b in database.get_books_to_download()] == ["B001"]
