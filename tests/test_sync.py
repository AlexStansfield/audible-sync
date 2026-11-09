import logging
from datetime import UTC, datetime

import pytest
from audible.exceptions import NotFoundError, NotResponding

import audible_sync.pipeline.sync as sync_module
from audible_sync.pipeline.sync import SyncResult, _api_timestamp, sync_library
from tests.conftest import make_account, make_book

ACCOUNT = make_account()


class FakeAudible:
    """Records the cursor each get_library call was made with."""

    def __init__(self, books):
        self.books = books
        self.calls = []

    def get_library(self, purchased_after=None):
        self.calls.append(purchased_after)
        return self.books


@pytest.fixture(autouse=True)
def no_stored_podcasts(monkeypatch):
    """Nothing to tidy up unless a test says so, and so no database to read."""
    monkeypatch.setattr(sync_module, "undownloaded_books_without_length", lambda account_id: [])


def _patch(monkeypatch, *, cursor=None, run_start=None, needs_refresh=False, updated=None):
    """
    Replace everything sync_library reads.

    `run_start` is the previous run's start time, the preferred cursor source;
    `cursor` is the `MAX(date_added)` fallback used when there is no run.
    """
    updated = [] if updated is None else updated
    monkeypatch.setattr(sync_module, "latest_successful_sync_start", lambda account_id: run_start)
    monkeypatch.setattr(sync_module, "latest_date_added", lambda account_id: cursor)
    monkeypatch.setattr(sync_module, "needs_consumability_refresh", lambda account_id: needs_refresh)

    def fake_update(account_id, books, *, monitor_new=True):
        assert account_id == ACCOUNT.id
        updated.append(books)
        return len(books)

    monkeypatch.setattr(sync_module, "update_books", fake_update)
    return updated


def test_api_timestamp_uses_audibles_z_format():
    """
    Stored run timestamps carry `+00:00`; Audible's own are the `Z` form. Passing one
    through unconverted would send the API a format it never produces.
    """
    assert _api_timestamp(datetime(2026, 9, 9, 5, 16, 15, tzinfo=UTC)) == "2026-09-09T05:16:15Z"


def test_sync_library_fetches_everything_on_an_empty_database(monkeypatch):
    _patch(monkeypatch)
    audible = FakeAudible([make_book("B001")])

    assert sync_library(audible, ACCOUNT) == SyncResult(books_seen=1, books_added=1, full_refresh=True)
    assert audible.calls == [None]


def test_sync_library_fetches_incrementally_when_the_library_is_current(monkeypatch):
    _patch(monkeypatch, cursor="2024-01-01T00:00:00Z")
    audible = FakeAudible([make_book("B001")])

    sync_library(audible, ACCOUNT)

    # One request only: nothing is parked, so there is nothing the cursor cannot see
    assert audible.calls == ["2024-01-01T00:00:00Z"]


def test_sync_library_prefers_the_previous_runs_start_with_an_hours_overlap(monkeypatch):
    """
    A run's start time is the real "last synced". The hour of overlap absorbs clock
    skew: `purchased_after` is filtered on Audible's clock, so a local clock running
    even slightly fast would step straight over a purchase.
    """
    _patch(monkeypatch, cursor="2024-01-01T00:00:00Z", run_start="2026-09-09T05:16:15+00:00")
    audible = FakeAudible([make_book("B001")])

    sync_library(audible, ACCOUNT)

    assert audible.calls == ["2026-09-09T04:16:15Z"]


def test_sync_library_falls_back_to_the_library_cursor_with_no_recorded_run(monkeypatch):
    """A database that predates sync_runs must behave exactly as it did before."""
    _patch(monkeypatch, cursor="2024-01-01T00:00:00Z", run_start=None)
    audible = FakeAudible([make_book("B001")])

    sync_library(audible, ACCOUNT)

    assert audible.calls == ["2024-01-01T00:00:00Z"]


def test_sync_library_rereads_the_whole_library_while_a_book_is_parked(monkeypatch, caplog):
    """
    The incremental cursor can never re-read a book already in the library, so a
    withdrawn Plus title Audible has offered again would stay parked forever.
    """
    caplog.set_level(logging.INFO)
    _patch(monkeypatch, cursor="2024-01-01T00:00:00Z", needs_refresh=True)
    audible = FakeAudible([make_book("B001")])

    sync_library(audible, ACCOUNT)

    assert audible.calls == ["2024-01-01T00:00:00Z", None]
    assert "refresh availability" in caplog.text


def test_sync_library_reads_only_new_purchases_on_an_ordinary_run(monkeypatch):
    _patch(monkeypatch, cursor="2024-01-01T00:00:00Z")
    audible = FakeAudible([make_book("B001")])

    assert sync_library(audible, ACCOUNT).full_refresh is False


def test_a_full_refresh_reads_the_new_purchases_then_the_whole_library(monkeypatch, caplog):
    """Every stored book is given to `update_books` again, which brings its data up to date."""
    caplog.set_level(logging.INFO)
    updated = _patch(monkeypatch, cursor="2024-01-01T00:00:00Z")
    audible = FakeAudible([make_book("B001")])

    result = sync_library(audible, ACCOUNT, full=True)

    assert audible.calls == ["2024-01-01T00:00:00Z", None]
    assert len(updated) == 2
    assert result.full_refresh is True
    assert "refresh every book" in caplog.text


def test_a_full_refresh_on_a_first_sync_reads_the_library_once(monkeypatch):
    _patch(monkeypatch)
    audible = FakeAudible([make_book("B001")])

    assert sync_library(audible, ACCOUNT, full=True).full_refresh is True
    assert audible.calls == [None]


