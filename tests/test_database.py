import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import pytest

import src.database as database
from src.model import Account, Book, BookStatus, SyncOutcome, SyncRun
from tests.conftest import ACCOUNT_ID as ACCOUNT
from tests.conftest import make_book


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the database module at a fresh temporary file, with one account to file books under."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_FILE", str(db_file))
    database.init_db()
    assert database.add_account("Test (UK)", "uk", auth={"locale_code": "uk"}) == ACCOUNT
    return db_file


def _id(asin: str) -> int:
    """The row id of a book, which is what the state-machine functions take."""
    return database.get_book_by_asin(asin).id


def test_init_db_creates_library_table_with_all_columns(db):
    conn = sqlite3.connect(db)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(library)")]
    conn.close()
    assert columns[:3] == ["id", "account_id", "asin"]
    # By name, not position: nothing indexes a row positionally any more.
    assert set(columns) == {
        "id",
        "account_id",
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
        "monitored",
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
    assert database.update_books(ACCOUNT, [make_book("B001"), make_book("B002")]) == 2
    assert database.update_books(ACCOUNT, [make_book("B001"), make_book("B003")]) == 1

    books = database.get_books()
    assert {book.asin for book in books} == {"B001", "B002", "B003"}
    assert all(book.status == "waiting_download" for book in books)


def test_update_books_serialises_lists_as_json(db):
    """The columns really hold JSON text, which `Book.from_row` hides from the reader."""
    database.update_books(
        ACCOUNT, [make_book(authors=["A", "B"], series=[{"title": "S", "sequence": "1"}], has_pdf=True)]
    )

    conn = sqlite3.connect(database.DB_FILE)
    row = conn.execute("SELECT authors, series, has_pdf FROM library WHERE asin='B001'").fetchone()
    conn.close()

    assert row[0] == '["A", "B"]'
    assert row[1] == '[{"title": "S", "sequence": "1"}]'
    assert row[2] == 1


def test_get_book_by_asin_decodes_json_columns(db):
    database.update_books(
        ACCOUNT, [make_book(authors=["A", "B"], series=[{"title": "S", "sequence": "1"}], has_pdf=True)]
    )

    book = database.get_book_by_asin("B001")

    assert book.authors == ["A", "B"]
    assert book.series == [{"title": "S", "sequence": "1"}]
    assert book.has_pdf is True


def test_get_books_orders_newest_first_and_respects_limit(db):
    database.update_books(
        ACCOUNT,
        [
            make_book("OLD", date_added="2020-01-01"),
            make_book("NEW", date_added="2024-01-01"),
            make_book("MID", date_added="2022-01-01"),
        ],
    )
    assert [book.asin for book in database.get_books()] == ["NEW", "MID", "OLD"]
    assert [book.asin for book in database.get_books(limit=1)] == ["NEW"]


def test_mark_book_downloaded_removes_from_waiting(db):
    database.update_books(ACCOUNT, [make_book("B001"), make_book("B002")])
    database.mark_book_downloaded(_id("B001"))

    waiting = [book.asin for book in database.get_books_to_download()]
    assert waiting == ["B002"]
    assert database.get_book_by_asin("B001").status == "downloaded"


def test_mark_book_downloaded_records_format_and_timestamp(db, monkeypatch):
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-01-02T03:04:05+00:00")
    database.update_books(ACCOUNT, [make_book("B001")])
    database.mark_book_downloaded(_id("B001"), encoding_format="oga")

    book = database.get_book_by_asin("B001")
    assert book.encoding_format == "oga"
    assert book.downloaded_at == "2026-01-02T03:04:05+00:00"


def test_mark_book_downloaded_default_timestamp_is_utc_iso(db):
    database.update_books(ACCOUNT, [make_book("B001")])
    before = datetime.now(UTC).replace(microsecond=0)
    database.mark_book_downloaded(_id("B001"))

    book = database.get_book_by_asin("B001")
    assert book.encoding_format is None
    stamp = datetime.fromisoformat(book.downloaded_at)
    assert stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0
    assert before <= stamp <= datetime.now(UTC)


def test_update_book_accessories_only_sets_given_paths(db):
    database.update_books(ACCOUNT, [make_book("B001")])
    database.update_book_accessories(_id("B001"), pdf_path="/p.pdf", cover_path="/c.jpg")
    book = database.get_book_by_asin("B001")
    assert book.pdf_path == "/p.pdf"
    assert book.cover_path == "/c.jpg"
    assert book.annotations_path is None

    database.update_book_accessories(_id("B001"), annotations_path="/a.json")
    book = database.get_book_by_asin("B001")
    assert book.pdf_path == "/p.pdf"
    assert book.annotations_path == "/a.json"


def test_update_books_handles_a_duplicate_asin_within_one_batch(db):
    """A repeated ASIN in one API response used to abort the sync with nothing committed."""
    inserted = database.update_books(ACCOUNT, [make_book("B001"), make_book("B001"), make_book("B002")])

    assert inserted == 2
    assert sorted(book.asin for book in database.get_books()) == ["B001", "B002"]


def test_update_books_on_an_empty_list_inserts_nothing(db):
    assert database.update_books(ACCOUNT, []) == 0
    assert database.get_books() == []


def test_latest_date_added_is_none_for_an_empty_library(db):
    assert database.latest_date_added(ACCOUNT) is None


def test_latest_date_added_returns_the_newest_regardless_of_insert_order(db):
    database.update_books(
        ACCOUNT,
        [
            make_book("MID", date_added="2024-06-01T00:00:00Z"),
            make_book("NEW", date_added="2024-12-01T00:00:00Z"),
            make_book("OLD", date_added="2024-01-01T00:00:00Z"),
        ],
    )

    assert database.latest_date_added(ACCOUNT) == "2024-12-01T00:00:00Z"


def test_mark_book_downloaded_records_accessory_paths_in_one_statement(db):
    database.update_books(ACCOUNT, [make_book("B001")])

    database.mark_book_downloaded(
        _id("B001"),
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
    database.update_books(ACCOUNT, [make_book("B001")])
    database.mark_book_downloaded(_id("B001"), encoding_format="m4b", pdf_path="/lib/book.pdf")

    database.mark_book_downloaded(_id("B001"), encoding_format="oga")

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
    database.update_books(ACCOUNT, [make_book("B001")])
    assert all(isinstance(book, Book) for book in database.get_books())


def test_get_book_by_asin_round_trips_a_book(db):
    """
    Proves the `json.dumps` in `update_books` and `Book.from_row`'s decode are symmetric.

    The whole-dataclass equality is also load-bearing on `attempts`: the field default
    and the column default have to agree or a freshly inserted book stops matching.
    """
    original = make_book(authors=["A", "B"], series=[{"title": "S", "sequence": "1"}], has_pdf=True)
    database.update_books(ACCOUNT, [original])

    stored = database.get_book_by_asin("B001")
    # The row id is the database's to assign; everything else must come back as it went in
    assert stored == replace(original, id=stored.id, status="waiting_download")


def test_get_book_by_asin_on_a_legacy_migrated_database(tmp_path, monkeypatch):
    """
    A database that predates most of the schema still reads back.

    The rebuild into the per-account shape gives such a table every column, so the
    reads that used to raise on it for want of `date_added` now work too.
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
    assert book.monitored is True
    assert [b.asin for b in database.get_books()] == ["B001"]


def _claimable(asin: str = "B001") -> None:
    """Put one book in the library, waiting to be downloaded."""
    database.update_books(ACCOUNT, [make_book(asin)])


def test_claim_book_for_download_takes_the_book_and_counts_the_attempt(db):
    _claimable()

    assert database.claim_book_for_download(_id("B001")) is True

    book = database.get_book_by_asin("B001")
    assert book.status is BookStatus.DOWNLOADING
    assert book.attempts == 1
    assert book.last_attempt_at is not None


def test_claim_book_for_download_refuses_a_book_another_run_already_holds(db):
    """The second caller matches no rows, so two runs can never work on one book."""
    _claimable()
    assert database.claim_book_for_download(_id("B001")) is True

    assert database.claim_book_for_download(_id("B001")) is False
    assert database.get_book_by_asin("B001").attempts == 1


def test_claim_book_for_download_takes_a_book_out_of_the_queue(db):
    database.update_books(ACCOUNT, [make_book("B001"), make_book("B002")])
    database.claim_book_for_download(_id("B001"))

    assert [book.asin for book in database.get_books_to_download()] == ["B002"]


def _set(asin: str, **columns) -> None:
    """Force one row's state, for conditions the public API cannot reach."""
    assignments = ", ".join(f"{name} = ?" for name in columns)
    conn = sqlite3.connect(database.DB_FILE)
    conn.execute(f"UPDATE library SET {assignments} WHERE asin = ?", (*columns.values(), asin))
    conn.commit()
    conn.close()


def test_download_queue_offers_a_download_abandoned_by_a_dead_process(db):
    """
    The queue is what makes `STALE_CLAIM_SECONDS` reachable.

    `claim_book_for_download` could always reclaim an abandoned row, but while the queue
    selected `waiting_download` alone nothing ever offered it one, so a book a killed run
    had claimed stayed `downloading` forever.
    """
    _claimable()
    database.claim_book_for_download(_id("B001"))
    assert database.get_books_to_download() == []

    _set("B001", last_attempt_at="2020-01-01T00:00:00+00:00")

    assert [book.asin for book in database.get_books_to_download()] == ["B001"]


def test_download_queue_offers_a_downloading_row_with_no_timestamp(db):
    """A NULL timestamp counts as stale here for the same reason it does in the claim."""
    _claimable()
    _set("B001", status="downloading", last_attempt_at=None)

    assert [book.asin for book in database.get_books_to_download()] == ["B001"]


def test_download_queue_agrees_with_the_claim_about_what_is_stale(db):
    """The two must use one definition, or the queue offers a book the claim refuses."""
    _claimable()
    database.claim_book_for_download(_id("B001"))
    _set("B001", last_attempt_at="2020-01-01T00:00:00+00:00")

    offered = database.get_books_to_download()

    assert [book.asin for book in offered] == ["B001"]
    assert database.claim_book_for_download(_id("B001")) is True
    assert database.get_book_by_asin("B001").attempts == 2


@pytest.mark.parametrize("status", ["downloaded", "failed", "unavailable"])
def test_download_queue_never_offers_a_book_that_is_not_pending(db, status):
    """A stale timestamp must not drag a finished or given-up book back in."""
    _claimable()
    _set("B001", status=status, last_attempt_at="2020-01-01T00:00:00+00:00")

    assert database.get_books_to_download() == []


def test_download_queue_orders_a_reclaim_with_everything_else(db):
    """One ordering across both statuses, or a reclaim would jump the queue."""
    database.update_books(
        ACCOUNT,
        [
            make_book("B001", date_added="2024-01-03T00:00:00Z"),
            make_book("B002", date_added="2024-01-01T00:00:00Z"),
            make_book("B003", date_added="2024-01-02T00:00:00Z"),
        ],
    )
    _set("B003", status="downloading", last_attempt_at="2020-01-01T00:00:00+00:00")

    assert [book.asin for book in database.get_books_to_download()] == ["B002", "B003", "B001"]


def test_claim_book_for_download_reclaims_a_download_abandoned_long_enough_ago(db):
    """A process killed mid-download must not strand its book forever."""
    _claimable()
    database.claim_book_for_download(_id("B001"))

    conn = sqlite3.connect(database.DB_FILE)
    conn.execute("UPDATE library SET last_attempt_at = '2020-01-01T00:00:00+00:00' WHERE asin = 'B001'")
    conn.commit()
    conn.close()

    assert database.claim_book_for_download(_id("B001")) is True
    assert database.get_book_by_asin("B001").attempts == 2


def test_claim_book_for_download_reclaims_a_downloading_row_with_no_timestamp(db):
    """A hand-edited row with no `last_attempt_at` would otherwise never be picked up again."""
    _claimable()
    conn = sqlite3.connect(database.DB_FILE)
    conn.execute("UPDATE library SET status = 'downloading', last_attempt_at = NULL WHERE asin = 'B001'")
    conn.commit()
    conn.close()

    assert database.claim_book_for_download(_id("B001")) is True


def test_claim_book_for_download_does_not_reclaim_a_download_still_running(db):
    _claimable()
    database.claim_book_for_download(_id("B001"))

    assert database.claim_book_for_download(_id("B001"), stale_after=3600) is False


@pytest.mark.parametrize("status", [BookStatus.DOWNLOADED, BookStatus.FAILED])
def test_claim_book_for_download_ignores_a_finished_or_failed_book(db, status):
    _claimable()
    conn = sqlite3.connect(database.DB_FILE)
    conn.execute("UPDATE library SET status = ? WHERE asin = 'B001'", (status,))
    conn.commit()
    conn.close()

    assert database.claim_book_for_download(_id("B001")) is False


def test_claim_book_for_download_returns_false_for_an_unknown_asin(db):
    assert database.claim_book_for_download(999) is False


def test_mark_book_failed_returns_a_retryable_book_to_the_queue(db):
    _claimable()
    database.claim_book_for_download(_id("B001"))

    status = database.mark_book_failed(_id("B001"), "HTTPError: 503", max_attempts=3)

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
        database.claim_book_for_download(_id("B001"))
        outcomes.append(database.mark_book_failed(_id("B001"), "boom", max_attempts=3))

    assert outcomes == [BookStatus.WAITING_DOWNLOAD, BookStatus.WAITING_DOWNLOAD, BookStatus.FAILED]
    assert database.get_book_by_asin("B001").attempts == 3
    assert database.get_books_to_download() == []


def test_mark_book_failed_terminal_gives_up_on_the_first_attempt(db):
    """A licence Audible refuses will not be granted on the third ask."""
    _claimable()
    database.claim_book_for_download(_id("B001"))

    status = database.mark_book_failed(_id("B001"), "LicenseError: Denied", max_attempts=3, terminal=True)

    assert status is BookStatus.FAILED
    assert database.get_book_by_asin("B001").attempts == 1
    assert database.get_books_to_download() == []


def test_mark_book_failed_returns_none_for_an_unknown_asin(db):
    assert database.mark_book_failed(999, "boom", max_attempts=3) is None


def test_release_book_hands_the_claim_back_without_burning_an_attempt(db):
    """Expired credentials are not this book's fault, so the attempt is given back."""
    _claimable()
    database.claim_book_for_download(_id("B001"))

    database.release_book(_id("B001"))

    book = database.get_book_by_asin("B001")
    assert book.status is BookStatus.WAITING_DOWNLOAD
    assert book.attempts == 0
    assert [b.asin for b in database.get_books_to_download()] == ["B001"]


def test_release_book_leaves_a_book_it_does_not_hold_alone(db):
    _claimable()
    database.mark_book_downloaded(_id("B001"), encoding_format="m4b")

    database.release_book(_id("B001"))

    assert database.get_book_by_asin("B001").status is BookStatus.DOWNLOADED


def test_get_books_to_download_skips_downloading_and_failed_books(db):
    database.update_books(ACCOUNT, [make_book("WAIT"), make_book("BUSY"), make_book("DEAD")])
    database.claim_book_for_download(_id("BUSY"))
    database.claim_book_for_download(_id("DEAD"))
    database.mark_book_failed(_id("DEAD"), "boom", max_attempts=1)

    assert [book.asin for book in database.get_books_to_download()] == ["WAIT"]


def test_get_book_by_asin_returns_the_status_as_an_enum(db):
    _claimable()
    assert database.get_book_by_asin("B001").status is BookStatus.WAITING_DOWNLOAD


def test_mark_book_downloaded_clears_a_stale_error(db):
    """A book that succeeded on its second attempt must not keep showing the first failure."""
    _claimable()
    database.claim_book_for_download(_id("B001"))
    database.mark_book_failed(_id("B001"), "HTTPError: 503", max_attempts=3)
    database.claim_book_for_download(_id("B001"))

    database.mark_book_downloaded(_id("B001"), encoding_format="m4b")

    book = database.get_book_by_asin("B001")
    assert book.last_error is None
    assert book.attempts == 2


def test_update_books_refreshes_the_mutable_api_fields_on_a_second_sync(db):
    """`is_finished` and `percent_complete` used to be frozen at first sight of the book."""
    database.update_books(ACCOUNT, [make_book("B001", "Old Title")])

    added = database.update_books(
        ACCOUNT, [make_book("B001", "New Title", authors=["Ann"], is_finished=True, percent_complete=100.0)]
    )

    assert added == 0
    book = database.get_book_by_asin("B001")
    assert book.title == "New Title"
    assert book.authors == ["Ann"]
    assert book.is_finished is True
    assert book.percent_complete == 100.0


def test_update_books_never_touches_the_download_state(db):
    """Everything the downloader owns survives a re-sync untouched."""
    database.update_books(ACCOUNT, [make_book("B001")])
    database.claim_book_for_download(_id("B001"))
    database.mark_book_downloaded(
        _id("B001"),
        encoding_format="oga",
        pdf_path="/lib/book.pdf",
        cover_path="/lib/book_cover.jpg",
        annotations_path="/lib/book_annotations.json",
    )
    before = database.get_book_by_asin("B001")

    database.update_books(ACCOUNT, [make_book("B001", "Renamed", is_finished=True)])

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
    database.update_books(ACCOUNT, [make_book("B001", date_added="2024-01-01T00:00:00Z")])

    database.update_books(ACCOUNT, [make_book("B001", date_added="2025-01-01T00:00:00Z")])

    assert database.get_book_by_asin("B001").date_added == "2024-01-01T00:00:00Z"
    assert database.latest_date_added(ACCOUNT) == "2024-01-01T00:00:00Z"


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
    database.update_books(ACCOUNT, [make_book("GONE", is_consumable=False)])

    book = database.get_book_by_asin("GONE")
    assert book.status is BookStatus.UNAVAILABLE
    assert book.is_consumable is False
    assert database.get_books_to_download() == []


def test_update_books_returns_a_restored_book_to_the_queue(db):
    """Audible offers withdrawn Plus titles again; the next sync must pick that up on its own."""
    database.update_books(ACCOUNT, [make_book("BACK", is_consumable=False)])
    assert database.get_books_to_download() == []

    database.update_books(ACCOUNT, [make_book("BACK", is_consumable=True)])

    book = database.get_book_by_asin("BACK")
    assert book.status is BookStatus.WAITING_DOWNLOAD
    assert book.is_consumable is True
    assert [b.asin for b in database.get_books_to_download()] == ["BACK"]


def test_update_books_parks_a_waiting_book_that_has_been_withdrawn(db):
    database.update_books(ACCOUNT, [make_book("B001")])

    database.update_books(ACCOUNT, [make_book("B001", is_consumable=False)])

    assert database.get_book_by_asin("B001").status is BookStatus.UNAVAILABLE


@pytest.mark.parametrize("status", [BookStatus.DOWNLOADED, BookStatus.DOWNLOADING, BookStatus.FAILED])
def test_update_books_leaves_every_other_status_alone_when_rights_change(db, status):
    """Withdrawal only moves a book between the queue and `unavailable`, never out of these."""
    database.update_books(ACCOUNT, [make_book("B001")])
    conn = sqlite3.connect(database.DB_FILE)
    conn.execute("UPDATE library SET status = ? WHERE asin = 'B001'", (status,))
    conn.commit()
    conn.close()

    database.update_books(ACCOUNT, [make_book("B001", is_consumable=False)])

    book = database.get_book_by_asin("B001")
    assert book.status is status
    # ...but the flag itself still refreshes, so a UI can say why
    assert book.is_consumable is False


def test_mark_book_unavailable_parks_a_book_without_failing_it(db):
    database.update_books(ACCOUNT, [make_book("B001")])
    database.claim_book_for_download(_id("B001"))

    database.mark_book_unavailable(_id("B001"), "Audible did not grant a license (status Denied)")

    book = database.get_book_by_asin("B001")
    assert book.status is BookStatus.UNAVAILABLE
    assert book.is_consumable is False
    assert "Denied" in book.last_error
    # The attempt really happened, but nothing terminal is decided from it here
    assert book.attempts == 1
    assert database.get_books_to_download() == []


def test_a_parked_book_comes_back_on_the_next_sync(db):
    """The full round trip: denied at download time, then restored by a later sync."""
    database.update_books(ACCOUNT, [make_book("B001")])
    database.claim_book_for_download(_id("B001"))
    database.mark_book_unavailable(_id("B001"), "Denied")

    database.update_books(ACCOUNT, [make_book("B001", is_consumable=True)])

    assert database.get_book_by_asin("B001").status is BookStatus.WAITING_DOWNLOAD
    assert [b.asin for b in database.get_books_to_download()] == ["B001"]


def test_needs_consumability_refresh_is_false_for_a_fully_read_library(db):
    database.update_books(ACCOUNT, [make_book("B001"), make_book("B002")])

    assert database.needs_consumability_refresh(ACCOUNT) is False


def test_needs_consumability_refresh_spots_a_parked_book(db):
    """A parked title is the case the incremental sync can never re-read on its own."""
    database.update_books(ACCOUNT, [make_book("B001"), make_book("GONE", is_consumable=False)])

    assert database.needs_consumability_refresh(ACCOUNT) is True


def test_needs_consumability_refresh_spots_a_row_that_predates_the_column(db):
    """After upgrading, availability has never been read for the existing library."""
    database.update_books(ACCOUNT, [make_book("B001")])
    conn = sqlite3.connect(database.DB_FILE)
    conn.execute("UPDATE library SET is_consumable = NULL")
    conn.commit()
    conn.close()

    assert database.needs_consumability_refresh(ACCOUNT) is True


def test_needs_consumability_refresh_is_false_for_an_empty_library(db):
    assert database.needs_consumability_refresh(ACCOUNT) is False


# --- sync_runs -------------------------------------------------------------------


def test_init_db_enables_wal(db):
    """Two processes share this database, so a reader must not block the writer."""
    conn = sqlite3.connect(db)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_init_db_creates_sync_runs_table_with_all_columns(db):
    conn = sqlite3.connect(db)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(sync_runs)")]
    conn.close()
    assert set(columns) == {
        "id",
        "account_id",
        "started_at",
        "finished_at",
        "outcome",
        "books_seen",
        "books_added",
        "books_downloaded",
        "books_failed",
        "error",
    }


def test_start_sync_run_opens_a_running_row(db, monkeypatch):
    """Written before Audible is touched, so a killed run leaves this row behind."""
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-01-02T03:04:05+00:00")

    run_id = database.start_sync_run()

    run = database.get_sync_runs()[0]
    assert run.id == run_id
    assert run.started_at == "2026-01-02T03:04:05+00:00"
    assert run.outcome is SyncOutcome.RUNNING
    assert run.finished_at is None


def test_finish_sync_run_records_the_outcome_and_counters(db, monkeypatch):
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-01-02T03:04:05+00:00")
    run_id = database.start_sync_run()
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-01-02T04:00:00+00:00")

    database.finish_sync_run(
        run_id,
        outcome=SyncOutcome.PARTIAL,
        books_seen=9,
        books_added=4,
        books_downloaded=3,
        books_failed=1,
        error="RuntimeError: disk full",
    )

    run = database.get_sync_runs()[0]
    assert run == SyncRun(
        id=run_id,
        started_at="2026-01-02T03:04:05+00:00",
        finished_at="2026-01-02T04:00:00+00:00",
        outcome=SyncOutcome.PARTIAL,
        books_seen=9,
        books_added=4,
        books_downloaded=3,
        books_failed=1,
        error="RuntimeError: disk full",
    )


def test_latest_successful_sync_start_is_none_on_an_empty_table(db):
    assert database.latest_successful_sync_start() is None


@pytest.mark.parametrize("outcome", [SyncOutcome.SUCCESS, SyncOutcome.PARTIAL])
def test_latest_successful_sync_start_counts_a_run_whose_sync_completed(db, monkeypatch, outcome):
    """`partial` counts too: its sync read the library through, only a download failed."""
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-01-02T03:04:05+00:00")
    database.finish_sync_run(database.start_sync_run(), outcome=outcome)

    assert database.latest_successful_sync_start() == "2026-01-02T03:04:05+00:00"


@pytest.mark.parametrize("outcome", [SyncOutcome.RUNNING, SyncOutcome.FAILED])
def test_latest_successful_sync_start_ignores_a_run_that_never_read_the_library(db, monkeypatch, outcome):
    """A killed or failed run must not advance the cursor past books it never saw."""
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-01-02T03:04:05+00:00")
    run_id = database.start_sync_run()
    if outcome is not SyncOutcome.RUNNING:
        database.finish_sync_run(run_id, outcome=outcome)

    assert database.latest_successful_sync_start() is None


def test_latest_successful_sync_start_takes_the_newest(db, monkeypatch):
    for started in ("2026-01-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"):
        monkeypatch.setattr(database, "_utcnow", lambda started=started: started)
        database.finish_sync_run(database.start_sync_run(), outcome=SyncOutcome.SUCCESS)

    assert database.latest_successful_sync_start() == "2026-03-01T00:00:00+00:00"


def test_get_sync_runs_is_newest_first_and_honours_the_limit(db, monkeypatch):
    for started in ("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00"):
        monkeypatch.setattr(database, "_utcnow", lambda started=started: started)
        database.start_sync_run()

    runs = database.get_sync_runs(limit=2)

    assert [run.started_at for run in runs] == ["2026-03-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"]


# --- settings ---------------------------------------------------------------------


def test_init_db_creates_the_settings_table(db):
    conn = sqlite3.connect(db)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(settings)")]
    conn.close()

    assert columns == ["key", "value", "updated_at"]


def test_get_settings_is_empty_on_a_fresh_database(db):
    assert database.get_settings() == {}
    assert database.has_settings() is False


def test_save_settings_stores_text_and_stamps_when(db, monkeypatch):
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-09-12T10:00:00+00:00")

    database.save_settings({"bitrate": "48", "debug": "true"})

    assert database.get_settings() == {"bitrate": "48", "debug": "true"}
    assert database.has_settings() is True
    conn = sqlite3.connect(db)
    stamps = {row[0] for row in conn.execute("SELECT updated_at FROM settings")}
    conn.close()
    assert stamps == {"2026-09-12T10:00:00+00:00"}


def test_save_settings_replaces_a_key_and_leaves_the_others_alone(db, monkeypatch):
    """A partial update from the API must not wipe the settings it did not mention."""
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-09-12T10:00:00+00:00")
    database.save_settings({"bitrate": "48", "debug": "true"})
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-09-12T11:00:00+00:00")

    database.save_settings({"bitrate": "32"})

    assert database.get_settings() == {"bitrate": "32", "debug": "true"}
    conn = sqlite3.connect(db)
    stamps = dict(conn.execute("SELECT key, updated_at FROM settings"))
    conn.close()
    assert stamps == {"bitrate": "2026-09-12T11:00:00+00:00", "debug": "2026-09-12T10:00:00+00:00"}


def test_save_settings_with_nothing_to_write_touches_nothing(db):
    database.save_settings({})

    assert database.has_settings() is False


# --- run history ----------------------------------------------------------------


def _finished_run(outcome, started_at, monkeypatch):
    monkeypatch.setattr(database, "_utcnow", lambda: started_at)
    run_id = database.start_sync_run()
    database.finish_sync_run(run_id, outcome=outcome)
    return run_id


def test_get_sync_run_returns_the_row_or_none(db, monkeypatch):
    run_id = _finished_run(SyncOutcome.SUCCESS, "2026-09-12T10:00:00+00:00", monkeypatch)

    run = database.get_sync_run(run_id)

    assert isinstance(run, SyncRun)
    assert (run.id, run.outcome) == (run_id, SyncOutcome.SUCCESS)
    assert database.get_sync_run(run_id + 1) is None


def test_get_sync_runs_pages_newest_first(db, monkeypatch):
    ids = [_finished_run(SyncOutcome.SUCCESS, f"2026-09-1{d}T10:00:00+00:00", monkeypatch) for d in (1, 2, 3)]

    assert [r.id for r in database.get_sync_runs(limit=2)] == [ids[2], ids[1]]
    assert [r.id for r in database.get_sync_runs(limit=2, offset=2)] == [ids[0]]
    assert database.count_sync_runs() == 3


def test_count_sync_runs_is_zero_on_a_fresh_database(db):
    assert database.count_sync_runs() == 0


def test_latest_sync_run_start_counts_every_run_whatever_became_of_it(db, monkeypatch):
    """The scheduler paces from the last attempt, so a failing setup waits the full
    interval between tries rather than retrying every tick."""
    assert database.latest_sync_run_start() is None
    _finished_run(SyncOutcome.SUCCESS, "2026-09-11T10:00:00+00:00", monkeypatch)
    _finished_run(SyncOutcome.FAILED, "2026-09-12T10:00:00+00:00", monkeypatch)
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-09-13T10:00:00+00:00")
    database.start_sync_run()  # still running

    assert database.latest_sync_run_start() == "2026-09-13T10:00:00+00:00"


def test_a_cancelled_run_is_a_cursor(db, monkeypatch):
    """A cancel is only honoured after the library sync, so the library was read through."""
    _finished_run(SyncOutcome.SUCCESS, "2026-09-11T10:00:00+00:00", monkeypatch)
    _finished_run(SyncOutcome.CANCELLED, "2026-09-12T10:00:00+00:00", monkeypatch)
    _finished_run(SyncOutcome.FAILED, "2026-09-13T10:00:00+00:00", monkeypatch)

    assert database.latest_successful_sync_start() == "2026-09-12T10:00:00+00:00"


# --- accounts -----------------------------------------------------------------------


def test_add_and_get_account(db, monkeypatch):
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-09-12T10:00:00+00:00")

    account_id = database.add_account(
        "Alex (US)", "us", auth={"locale_code": "us", "access_token": "t"}, customer_name="Alex", monitor_existing=False
    )
    account = database.get_account(account_id)

    assert isinstance(account, Account)
    assert account == Account(
        id=account_id,
        name="Alex (US)",
        country_code="us",
        customer_name="Alex",
        auth={"locale_code": "us", "access_token": "t"},
        enabled=True,
        monitor_existing=False,
        created_at="2026-09-12T10:00:00+00:00",
        last_synced_at=None,
    )
    assert account.needs_login is False
    assert database.get_account(999) is None


def test_get_accounts_is_oldest_first(db):
    second = database.add_account("Two", "us", auth=None)

    assert [a.id for a in database.get_accounts()] == [ACCOUNT, second]


def test_an_account_without_credentials_needs_a_login(db):
    account_id = database.add_account("Pending", "us", auth=None)

    assert database.get_account(account_id).needs_login is True


def test_account_repr_never_shows_the_credentials(db):
    account = database.get_account(ACCOUNT)

    assert "locale_code" not in repr(account)
    assert "needs_login=False" in repr(account)


def test_save_account_auth_stores_and_clears(db):
    database.save_account_auth(ACCOUNT, {"locale_code": "uk", "access_token": "new"})
    assert database.get_account(ACCOUNT).auth == {"locale_code": "uk", "access_token": "new"}

    database.save_account_auth(ACCOUNT, None)
    assert database.get_account(ACCOUNT).needs_login is True


def test_update_account_changes_only_what_it_is_given(db):
    database.update_account(ACCOUNT, name="Renamed", enabled=False)

    account = database.get_account(ACCOUNT)
    assert (account.name, account.enabled, account.country_code) == ("Renamed", False, "uk")

    database.update_account(ACCOUNT)  # nothing to do
    assert database.get_account(ACCOUNT).name == "Renamed"


def test_mark_account_synced(db, monkeypatch):
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-09-12T10:00:00+00:00")

    database.mark_account_synced(ACCOUNT)

    assert database.get_account(ACCOUNT).last_synced_at == "2026-09-12T10:00:00+00:00"


def test_delete_account_takes_its_books_and_runs_with_it(db):
    other = database.add_account("Other", "us", auth=None)
    database.update_books(ACCOUNT, [make_book("B001")])
    database.update_books(other, [make_book("B001"), make_book("B002")])
    run_mine = database.start_sync_run(ACCOUNT)
    run_other = database.start_sync_run(other)

    database.delete_account(other)

    assert database.get_account(other) is None
    assert [(b.account_id, b.asin) for b in database.get_books()] == [(ACCOUNT, "B001")]
    assert database.get_sync_run(run_other) is None
    assert database.get_sync_run(run_mine) is not None


# --- books per account ---------------------------------------------------------------


def test_the_same_asin_can_be_in_two_accounts(db):
    """A person with two marketplaces can own one title in both; the identity is the pair."""
    other = database.add_account("Other", "us", auth=None)

    database.update_books(ACCOUNT, [make_book("B001", "Mine")])
    database.update_books(other, [make_book("B001", "Theirs")])

    mine = database.get_book_by_asin("B001", account_id=ACCOUNT)
    theirs = database.get_book_by_asin("B001", account_id=other)
    assert (mine.title, theirs.title) == ("Mine", "Theirs")
    assert mine.id != theirs.id
    assert database.get_book(theirs.id).title == "Theirs"
    assert database.get_book(999) is None


def test_get_books_can_be_limited_to_one_account(db):
    other = database.add_account("Other", "us", auth=None)
    database.update_books(ACCOUNT, [make_book("B001")])
    database.update_books(other, [make_book("B002")])

    assert [b.asin for b in database.get_books(account_id=other)] == ["B002"]
    assert {b.asin for b in database.get_books()} == {"B001", "B002"}


def test_the_download_queue_can_be_limited_to_one_account(db):
    other = database.add_account("Other", "us", auth=None)
    database.update_books(ACCOUNT, [make_book("B001")])
    database.update_books(other, [make_book("B002")])

    assert [b.asin for b in database.get_books_to_download(account_id=other)] == ["B002"]
    assert [b.asin for b in database.get_books_to_download()] == ["B001", "B002"]


def test_the_cursor_reads_are_per_account(db, monkeypatch):
    other = database.add_account("Other", "us", auth=None)
    database.update_books(ACCOUNT, [make_book("B001", date_added="2024-01-01T00:00:00Z")])
    database.update_books(other, [make_book("B002", date_added="2025-01-01T00:00:00Z", is_consumable=False)])
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-09-12T10:00:00+00:00")
    database.finish_sync_run(database.start_sync_run(other), outcome=SyncOutcome.SUCCESS)

    assert database.latest_date_added(ACCOUNT) == "2024-01-01T00:00:00Z"
    assert database.latest_date_added(other) == "2025-01-01T00:00:00Z"
    assert database.needs_consumability_refresh(ACCOUNT) is False
    assert database.needs_consumability_refresh(other) is True
    assert database.latest_successful_sync_start(ACCOUNT) is None
    assert database.latest_successful_sync_start(other) == "2026-09-12T10:00:00+00:00"


def test_a_run_from_before_accounts_is_a_cursor_for_the_migrated_account(db, monkeypatch):
    """Rows with no account were that library's runs; forgetting them would re-fetch everything."""
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-09-12T10:00:00+00:00")
    database.finish_sync_run(database.start_sync_run(), outcome=SyncOutcome.SUCCESS)

    assert database.latest_successful_sync_start(ACCOUNT) == "2026-09-12T10:00:00+00:00"


def test_sync_runs_record_their_account(db):
    other = database.add_account("Other", "us", auth=None)
    mine = database.start_sync_run(ACCOUNT)
    theirs = database.start_sync_run(other)

    assert database.get_sync_run(mine).account_id == ACCOUNT
    assert [r.id for r in database.get_sync_runs(account_id=other)] == [theirs]
    assert database.count_sync_runs(account_id=other) == 1
    assert database.count_sync_runs() == 2


# --- monitored ------------------------------------------------------------------------


def test_update_books_inserts_unmonitored_when_told_to(db):
    database.update_books(ACCOUNT, [make_book("B001")], monitor_new=False)

    assert database.get_book_by_asin("B001").monitored is False
    assert database.get_books_to_download() == []


def test_update_books_never_changes_whether_an_existing_book_is_wanted(db):
    """Wanted or not is the user's decision; a later sync must not overturn it either way."""
    database.update_books(ACCOUNT, [make_book("B001")], monitor_new=False)
    database.update_books(ACCOUNT, [make_book("B002")], monitor_new=True)

    database.update_books(ACCOUNT, [make_book("B001"), make_book("B002")], monitor_new=True)
    assert database.get_book_by_asin("B001").monitored is False
    database.update_books(ACCOUNT, [make_book("B001"), make_book("B002")], monitor_new=False)
    assert database.get_book_by_asin("B002").monitored is True


def test_the_download_queue_skips_an_unmonitored_book_whatever_its_status(db):
    database.update_books(ACCOUNT, [make_book("B001"), make_book("B002")])
    _set("B001", monitored=0)
    _set("B002", monitored=0, status=BookStatus.DOWNLOADING, last_attempt_at=None)

    assert database.get_books_to_download() == []


# --- migration to accounts -------------------------------------------------------------


def _pre_accounts_database(tmp_path):
    """A Milestone 2 database: `library` keyed by asin alone, `sync_runs` without an account."""
    db_file = tmp_path / "old.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        """
        CREATE TABLE library (
            asin TEXT PRIMARY KEY, title TEXT, authors JSON, date_added TEXT, status TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, is_consumable BOOLEAN
        )
        """
    )
    conn.execute("CREATE INDEX idx_library_status_date_added ON library(status, date_added)")
    conn.executemany(
        "INSERT INTO library (asin, title, authors, date_added, status, attempts) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("B001", "One", '["A"]', "2024-01-01T00:00:00Z", "downloaded", 1),
            ("B002", "Two", '["B"]', "2024-02-01T00:00:00Z", "waiting_download", 0),
        ],
    )
    conn.execute(
        "CREATE TABLE sync_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT, "
        "outcome TEXT NOT NULL, books_seen INTEGER NOT NULL DEFAULT 0, books_added INTEGER NOT NULL DEFAULT 0, "
        "books_downloaded INTEGER NOT NULL DEFAULT 0, books_failed INTEGER NOT NULL DEFAULT 0, error TEXT)"
    )
    conn.execute(
        "INSERT INTO sync_runs (started_at, finished_at, outcome) VALUES (?, ?, ?)",
        ("2026-09-01T10:00:00+00:00", "2026-09-01T10:05:00+00:00", "success"),
    )
    conn.commit()
    conn.close()
    return db_file


def test_migration_rebuilds_a_pre_accounts_library_under_one_account(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(_pre_accounts_database(tmp_path)))

    database.init_db()

    (account,) = database.get_accounts()
    assert account.name == database.LEGACY_ACCOUNT_NAME
    assert account.needs_login is True
    books = database.get_books()
    assert [(b.id, b.account_id, b.asin, b.title, b.authors, b.status, b.attempts) for b in books] == [
        (2, account.id, "B002", "Two", ["B"], "waiting_download", 0),
        (1, account.id, "B001", "One", ["A"], "downloaded", 1),
    ]
    # Every column exists on the rebuilt table, with its default
    assert all(b.monitored for b in books)
    assert database.get_books_to_download()[0].asin == "B002"
    # The old runs stay, without an account, and still serve as the cursor
    assert database.latest_successful_sync_start(account.id) == "2026-09-01T10:00:00+00:00"
    assert database.get_sync_run(1).account_id is None


def test_migration_runs_once(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(_pre_accounts_database(tmp_path)))
    database.init_db()

    database.init_db()

    assert len(database.get_accounts()) == 1
    assert len(database.get_books()) == 2


def test_migration_recreates_the_indexes_on_the_new_table(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(_pre_accounts_database(tmp_path)))

    database.init_db()

    conn = sqlite3.connect(database.DB_FILE)
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(library)")}
    run_indexes = {row[1] for row in conn.execute("PRAGMA index_list(sync_runs)")}
    conn.close()
    assert {"idx_library_date_added", "idx_library_status_date_added"} <= indexes
    assert "idx_sync_runs_account_outcome_started_at" in run_indexes


def test_migration_enforces_one_row_per_account_and_asin(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(_pre_accounts_database(tmp_path)))
    database.init_db()
    (account,) = database.get_accounts()

    conn = sqlite3.connect(database.DB_FILE)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO library (account_id, asin) VALUES (?, 'B001')", (account.id,))
    conn.close()
