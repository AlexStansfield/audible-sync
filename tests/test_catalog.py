import pytest

import audible_sync.database as database
from audible_sync import catalog as catalog_module
from audible_sync.catalog import AccountNeedsLogin, Catalog, NoAccount, TTLCache, holdings
from audible_sync.model import CatalogBook
from tests.conftest import make_book


class FakeAuth:
    def __init__(self, blob):
        self.blob = blob

    def to_dict(self):
        return self.blob


class FakeAudible:
    """Stands in for `Audible`'s catalogue calls, counting them."""

    def __init__(self, account, books=(), total=None):
        self.auth = FakeAuth(account.auth)
        self.books = list(books)
        self.total = len(self.books) if total is None else total
        self.calls = []

    def search_catalog(self, **params):
        self.calls.append(("search", params))
        return self.books, self.total

    def get_catalog_book(self, asin):
        self.calls.append(("book", asin))
        return next((book for book in self.books if book.asin == asin), None)


def catalogue_book(asin, *series):
    return CatalogBook(
        asin=asin,
        title=asin,
        series=[
            {"title": title, "sequence": sequence, "series_asin": series_asin}
            for series_asin, title, sequence in series
        ],
    )


@pytest.fixture
def account(scratch_db):
    account_id = database.add_account("Alex (UK)", "uk", auth={"locale_code": "uk", "access_token": "Atna|t"})
    return database.get_account(account_id)


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()


def make_catalog(fake=None, **cache):
    made = []

    def client_for(account):
        client = fake or FakeAudible(account)
        made.append(client)
        return client

    found = Catalog(cache=TTLCache(**cache) if cache else None, client_for=client_for)
    found.made = made
    return found


# --- which account ------------------------------------------------------------------


def test_the_oldest_signed_in_account_is_the_default(scratch_db):
    database.add_account("Pending", "us", auth=None)
    second = database.add_account("Alex (UK)", "uk", auth={"locale_code": "uk"})

    assert make_catalog().account().id == second


def test_with_no_signed_in_account_there_is_none_to_ask(scratch_db):
    database.add_account("Pending", "us", auth=None)

    with pytest.raises(NoAccount):
        make_catalog().account()


def test_an_account_named_must_be_signed_in(scratch_db):
    pending = database.add_account("Pending", "us", auth=None)

    with pytest.raises(AccountNeedsLogin):
        make_catalog().account(pending)
    with pytest.raises(KeyError):
        make_catalog().account(999)


# --- caching ------------------------------------------------------------------------


def test_the_same_search_is_answered_from_memory(account):
    fake = FakeAudible(account, [catalogue_book("B000000001")])
    found = make_catalog(fake)

    found.search(account, keywords="scalzi")
    found.search(account, keywords="scalzi")
    found.search(account, keywords="scalzi", page=2)

    assert [params.get("page") for _, params in fake.calls] == [1, 2]


def test_an_answer_expires():
    now = [0.0]
    cache = TTLCache(seconds=10, clock=lambda: now[0])
    fetched = []

    def fetch():
        fetched.append(now[0])
        return len(fetched)

    assert cache.get("k", fetch) == 1
    now[0] = 9.9
    assert cache.get("k", fetch) == 1
    now[0] = 10.1
    assert cache.get("k", fetch) == 2


def test_the_oldest_entry_goes_when_the_cache_is_full():
    cache = TTLCache(entries=2)
    for key in ("a", "b", "c"):
        cache.get(key, lambda key=key: key)

    assert cache.get("a", lambda: "again") == "again"
    assert cache.get("c", lambda: "again") == "c"


def test_a_failed_call_is_not_cached(account):
    calls = []

    class Failing(FakeAudible):
        def get_catalog_book(self, asin):
            calls.append(asin)
            raise RuntimeError("down")

    found = make_catalog(Failing(account))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            found.book(account, "B000000001")

    assert len(calls) == 2


