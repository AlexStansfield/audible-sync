import pytest

import src.audible_client
from src.audible_client import RESPONSE_GROUPS, Audible, _prepare_book, _prepare_books


def make_item(asin="B001", **overrides):
    """A library item shaped the way the Audible API returns a complete one."""
    item = {
        "asin": asin,
        "title": "Title",
        "subtitle": "Sub",
        "authors": [{"name": "Author One"}],
        "narrators": [{"name": "Narrator One"}],
        "series": [{"title": "Series", "sequence": "1"}],
        "category_ladders": [{"ladder": [{"name": "Fiction"}]}],
        "runtime_length_min": 600,
        "is_finished": False,
        "percent_complete": 0.0,
        "release_date": "2020-01-15",
        "library_status": {"date_added": "2024-01-01T00:00:00Z"},
        "product_images": {"1215": "https://img/1215.jpg", "500": "https://img/500.jpg"},
        "pdf_url": "https://pdf",
    }
    item.update(overrides)
    return item


def test_prepare_book_maps_a_complete_item():
    book = _prepare_book(make_item())

    assert book.asin == "B001"
    assert book.authors == ["Author One"]
    assert book.narrators == ["Narrator One"]
    assert book.series == [{"title": "Series", "sequence": "1"}]
    assert book.genres == ["Fiction"]
    assert book.length == 600
    assert book.date_added == "2024-01-01T00:00:00Z"
    assert book.cover_url == "https://img/1215.jpg"
    assert book.has_pdf is True


def test_prepare_book_falls_back_to_the_smaller_cover():
    book = _prepare_book(make_item(product_images={"500": "https://img/500.jpg"}))
    assert book.cover_url == "https://img/500.jpg"


@pytest.mark.parametrize(
    "missing",
    ["subtitle", "narrators", "runtime_length_min", "release_date", "series", "library_status", "category_ladders"],
)
def test_prepare_book_tolerates_a_missing_optional_key(missing):
    """A podcast or an unnumbered series entry must not take the whole sync down."""
    item = make_item()
    del item[missing]

    book = _prepare_book(item)

    assert book.asin == "B001"


def test_prepare_book_tolerates_a_series_entry_without_a_sequence():
    book = _prepare_book(make_item(series=[{"title": "Companion"}]))
    assert book.series == [{"title": "Companion", "sequence": None}]


def test_prepare_book_tolerates_null_values():
    book = _prepare_book(make_item(series=None, product_images=None, category_ladders=None, narrators=None))

    assert book.series == []
    assert book.cover_url == ""
    assert book.genres == []
    assert book.narrators == []


def test_prepare_books_skips_only_the_unreadable_item(caplog):
    items = [make_item("GOOD1"), {"no_asin": True}, make_item("GOOD2")]

    books = _prepare_books(items)

    assert [b.asin for b in books] == ["GOOD1", "GOOD2"]
    assert "Skipping library item" in caplog.text


class FakeClient:
    """Records the params of each library request and replays canned pages."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, path, params=None, **kwargs):
        self.calls.append(params)
        return {"items": self.pages[len(self.calls) - 1]}


def make_audible(pages):
    audible = Audible.__new__(Audible)
    audible.client = FakeClient(pages)
    return audible


def test_get_library_returns_a_single_short_page():
    audible = make_audible([[make_item("B1"), make_item("B2")]])

    books = audible.get_library()

    assert [b.asin for b in books] == ["B1", "B2"]
    assert len(audible.client.calls) == 1
    assert audible.client.calls[0]["page"] == 1
    assert "purchased_after" not in audible.client.calls[0]


def test_get_library_follows_pagination_until_a_short_page(monkeypatch):
    monkeypatch.setattr("src.audible_client._PAGE_SIZE", 2)
    audible = make_audible([[make_item("B1"), make_item("B2")], [make_item("B3")]])

    books = audible.get_library()

    # A library larger than one page used to be silently truncated
    assert [b.asin for b in books] == ["B1", "B2", "B3"]
    assert [call["page"] for call in audible.client.calls] == [1, 2]


def test_get_library_stops_when_a_full_page_is_followed_by_an_empty_one(monkeypatch):
    monkeypatch.setattr("src.audible_client._PAGE_SIZE", 2)
    audible = make_audible([[make_item("B1"), make_item("B2")], []])

    books = audible.get_library()

    assert [b.asin for b in books] == ["B1", "B2"]
    assert len(audible.client.calls) == 2


def test_get_library_passes_the_incremental_cursor_on_every_page(monkeypatch):
    monkeypatch.setattr("src.audible_client._PAGE_SIZE", 1)
    audible = make_audible([[make_item("B1")], [make_item("B2")], []])

    audible.get_library("2024-01-01T00:00:00Z")

    assert all(call["purchased_after"] == "2024-01-01T00:00:00Z" for call in audible.client.calls)
    assert all(call["response_groups"] == RESPONSE_GROUPS for call in audible.client.calls)


def test_client_is_given_a_timeout_that_fits_a_full_page(monkeypatch):
    """
    The library default of 10s is shorter than a full 1000-item page takes, so a
    first sync on an empty database raised NotResponding and aborted the run.
    """
    captured = {}

    class FakeAuthenticator:
        @staticmethod
        def from_file(filename):
            return "auth"

    def fake_client(auth, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(src.audible_client.audible, "Authenticator", FakeAuthenticator)
    monkeypatch.setattr(src.audible_client.audible, "Client", fake_client)

    src.audible_client.Audible("ignored.json")

    assert captured["timeout"] == src.audible_client._API_TIMEOUT
    assert captured["timeout"] > 10


def test_prepare_book_reads_the_consumable_flag():
    """`customer_rights.is_consumable` is how a withdrawn Plus title is spotted at sync time."""
    assert _prepare_book(make_item(customer_rights={"is_consumable": True})).is_consumable is True
    assert _prepare_book(make_item(customer_rights={"is_consumable": False})).is_consumable is False


@pytest.mark.parametrize("item", [{}, {"customer_rights": None}, {"customer_rights": {}}])
def test_prepare_book_treats_a_missing_consumable_flag_as_available(item):
    """
    Fails open on purpose.

    Defaulting to False when the response group is absent, or Audible stops sending it,
    would park the entire library as unavailable in one sync. The licence request is
    still the thing that decides.
    """
    assert _prepare_book(make_item(**item)).is_consumable is True


def test_response_groups_ask_for_customer_rights():
    """Without this group every book reads back as consumable and nothing is ever parked."""
    assert "customer_rights" in RESPONSE_GROUPS
