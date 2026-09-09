import logging

import src.sync as sync_module
from src.sync import sync_library
from tests.conftest import make_book


class FakeAudible:
    """Records the cursor each get_library call was made with."""

    def __init__(self, books):
        self.books = books
        self.calls = []

    def get_library(self, purchased_after=None):
        self.calls.append(purchased_after)
        return self.books


def _patch(monkeypatch, *, cursor, needs_refresh, updated):
    monkeypatch.setattr(sync_module, "latest_date_added", lambda: cursor)
    monkeypatch.setattr(sync_module, "needs_consumability_refresh", lambda: needs_refresh)
    monkeypatch.setattr(sync_module, "update_books", lambda books: updated.append(books) or len(books))


def test_sync_library_fetches_everything_on_an_empty_database(monkeypatch):
    updated = []
    _patch(monkeypatch, cursor=None, needs_refresh=False, updated=updated)
    audible = FakeAudible([make_book("B001")])

    assert sync_library(audible) == 1
    assert audible.calls == [None]


def test_sync_library_fetches_incrementally_when_the_library_is_current(monkeypatch):
    updated = []
    _patch(monkeypatch, cursor="2024-01-01T00:00:00Z", needs_refresh=False, updated=updated)
    audible = FakeAudible([make_book("B001")])

    sync_library(audible)

    # One request only: nothing is parked, so there is nothing the cursor cannot see
    assert audible.calls == ["2024-01-01T00:00:00Z"]


def test_sync_library_rereads_the_whole_library_while_a_book_is_parked(monkeypatch, caplog):
    """
    The incremental cursor can never re-read a book already in the library, so a
    withdrawn Plus title Audible has offered again would stay parked forever.
    """
    caplog.set_level(logging.INFO)
    updated = []
    _patch(monkeypatch, cursor="2024-01-01T00:00:00Z", needs_refresh=True, updated=updated)
    audible = FakeAudible([make_book("B001")])

    sync_library(audible)

    assert audible.calls == ["2024-01-01T00:00:00Z", None]
    assert "refresh availability" in caplog.text


def test_sync_library_counts_only_the_incremental_insert(monkeypatch):
    """The refresh pass must not inflate the number of new books reported."""
    updated = []
    _patch(monkeypatch, cursor="2024-01-01T00:00:00Z", needs_refresh=True, updated=updated)
    audible = FakeAudible([make_book("B001"), make_book("B002")])

    assert sync_library(audible) == 2


def test_sync_library_does_not_refresh_twice_on_a_first_full_sync(monkeypatch):
    """The first sync already read everything, so a second pass would be wasted."""
    updated = []
    _patch(monkeypatch, cursor=None, needs_refresh=True, updated=updated)
    audible = FakeAudible([make_book("B001")])

    sync_library(audible)

    assert audible.calls == [None]