def test_a_refreshed_token_is_stored(account):
    fake = FakeAudible(account)
    fake.auth = FakeAuth({**account.auth, "access_token": "Atna|refreshed"})

    make_catalog(fake).book(account, "B000000001")

    assert database.get_account(account.id).auth["access_token"] == "Atna|refreshed"


def test_the_reachable_results_follow_the_sort(account):
    found = make_catalog(FakeAudible(account, [], total=40_000))

    assert found.search(account, keywords="x", sort="bestsellers").reachable == 5000
    assert found.search(account, keywords="x", sort="newest").reachable == 1000
    assert make_catalog(FakeAudible(account, [], total=28)).search(account, keywords="x").reachable == 28


# --- series by name -------------------------------------------------------------------


def test_a_series_is_found_through_its_books(account):
    curly = "Old Man\N{RIGHT SINGLE QUOTATION MARK}s War"
    books = [
        catalogue_book("B000000001", ("S1", curly, "1")),
        catalogue_book("B000000002", ("S1", curly, "2"), ("S9", "Omnibus", "3")),
        catalogue_book("B000000003", ("S2", "Old Man's War: The Graphic Novels", "1")),
        catalogue_book("B000000004", ("S3", "Old Man's Apocalypse", "1")),
        catalogue_book("B000000005"),
    ]
    fake = FakeAudible(account, books)

    matches = make_catalog(fake).find_series(account, "old man's war", author="John Scalzi")

    assert [(match.asin, match.exact, match.books_found) for match in matches] == [("S1", True, 2), ("S2", False, 1)]
    assert fake.calls[0][1]["keywords"] == "old man's war"
    assert fake.calls[0][1]["author"] == "John Scalzi"


# --- holdings ---------------------------------------------------------------------------


def test_holdings_count_every_library_of_the_marketplace(account):
    other = database.add_account("Alex too (UK)", "UK", auth={"locale_code": "uk"})
    us = database.add_account("Alex (US)", "us", auth={"locale_code": "us"})
    database.update_books(account.id, [make_book("B000000001", id=None, account_id=None)])
    database.update_books(other, [make_book("B000000002", id=None, account_id=None)])
    database.update_books(us, [make_book("B000000003", id=None, account_id=None)])

    held = holdings(account, [CatalogBook(asin=asin, title="") for asin in ("B000000001", "B000000002", "B000000003")])

    assert set(held) == {"B000000001", "B000000002"}
    assert held["B000000001"] == database.get_book_by_asin("B000000001").id


def test_no_books_hold_nothing(account):
    assert holdings(account, []) == {}


def test_the_module_caches_for_minutes():
    assert 60 <= catalog_module.CACHE_SECONDS <= 900


def test_a_page_past_audibles_reach_is_refused_without_asking(account):
    fake = FakeAudible(account, [catalogue_book("B000000001")])

    with pytest.raises(catalog_module.PastReach) as refused:
        make_catalog(fake).search(account, keywords="x", sort="newest", page=51, page_size=20)

    assert refused.value.reachable == 1000
    assert fake.calls == []


def test_a_book_listing_a_series_twice_counts_once(account):
    twice = catalogue_book("B000000001", ("S1", "Dune", "1"), ("S1", "Dune", "1.0"))
    others = [catalogue_book(f"B00000000{n}", ("S2", "Dune Chronicles", "1")) for n in (2, 3)]

    matches = make_catalog(FakeAudible(account, [twice, *others])).find_series(account, "dune")

    assert [(match.asin, match.books_found) for match in matches] == [("S1", 1), ("S2", 2)]


def test_the_client_is_closed_and_a_refreshed_token_kept_when_a_call_fails(account):
    closed = []

    class Failing(FakeAudible):
        def get_catalog_book(self, asin):
            raise RuntimeError("down")

        def close(self):
            closed.append(True)

    fake = Failing(account)
    fake.auth = FakeAuth({**account.auth, "access_token": "Atna|refreshed"})

    with pytest.raises(RuntimeError):
        make_catalog(fake).book(account, "B000000001")

    assert closed == [True]
    assert database.get_account(account.id).auth["access_token"] == "Atna|refreshed"
