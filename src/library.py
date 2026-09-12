"""
What the API does to one book: unmonitor it, delete its files, download it again.

The download pipeline files a book and records where; this module is the reverse
direction. The rules the actions share: a book a run is working on (`downloading`) is
never touched, the files on disk are removed before the row forgets them, and every
transition goes through `database.reset_for_download` so the state machine has one
way back to the start.
"""

import logging
from pathlib import Path

from src.accounts import authenticator_for
from src.audible_client import Audible
from src.database import clear_book_files, get_account, get_book, reset_for_download, update_books
from src.encoding import output_extension, read_embedded_asin
from src.model import Book, BookStatus
from src.naming import book_output_paths
from src.settings import Settings

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


def delete_book_files(book: Book, settings: Settings) -> list[Path]:
    """
    Remove the audio, PDF, cover and annotations, and any folders left empty.

    Returns what was removed. A file already gone is not an error. Empty parents are
    pruned up to, but never including, the audiobook folder itself, so removing the
    last book of a series takes the series folder with it while a folder that still
    holds another book is left alone.
    """
    removed: list[Path] = []
    candidates = [audio_path_for(book, settings)] + [
        Path(p) for p in (book.pdf_path, book.cover_path, book.annotations_path) if p
    ]
    for path in candidates:
        if path is not None and path.is_file():
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


def delete_download(book: Book, settings: Settings) -> list[Path]:
    """
    Remove the files and stop wanting the book: it goes back to `waiting_download`
    unmonitored, so it is not fetched again until somebody asks.

    Raises:
        BookBusy: if a run is downloading it
    """
    _not_busy(book)
    removed = delete_book_files(book, settings)
    clear_book_files(book.id)
    reset_for_download(book.id, monitored=False)
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
    reset_for_download(book.id, monitored=True)
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
    reset_for_download(book.id, monitored=True)


def refresh(book: Book) -> Book:
    """
    Re-read one book from Audible and store what came back.

    The one network call in this module. Raises whatever the Audible client raises.
    """
    account = get_account(book.account_id)
    if account is None or account.needs_login:
        raise ValueError(f"account {book.account_id} has no credentials")
    fresh = Audible(authenticator_for(account)).get_book(book.asin)
    update_books(account.id, [fresh])
    return get_book(book.id)
