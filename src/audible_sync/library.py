"""
What the API does to one book: unmonitor it, delete its files, download it again.

The download pipeline files a book and records where; this module is the reverse
direction. The rules the actions share: a book a run is working on (`downloading`) is
never touched, the files on disk are removed before the row forgets them, and every
transition goes through `database.reset_for_download` so the state machine has one
way back to the start.
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from audible_sync.accounts import authenticator_for, persist_auth_if_changed
from audible_sync.audible_client import Audible
from audible_sync.database import (
    clear_book_files,
    get_account,
    get_accounts,
    get_book,
    get_books_to_download,
    reset_for_download,
    set_monitored,
    update_books,
)
from audible_sync.encoding import output_extension, read_embedded_asin
from audible_sync.model import Book, BookStatus
from audible_sync.naming import book_output_paths
from audible_sync.settings import Settings

logger = logging.getLogger(__name__)


class BookBusy(Exception):
    """A run is downloading the book; cancel the run first."""


def audio_path_for(book: Book, settings: Settings) -> Path | None:
    """
    Where the book's finished audio is, or None if there is no file to be found.

    `file_path` is recorded on download. A book downloaded before it was recorded is
    found the way the downloader would file it today - the naming templates, then the
    ` [asin]` variant used when two books render to the same name - and only counts
    when the file really carries this book's ASIN, so a book that has since been filed
    elsewhere under different templates is a miss, not somebody else's file.
    """
    if book.file_path:
        return Path(book.file_path)

    folder, stem = book_output_paths(
        book, settings.audiobook_folder, settings.folder_template, settings.filename_template
    )
    # Rows from before the format was recorded are M4B: Opus arrived with the column
    extension = output_extension(book.encoding_format or "m4b")
    for candidate in (folder / f"{stem}{extension}", folder / f"{stem} [{book.asin}]{extension}"):
        if candidate.is_file() and read_embedded_asin(candidate) == book.asin:
            return candidate
    return None


def book_files(book: Book, settings: Settings) -> list[Path]:
    """The book's audio, PDF, cover and annotations that exist on disk."""
    candidates = [audio_path_for(book, settings)] + [
        Path(p) for p in (book.pdf_path, book.cover_path, book.annotations_path) if p
    ]
    return [path for path in candidates if path is not None and path.is_file()]


def delete_book_files(book: Book, settings: Settings, *, files: list[Path] | None = None) -> list[Path]:
    """
    Remove the audio, PDF, cover and annotations, and any folders left empty.

    Returns what was removed. A file already gone is not an error. Empty parents are
    pruned up to, but never including, the audiobook folder itself, so removing the
    last book of a series takes the series folder with it while a folder that still
    holds another book is left alone.
    """
    removed: list[Path] = []
    for path in book_files(book, settings) if files is None else files:
        path.unlink()
        removed.append(path)
        logger.info("Removed %s", path)

    root = Path(settings.audiobook_folder).resolve()
    for path in removed:
        _prune_empty_parents(path.parent, root)
    return removed


def _prune_empty_parents(folder: Path, root: Path) -> None:
    while folder.resolve() != root and root in folder.resolve().parents:
        try:
            folder.rmdir()  # only succeeds when empty
        except OSError:
            return
        logger.info("Removed empty folder %s", folder)
        folder = folder.parent


def _not_busy(book: Book) -> None:
    if book.status is BookStatus.DOWNLOADING:
        raise BookBusy(f"{book.title} ({book.asin}) is being downloaded; cancel the run first")


def _reset(book: Book, *, monitored: bool) -> None:
    """
    `reset_for_download`, treating "nothing changed" as the run having got there first.

    `_not_busy` reads the `Book` the caller fetched; a run can claim the row between
    that read and this write, and the guarded UPDATE then changes nothing. Reporting
    that as success would lose the request silently.
    """
    if not reset_for_download(book.id, monitored=monitored):
        raise BookBusy(f"{book.title} ({book.asin}) was claimed by a run; cancel the run first")


def delete_download(book: Book, settings: Settings, *, files: list[Path] | None = None) -> list[Path]:
    """
    Remove the files and stop wanting the book: it goes back to `waiting_download`
    unmonitored, so it is not fetched again until somebody asks. `files` is what
    `book_files` returned, for a caller that has already looked.

    Raises:
        BookBusy: if a run is downloading it
    """
    _not_busy(book)
    removed = delete_book_files(book, settings, files=files)
    clear_book_files(book.id)
    _reset(book, monitored=False)
    return removed


def redownload(book: Book, settings: Settings) -> list[Path]:
    """
    Remove the files and queue the book afresh, attempts and all.

    Raises:
        BookBusy: if a run is downloading it
    """
    _not_busy(book)
    removed = delete_book_files(book, settings)
    clear_book_files(book.id)
    _reset(book, monitored=True)
    return removed


def retry(book: Book) -> None:
    """
    Give a book that failed (or was parked) another go, keeping any files it has.

    Raises:
        BookBusy: if a run is downloading it
        ValueError: if the book is already downloaded - that is what `redownload` is for
    """
    _not_busy(book)
    if book.status is BookStatus.DOWNLOADED:
        raise ValueError(f"{book.title} ({book.asin}) is already downloaded; use redownload to fetch it again")
    _reset(book, monitored=True)


