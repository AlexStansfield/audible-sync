import logging
from src.database import update_books, get_books
from src.audible import Audible

def sync_library(audible: Audible):
    logger = logging.getLogger("sync")
    # Check Books Count
    latest_book = get_books(1)
    purchased_after = None
    if (len(latest_book) == 0):
        logger.info('First sync, fetching all books.')
    else:
        purchased_after = latest_book[0][10]
        logger.info(f"Fetching books purchased since {purchased_after}")

    library = audible.get_library(purchased_after)
    books_synced = update_books(library)
    
    return books_synced