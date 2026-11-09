"""
The library: listing books, the per-book and bulk actions, the download queue and covers.

The rules the actions follow are `audible_sync.library`'s; these routes map its refusals to HTTP.
"""

import logging
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse

from audible_sync import library
from audible_sync.api.routes.accounts import account_or_404
from audible_sync.api.schemas import (
    BookAction,
    BookDetail,
    BookList,
    BookUpdate,
    BulkRequest,
    BulkResultOut,
    BulkSkip,
    QueueItem,
    QueueOut,
    Requeued,
    RetryUnavailable,
)
from audible_sync.database import (
    BOOK_SORT_COLUMNS,
    book_ids_matching,
    get_book,
    list_books,
    requeue_unavailable,
    set_monitored,
)
from audible_sync.model import BookStatus
from audible_sync.settings import Settings

logger = logging.getLogger(__name__)


def router() -> APIRouter:
    router = APIRouter()

    @router.get("/books", response_model=BookList)
    def get_books(
        account_id: int | None = None,
        status: BookStatus | None = None,
        monitored: bool | None = None,
        q: Annotated[str | None, Query(max_length=200)] = None,
        sort: Literal["date_added", "title", "author", "release_date", "downloaded_at"] = "date_added",
        order: Literal["asc", "desc"] = "desc",
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> BookList:
        assert sort in BOOK_SORT_COLUMNS  # the Literal above is the same set; keep them in step
        items, total = list_books(
            account_id=account_id,
            status=status,
            monitored=monitored,
            q=q,
            sort=sort,
            descending=order == "desc",
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return BookList(items=items, total=total, page=page, page_size=page_size)

    @router.post("/books/retry-unavailable", response_model=Requeued)
    def retry_unavailable(body: RetryUnavailable | None = None) -> Requeued:
        """
        Queue every parked book Audible still reports consumable again.

        For the day Audible refused every licence - a device registration it had
        stopped accepting, say - rather than one title it has retired. A re-login
        does this for the account by itself; this is the button for everything else.
        """
        account_id = body.account_id if body else None
        if account_id is not None:
            account_or_404(account_id)
        return Requeued(requeued=requeue_unavailable(account_id))

    @router.post("/books/bulk", response_model=BulkResultOut)
    def bulk_books(body: BulkRequest) -> BulkResultOut:
        """
        One action on many books: the ids selected, or everything the filters match.

        The filters mean what they mean on `GET /api/books`, so "all 354 matching" acts
        on the 354 the listing counted. Books a run is downloading, a retry of a
        downloaded book and deleting the files of a book with none are skipped, each
        with a reason; an unknown id is skipped as `not_found`. 404 for a filter on an
        unknown account.
        """
        if body.filter is not None:
            if body.filter.account_id is not None:
                account_or_404(body.filter.account_id)
            ids = book_ids_matching(**body.filter.model_dump())
        else:
            ids = body.ids
        result = library.bulk(body.action, ids, Settings.from_db())
        return BulkResultOut(
            updated=result.updated,
            queued=result.queued,
            skipped=[BulkSkip(id=book_id, reason=reason) for book_id, reason in result.skipped],
        )

    @router.get("/queue", response_model=QueueOut)
    def get_queue(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> QueueOut:
        """
        The books still to come, in the order a run takes them: each enabled account
        that can log in, oldest purchase first, cut at `max_download` per account. A
        plan rather than a promise (refusals, titles the sync restores, a run in flight;
        see `library.next_run_queue`). `limit` is how many to list; `total` (planned)
        and `waiting` count them all.
        """
        settings = Settings.from_db()
        queue = library.next_run_queue(settings)
        return QueueOut(
            items=[
                QueueItem(position=position, account_id=account_id, book=book)
                for position, (account_id, book) in enumerate(queue.planned[:limit], start=1)
            ],
            total=len(queue.planned),
            waiting=queue.waiting,
            max_download=settings.max_download,
        )

    @router.get("/books/{book_id}", response_model=BookDetail)
    def get_one_book(book_id: int) -> BookDetail:
        return _book_or_404(book_id)

    @router.patch("/books/{book_id}", response_model=BookDetail)
    def patch_book(book_id: int, update: BookUpdate) -> BookDetail:
        _book_or_404(book_id)
        set_monitored(book_id, update.monitored)
        return get_book(book_id)

    @router.post("/books/{book_id}/delete-files", response_model=BookAction)
    def delete_book_files(book_id: int) -> BookAction:
        """Remove the files and unmonitor the book, so it stays deleted."""
        book = _book_or_404(book_id)
        removed = _busy_to_409(library.delete_download, book, Settings.from_db())
        return BookAction(status="deleted", removed=[str(p) for p in removed])

    @router.post("/books/{book_id}/redownload", response_model=BookAction)
    def redownload_book(book_id: int) -> BookAction:
        """Remove the files and queue the book afresh, attempts reset."""
        book = _book_or_404(book_id)
        removed = _busy_to_409(library.redownload, book, Settings.from_db())
        return BookAction(status="queued", removed=[str(p) for p in removed])

    @router.post("/books/{book_id}/retry", response_model=BookAction)
    def retry_book(book_id: int) -> BookAction:
        """Queue a failed or parked book again without touching any files it has."""
        book = _book_or_404(book_id)
        try:
            library.retry(book)
        except library.BookBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return BookAction(status="queued")

    @router.post("/books/{book_id}/refresh", response_model=BookDetail)
    def refresh_book(book_id: int) -> BookDetail:
        """Re-read the book from Audible."""
        book = _book_or_404(book_id)
        try:
            return library.refresh(book)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except Exception as error:
            logger.warning("Could not refresh %s from Audible", book.asin, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Audible did not answer: {error}") from None

    @router.get("/books/{book_id}/cover")
    def book_cover(book_id: int):
        """The cover: the file once downloaded, Audible's URL before that."""
        book = _book_or_404(book_id)
        if book.cover_path and Path(book.cover_path).is_file():
            return FileResponse(book.cover_path)
        if book.cover_url:
            return RedirectResponse(book.cover_url, status_code=302)
        raise HTTPException(status_code=404, detail="No cover for this book")

    return router


def _book_or_404(book_id: int):
    book = get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail=f"No book with id {book_id}")
    return book


def _busy_to_409(action, book, settings):
    try:
        return action(book, settings)
    except library.BookBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