def refresh(book: Book) -> Book:
    """
    Re-read one book from Audible and store what came back.

    The one network call in this module. Raises whatever the Audible client raises.
    A token the call refreshed is stored afterwards, as a run does, so the next call
    does not have to refresh it again.
    """
    account = get_account(book.account_id)
    if account is None or account.needs_login:
        raise ValueError(f"account {book.account_id} has no credentials")
    audible = Audible(authenticator_for(account))
    try:
        fresh = audible.get_book(book.asin)
    finally:
        try:
            persist_auth_if_changed(account, audible.auth)
        except Exception:
            logger.exception("Could not store the refreshed credentials of %r", account)
    update_books(account.id, [fresh])
    return get_book(book.id)


class BulkAction(StrEnum):
    MONITOR = "monitor"
    UNMONITOR = "unmonitor"
    RETRY = "retry"
    DELETE_FILES = "delete_files"


class SkipReason(StrEnum):
    """Why a bulk action left a book alone; the UI words it."""

    NOT_FOUND = "not_found"
    BUSY = "busy"  # a run is downloading it
    DOWNLOADED = "downloaded"  # retry on a book that is already downloaded
    WITHDRAWN = "withdrawn"  # retry on a title Audible withdrew; the sync brings it back
    NO_FILES = "no_files"  # delete_files on a book never downloaded, with nothing on disk
    ERROR = "error"  # something went wrong with this book; the log has the traceback


@dataclass
class BulkResult:
    """
    What a bulk action did: `updated` books changed, `queued` of them now wait for a
    download, and the rest were `skipped` with a reason each.
    """

    updated: int = 0
    queued: int = 0
    skipped: list[tuple[int, SkipReason]] = field(default_factory=list)


def bulk(action: BulkAction | str, book_ids: list[int], settings: Settings) -> BulkResult:
    """
    Apply one action to many books, book by book, through the single-book functions.

    A book a run is downloading is skipped for every action - monitor included, unlike
    `PATCH /api/books/{id}`, because the design's result banner reports it that way.
    `monitor` counts as queued only the books that are `waiting_download`: a failed or
    parked book keeps its status until it is retried. A duplicate id is acted on once.

    One book failing (a file on a read-only share, say) is logged and skipped as
    `error`, and the batch carries on, so the result always accounts for every book.
    """
    action = BulkAction(action)
    result = BulkResult()
    for book_id in dict.fromkeys(book_ids):
        book = get_book(book_id)
        if book is None:
            result.skipped.append((book_id, SkipReason.NOT_FOUND))
            continue
        try:
            reason = _bulk_one(action, book, settings)
        except BookBusy:
            reason = SkipReason.BUSY
        except Exception:
            logger.exception("Bulk %s failed on %r", action, book)
            reason = SkipReason.ERROR
        if reason is not None:
            result.skipped.append((book_id, reason))
            continue
        result.updated += 1
        if action is BulkAction.RETRY or (action is BulkAction.MONITOR and book.status is BookStatus.WAITING_DOWNLOAD):
            result.queued += 1
    logger.info(
        "Bulk %s: %d updated, %d queued, %d skipped", action, result.updated, result.queued, len(result.skipped)
    )
    return result


def _bulk_one(action: BulkAction, book: Book, settings: Settings) -> SkipReason | None:
    """One book of a bulk action: None when it was done, else why not."""
    _not_busy(book)
    match action:
        case BulkAction.MONITOR | BulkAction.UNMONITOR:
            set_monitored(book.id, action is BulkAction.MONITOR)
        case BulkAction.RETRY:
            if book.status is BookStatus.DOWNLOADED:
                return SkipReason.DOWNLOADED
            # A certain refusal, and a queue of them trips the circuit breaker; this is
            # why `requeue_unavailable` leaves them to the sync too
            if book.status is BookStatus.UNAVAILABLE and not book.is_consumable:
                return SkipReason.WITHDRAWN
            retry(book)
        case BulkAction.DELETE_FILES:
            files = book_files(book, settings)
            # A downloaded book whose files have gone is still tidied, as the
            # single-book route does, so it stops claiming paths that are not there
            if not files and book.status is not BookStatus.DOWNLOADED:
                return SkipReason.NO_FILES
            delete_download(book, settings, files=files)
    return None


@dataclass
class Queue:
    """
    The next run's downloads in order: `planned` as (account id, book) pairs, and how
    many monitored books wait in those accounts in all, the `max_download` cut ignored.
    """

    planned: list[tuple[int, Book]]
    waiting: int


def next_run_queue(settings: Settings) -> Queue:
    """
    The books still to come, from the queue as it stands now: each account a run takes
    (`Account.runnable`), in the order the pipeline takes them, its queue oldest
    purchase first and cut at `max_download` - which is per account per run.

    The same query the run uses (`get_books_to_download`), so a book a crashed run left
    `downloading` shows once its claim is stale. It is a plan, not a promise, for three
    reasons nothing can know in advance:

    - a licence Audible refuses uses no slot, so the run reaches further down;
    - the run syncs before it downloads, and a withdrawn title Audible offers again
      rejoins the queue at its old purchase date, taking a slot from the end of this
      list (new purchases are the newest, so they only ever join the end);
    - while a run is in flight, its head is what that run has left: the book it is on
      is not listed (it is `current_run.book` in `/api/status`), and the accounts it has
      finished are listed again as the next run will find them.
    """
    planned: list[tuple[int, Book]] = []
    waiting = 0
    for account in get_accounts():
        if not account.runnable:
            continue
        books = get_books_to_download(account_id=account.id)
        waiting += len(books)
        cut = books if settings.max_download is None else books[: settings.max_download]
        planned.extend((account.id, book) for book in cut)
    return Queue(planned=planned, waiting=waiting)
