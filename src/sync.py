import logging

from src.audible import Audible
from src.database import latest_date_added, update_books

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

    return books_synced
