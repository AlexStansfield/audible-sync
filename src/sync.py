import logging

from src.audible import Audible
from src.database import latest_date_added, needs_consumability_refresh, update_books

logger = logging.getLogger(__name__)


def sync_library(audible: Audible) -> int:
    """
    Fetch new books from Audible into the library table.

    The first run fetches everything; later runs fetch only what was purchased
    after the newest `date_added` already stored. Returns the number of books
    inserted.
    """
    purchased_after = latest_date_added()
    if purchased_after is None:
        logger.info("Fetching all books")
    else:
        logger.info("Fetching books purchased since %s", purchased_after)

    library = audible.get_library(purchased_after)
    books_synced = update_books(library)

    if purchased_after is not None and needs_consumability_refresh():
        # An incremental fetch never re-reads a book already in the library, so on its
        # own it can never notice that Audible has offered a withdrawn Plus title again
        # (or withdrawn one that was fine). Re-reading the whole library is one extra
        # request per 1000 titles and only happens while something is actually parked.
        logger.info("Re-reading the full library to refresh availability")
        update_books(audible.get_library(None))

    return books_synced