def test_sync_library_counts_only_the_incremental_fetch(monkeypatch):
    """The refresh pass re-reads everything, so folding it in would report the library size."""
    _patch(monkeypatch, cursor="2024-01-01T00:00:00Z", needs_refresh=True)
    audible = FakeAudible([make_book("B001"), make_book("B002")])

    assert sync_library(audible, ACCOUNT) == SyncResult(books_seen=2, books_added=2, full_refresh=True)


def test_sync_library_does_not_refresh_twice_on_a_first_full_sync(monkeypatch):
    """The first sync already read everything, so a second pass would be wasted."""
    _patch(monkeypatch, needs_refresh=True)
    audible = FakeAudible([make_book("B001")])

    sync_library(audible, ACCOUNT)

    assert audible.calls == [None]


def _monitor_flags(monkeypatch, *, cursor=None, needs_refresh=False):
    flags = []
    monkeypatch.setattr(sync_module, "latest_successful_sync_start", lambda account_id: None)
    monkeypatch.setattr(sync_module, "latest_date_added", lambda account_id: cursor)
    monkeypatch.setattr(sync_module, "needs_consumability_refresh", lambda account_id: needs_refresh)
    monkeypatch.setattr(
        sync_module, "update_books", lambda account_id, books, *, monitor_new: flags.append(monitor_new) or 0
    )
    return flags


def test_the_first_full_fetch_follows_the_accounts_own_choice(monkeypatch):
    """The back catalogue is queued, or not, as decided when the account was added."""
    flags = _monitor_flags(monkeypatch)

    sync_library(FakeAudible([make_book()]), make_account(monitor_existing=False), auto_monitor_new=True)

    assert flags == [False]


def test_an_incremental_fetch_follows_the_global_setting(monkeypatch):
    """Anything found after the first fetch is a new purchase."""
    flags = _monitor_flags(monkeypatch, cursor="2024-01-01T00:00:00Z", needs_refresh=True)

    sync_library(FakeAudible([make_book()]), make_account(monitor_existing=False), auto_monitor_new=True)

    # Both the incremental fetch and the availability re-read of the same run
    assert flags == [True, True]


def test_the_cursor_is_looked_up_for_the_accounts_id(monkeypatch):
    seen = []
    monkeypatch.setattr(sync_module, "latest_successful_sync_start", lambda account_id: seen.append(account_id))
    monkeypatch.setattr(sync_module, "latest_date_added", lambda account_id: seen.append(account_id))
    monkeypatch.setattr(sync_module, "update_books", lambda account_id, books, *, monitor_new: 0)

    sync_library(FakeAudible([]), make_account(id=42))

    assert seen == [42, 42]


class _PodcastAudible(FakeAudible):
    """Answers `is_podcast` per ASIN; an exception is raised instead."""

    def __init__(self, answers):
        super().__init__([])
        self.answers = answers
        self.asked = []

    def is_podcast(self, asin):
        self.asked.append(asin)
        answer = self.answers[asin]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _stored(monkeypatch, *asins):
    """`asins` are the rows with no length; returns the ids the sync removes."""
    removed = []
    books = [make_book(asin, id=index) for index, asin in enumerate(asins, start=1)]
    monkeypatch.setattr(sync_module, "undownloaded_books_without_length", lambda account_id: books)
    monkeypatch.setattr(sync_module, "delete_undownloaded_book", lambda book_id: removed.append(book_id) or True)
    return removed


def test_a_sync_removes_a_stored_podcast_and_keeps_a_book_with_no_length(monkeypatch):
    _patch(monkeypatch)
    removed = _stored(monkeypatch, "POD", "BOOK")

    sync_library(_PodcastAudible({"POD": True, "BOOK": False}), ACCOUNT)

    assert removed == [1]


@pytest.mark.parametrize(
    "problem", [NotResponding.__new__(NotResponding), AttributeError("'str' object has no attribute 'get'")]
)
def test_a_failed_check_costs_neither_the_run_nor_more_timeouts(monkeypatch, caplog, problem):
    """
    It runs before the sync counts as done, so anything getting out fails the run. And
    with Audible not answering, each further row would be another 60 s timeout.
    """
    _patch(monkeypatch)
    removed = _stored(monkeypatch, "FIRST", "POD")
    audible = _PodcastAudible({"FIRST": problem, "POD": True})

    with caplog.at_level(logging.WARNING):
        result = sync_library(audible, ACCOUNT)

    assert result == SyncResult(books_seen=0, books_added=0, full_refresh=True)
    assert audible.asked == ["FIRST"]
    assert removed == []
    assert "Could not check whether FIRST is a podcast" in caplog.text


def test_a_title_gone_from_the_library_is_passed_over_quietly(monkeypatch, caplog):
    """It would otherwise be a warning every six hours, for good."""
    _patch(monkeypatch)
    removed = _stored(monkeypatch, "GONE", "POD")
    gone = NotFoundError.__new__(NotFoundError)

    with caplog.at_level(logging.WARNING):
        sync_library(_PodcastAudible({"GONE": gone, "POD": True}), ACCOUNT)

    assert removed == [2]
    assert "GONE" not in caplog.text


def test_a_sync_asks_audible_nothing_when_every_book_has_a_length(monkeypatch):
    _patch(monkeypatch)
    audible = _PodcastAudible({})

    sync_library(audible, ACCOUNT)

    assert audible.asked == []
