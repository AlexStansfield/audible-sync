"""
The Audible catalogue as the API offers it: searching, a book, a series, the
categories and authors, each through an account and cached for a few minutes.

The catalogue is the marketplace's, not the account's, but the account is what picks
the marketplace and what Audible tells the member's price to. What only Audible Sync
can add is what the libraries know: `holdings` says which books one already holds.
"""

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from audible_sync.audible_api.accounts import authenticator_for, persist_auth_if_changed
from audible_sync.audible_api.client import Audible
from audible_sync.audible_api.products import normalise_title, reachable_results
from audible_sync.database import get_account, get_accounts, library_book_ids
from audible_sync.model import Account, CatalogBook, CatalogSeries

logger = logging.getLogger(__name__)

T = TypeVar("T")

# A client polling a search, or a page shown again, is answered from memory rather than
# becoming a stream of requests to Audible in the account's name
CACHE_SECONDS = 300
CACHE_ENTRIES = 256

# A series name is looked for among the books of one page of a keyword search for it
_SERIES_SEARCH_SIZE = 50
_SERIES_MATCHES = 10


class NoAccount(LookupError):
    """There is no account signed in to search with."""


class AccountNeedsLogin(LookupError):
    """The account asked for has no working credentials."""


@dataclass
class SearchPage:
    books: list[CatalogBook]
    # Audible's count of everything matched, and how many of those it will page through
    total: int
    reachable: int


@dataclass
class SeriesMatch:
    """A series a name search found, and how many of the search's books are in it."""

    asin: str
    title: str
    books_found: int
    exact: bool


class TTLCache:
    """
    A small thread-safe cache whose entries expire, dropping the oldest when full.

    The endpoints run in a threadpool, hence the lock. Two requests missing at once both
    fetch; the later answer is the one kept, which is harmless.
    """

    def __init__(self, seconds: float = CACHE_SECONDS, entries: int = CACHE_ENTRIES, clock=time.monotonic):
        self.seconds = seconds
        self.entries = entries
        self.clock = clock
        self._lock = threading.Lock()
        self._items: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any, fetch: Callable[[], T]) -> T:
        now = self.clock()
        with self._lock:
            hit = self._items.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
        value = fetch()
        with self._lock:
            self._items[key] = (now + self.seconds, value)
            self._items.move_to_end(key)
            while len(self._items) > self.entries:
                self._items.popitem(last=False)
        return value


class Catalog:
    """
    The catalogue calls, each made with an account's credentials and its answer cached.

    `client_for` builds the Audible client from an account; tests hand in a fake.
    """

    def __init__(self, cache: TTLCache | None = None, client_for: Callable[[Account], Audible] | None = None):
        self.cache = cache or TTLCache()
        self.client_for = client_for or (lambda account: Audible(authenticator_for(account)))

    def account(self, account_id: int | None = None) -> Account:
        """
        The account to ask Audible through: the one named, or the oldest signed in.

        Raises:
            LookupError: `KeyError` for an unknown id, `AccountNeedsLogin` for one with no
                working credentials, `NoAccount` when none is signed in
        """
        if account_id is not None:
            account = get_account(account_id)
            if account is None:
                raise KeyError(account_id)
            if account.needs_login:
                raise AccountNeedsLogin(f"{account.name} needs signing in again")
            return account
        for account in get_accounts():
            if not account.needs_login:
                return account
        raise NoAccount("Add an Audible account, or sign in to one, to search the catalogue")

    def _ask(self, account: Account, key: tuple, call: Callable[[Audible], T]) -> T:
        def fetch() -> T:
            audible = self.client_for(account)
            try:
                return call(audible)
            finally:
                # A token the call refreshed is kept, as `library.refresh` does
                try:
                    persist_auth_if_changed(account, audible.auth)
                except Exception:
                    logger.exception("Could not store the refreshed credentials of %r", account)

        return self.cache.get((account.id, *key), fetch)

    def search(
        self,
        account: Account,
        *,
        keywords: str | None = None,
        title: str | None = None,
        author: str | None = None,
        narrator: str | None = None,
        category_id: str | None = None,
        sort: str = "relevance",
        page: int = 1,
        page_size: int = 20,
    ) -> SearchPage:
        filters = {
            "keywords": keywords,
            "title": title,
            "author": author,
            "narrator": narrator,
            "category_id": category_id,
        }
        key = ("search", tuple(sorted(filters.items())), sort, page, page_size)
        books, total = self._ask(
            account,
            key,
            lambda audible: audible.search_catalog(**filters, sort=sort, page=page, page_size=page_size),
        )
        return SearchPage(books=books, total=total, reachable=min(total, reachable_results(sort)))

    def book(self, account: Account, asin: str) -> CatalogBook | None:
        return self._ask(account, ("book", asin), lambda audible: audible.get_catalog_book(asin))

    def series(self, account: Account, asin: str) -> CatalogSeries | None:
        return self._ask(account, ("series", asin), lambda audible: audible.get_series(asin))

    def find_series(self, account: Account, name: str, author: str | None = None) -> list[SeriesMatch]:
        """
        The series called `name`, best first: Audible has no search by series, so this
        is a keyword search for the name, and the series its books carry whose title is
        the name (any case, any apostrophe) or contains it.

        The exact matches come first, then by how many of the search's books are in
        each. A series none of the first page's books is in is not found.
        """
        page = self.search(account, keywords=name, author=author, page_size=_SERIES_SEARCH_SIZE)
        wanted = normalise_title(name)
        found: dict[str, SeriesMatch] = {}
        for book in page.books:
            for entry in book.series:
                series_asin, series_title = entry.get("series_asin"), entry.get("title")
                if not series_asin or not series_title:
                    continue
                normalised = normalise_title(series_title)
                if wanted not in normalised:
                    continue
                match = found.setdefault(
                    series_asin,
                    SeriesMatch(asin=series_asin, title=series_title, books_found=0, exact=normalised == wanted),
                )
                match.books_found += 1
        ranked = sorted(found.values(), key=lambda match: (not match.exact, -match.books_found, match.title))
        return ranked[:_SERIES_MATCHES]

    def categories(self, account: Account) -> list[dict]:
        return self._ask(account, ("categories",), lambda audible: audible.get_categories())

    def authors(self, account: Account, name: str) -> list[dict[str, str | None]]:
        return self._ask(account, ("authors", name.strip().casefold()), lambda audible: audible.search_authors(name))


def holdings(account: Account, books: list[CatalogBook]) -> dict[str, int]:
    """Which of these books a library of the account's marketplace holds, ASIN to row id."""
    return library_book_ids([book.asin for book in books], account.country_code)
