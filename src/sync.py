import logging
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from src.audible_client import Audible
from src.database import (
    latest_date_added,
    latest_successful_sync_start,
    needs_consumability_refresh,
    update_books,
)

logger = logging.getLogger(__name__)

# How far back beyond the last run's start the cursor reaches. `purchased_after` is
# filtered on Audible's clock, not ours, so a local clock even slightly ahead would step
# over a purchase and never look at it again. Re-reading an hour of overlap costs
# nothing: `update_books` is an upsert, and a book already stored is simply refreshed.
CURSOR_OVERLAP = timedelta(hours=1)


class SyncResult(NamedTuple):
    """What one library sync read and what it changed."""

    books_seen: int
    books_added: int


def _api_timestamp(moment: datetime) -> str:
    """
    Format a timestamp the way the Audible API expects `purchased_after`.

    Stored run timestamps carry a `+00:00` offset (see `database._utcnow`) while
    Audible's own `date_added` is the `Z` form, so the two are not interchangeable and
    a cursor taken from a run has to be converted before it leaves for the API.
    """
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sync_cursor() -> str | None:
    """
    The `purchased_after` value for this run, or None to fetch the whole library.

    Preferred source is when the last run that read the library through started, which
    is a real record of "last synced". Falls back to the newest `date_added` in the
    library - the cursor this used to derive everything from - so a database that
    predates the `sync_runs` table behaves exactly as it did until it has recorded its
    first run. With neither, there is nothing to be incremental about.
    """
    started_at = latest_successful_sync_start()
    if started_at is not None:
        return _api_timestamp(datetime.fromisoformat(started_at) - CURSOR_OVERLAP)

    # Already in Audible's own format: it came from Audible in the first place.
    return latest_date_added()


def sync_library(audible: Audible) -> SyncResult:
    """
    Fetch new books from Audible into the library table.

    The first run fetches everything; later runs fetch only what was purchased since
    the previous run started (see `_sync_cursor`).

    Returns how many books the incremental fetch read and how many of them were new.
    The availability refresh pass below counts towards neither: it re-reads the whole
    library, so folding it in would report the library size as this run's work.
    """
    purchased_after = _sync_cursor()
    if purchased_after is None:
        logger.info("Fetching all books")
    else:
        logger.info("Fetching books purchased since %s", purchased_after)

    library = audible.get_library(purchased_after)
    books_added = update_books(library)

    if purchased_after is not None and needs_consumability_refresh():
        # An incremental fetch never re-reads a book already in the library, so on its
        # own it can never notice that Audible has offered a withdrawn Plus title again
        # (or withdrawn one that was fine). Re-reading the whole library is one extra
        # request per 1000 titles and only happens while something is actually parked.
        logger.info("Re-reading the full library to refresh availability")
        update_books(audible.get_library(None))

    return SyncResult(books_seen=len(library), books_added=books_added)
