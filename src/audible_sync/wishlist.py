"""
The wishlists: reading each account's from Audible, and adding and removing books.

Audible answers an add or a remove the same whether it changed anything or not, so
whether a book was already there, or already gone, is said from the `wishlist` table,
which every read replaces and every add or remove keeps in step.
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from audible_sync.audible_api.accounts import authenticator_for, keeping_credentials
from audible_sync.audible_api.client import Audible
from audible_sync.catalog import Catalog
from audible_sync.database import (
    add_wishlist_item,
    get_wishlist_item,
    library_holdings,
    record_wishlist_failure,
    remove_wishlist_item,
    replace_wishlist,
)
from audible_sync.model import Account, WishlistItem

logger = logging.getLogger(__name__)

# One account's wishlist is read and stored, or changed, by one thread at a time: a read
# replaces the table from a list fetched before it, so an add or a remove landing
# between the fetch and the store would be undone. A run and the API share the process
# (a CLI run beside the service is not covered, and the next read puts it right)
_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


@contextmanager
def _wishlist_of(account_id: int) -> Iterator[None]:
    with _locks_guard:
        lock = _locks[account_id]
    with lock:
        yield


class OwnedBook(ValueError):
    """The account's own library holds the book, so it does not go on its wishlist."""


class NotInCatalogue(LookupError):
    """Audible has no book with that ASIN."""


def read_wishlist(audible: Audible, account: Account) -> bool:
    """
    Read the account's whole wishlist from Audible and store it in place of the last.

    Never raises: a run calls it after its library sync, and a wishlist that cannot be
    read must leave the run's outcome alone. A failure is logged and recorded, and the
    books stored stay as of the last read that worked. Returns whether it was read.
    """
    with _wishlist_of(account.id):
        return _read_and_store(audible, account)


def _read_and_store(audible: Audible, account: Account) -> bool:
    try:
        items = audible.get_wishlist(account.id)
    except Exception as error:
        # Anything at all: the run that called this must go on to its downloads
        logger.warning("Could not read the wishlist of %r: %s", account, error, exc_info=True)
        try:
            record_wishlist_failure(account.id, f"{type(error).__name__}: {error}")
        except Exception:
            logger.exception("Could not record the failed wishlist read of %r", account)
        return False
    try:
        stored = replace_wishlist(account.id, items)
    except Exception:
        logger.exception("Could not store the wishlist of %r", account)
        return False
    if not stored:
        logger.info("Not storing the wishlist of %r: the account has been removed", account)
        return False
    logger.info("%d books on the wishlist of %s", len(items), account.name)
    return True


class Wishlists:
    """
    The calls that change or re-read a wishlist, each with the account's credentials.

    `client_for` builds the Audible client from an account; tests hand in a fake. A book
    is read through `catalog`, so one the catalogue showed a moment ago costs nothing.
    """

    def __init__(self, client_for: Callable[[Account], Audible] | None = None, catalog: Catalog | None = None):
        self.client_for = client_for or (lambda account: Audible(authenticator_for(account)))
        self.catalog = catalog or Catalog(client_for=self.client_for)

    def refresh(self, account: Account) -> bool:
        """Read the account's wishlist again now; whether it could be (see `read_wishlist`)."""
        with keeping_credentials(account, self.client_for(account)) as audible:
            return read_wishlist(audible, account)

    def add(self, account: Account, asin: str) -> tuple[WishlistItem, bool]:
        """
        Put a book on the account's wishlist, and say whether it was new to it.

        The book is read from the catalogue first, so an ASIN Audible does not know is
        refused rather than sent, and so the table has its details at once.

        Raises:
            OwnedBook: when the account's own library holds it
            NotInCatalogue: when Audible has no such book
            audible.exceptions.AudibleError: when Audible refuses or does not answer
        """
        holders = {copy[0] for copy in library_holdings([asin], account.country_code).get(asin, [])}
        if account.id in holders:
            raise OwnedBook(f"{account.name} owns this book, so it cannot go on their wishlist")
        book = self.catalog.book(account, asin)
        if book is None:
            raise NotInCatalogue(f"No book {asin} in the catalogue")
        with _wishlist_of(account.id):
            with keeping_credentials(account, self.client_for(account)) as audible:
                audible.add_to_wishlist(asin)
            item = WishlistItem(account_id=account.id, book=book, added_at=_now())
            added = add_wishlist_item(item)
            return (item if added else get_wishlist_item(account.id, asin) or item), added

    def remove(self, account: Account, asin: str) -> bool:
        """
        Take a book off the account's wishlist; whether it was on it, as far as the table knows.

        Audible is told either way, so a book put on the wishlist on Audible since the
        last read comes off too.

        Raises:
            audible.exceptions.AudibleError: when Audible refuses or does not answer
        """
        with _wishlist_of(account.id):
            with keeping_credentials(account, self.client_for(account)) as audible:
                audible.remove_from_wishlist(asin)
            return remove_wishlist_item(account.id, asin)


def _now() -> str:
    # As Audible writes `added_timestamp`, so the two sort together
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
