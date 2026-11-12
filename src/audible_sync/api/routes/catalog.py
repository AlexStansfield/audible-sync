"""
The Audible catalogue: searching it, a book, a series, the categories and authors.

Each goes to Audible through an account, `account_id` or the oldest signed in, which
picks the marketplace; answers are cached for a few minutes (`audible_sync.catalog`).
409 when there is no account to ask through, 502 when Audible does not answer.
"""

import logging
from collections.abc import Callable
from typing import Annotated, Literal

from audible.exceptions import AudibleError, NotFoundError, Unauthorized
from fastapi import APIRouter, HTTPException, Path, Query

from audible_sync.api.schemas import (
    AuthorOut,
    CatalogBookDetail,
    CatalogBookOut,
    CatalogSearchOut,
    CatalogSeriesOut,
    CategoryOut,
    LibraryCopyOut,
    SeriesBookOut,
    SeriesMatchOut,
)
from audible_sync.audible_api.products import MAX_PAGE_SIZE, SORTS
from audible_sync.catalog import AccountNeedsLogin, Catalog, NoAccount, PastReach, marks
from audible_sync.model import Account, CatalogBook

logger = logging.getLogger(__name__)


# ASINs are ten letters and digits; checked here, as one goes into Audible's URL
Asin = Annotated[str, Path(pattern=r"^[A-Za-z0-9]{10}$")]
Term = Annotated[str | None, Query(min_length=1, max_length=200)]
Sort = Literal[tuple(SORTS)]  # type: ignore[valid-type]


def account_for(catalog: Catalog, account_id: int | None) -> Account:
    """The account to ask Audible through (`Catalog.account`), its refusals as HTTP."""
    try:
        return catalog.account(account_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No account with id {account_id}") from None
    except (NoAccount, AccountNeedsLogin) as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


def ask[T](account: Account, call: Callable[[], T], missing: str = "Not in the catalogue") -> T:
    """Make a request to Audible through `account`, its failures as HTTP."""
    try:
        return call()
    except PastReach as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except NotFoundError:
        # Audible answers an unknown ASIN with a 200 holding nothing, but a 404 is no outage either
        raise HTTPException(status_code=404, detail=missing) from None
    except Unauthorized:
        raise HTTPException(status_code=409, detail=f"Audible refused {account.name}; sign in again") from None
    except AudibleError as error:
        logger.warning("Audible did not answer for %r", account, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Audible did not answer: {error}") from None


def marked[M: CatalogBookOut](
    country_code: str, books: list[CatalogBook], model: type[M], extra: list[dict] | None = None
) -> list[M]:
    """
    The books as `model`, each with what the accounts of the marketplace have of it:
    the libraries holding it and the wishlists it is on. `extra` adds fields per book.
    """
    found = marks(country_code, [book.asin for book in books])
    out = []
    for index, book in enumerate(books):
        mark = found[book.asin]
        update = {
            "in_library": bool(mark.library),
            "library_book_id": mark.library[0].book_id if mark.library else None,
            "library": [LibraryCopyOut.model_validate(copy) for copy in mark.library],
            "on_wishlist": mark.on_wishlist,
            **(extra[index] if extra else {}),
        }
        out.append(model.model_validate(_With(book, update)))
    return out


class _With:
    """A book's attributes with some added or replaced, for validating a response model in one go."""

    def __init__(self, book: CatalogBook, extra: dict):
        self._book = book
        self._extra = extra

    def __getattr__(self, name: str):
        return self._extra[name] if name in self._extra else getattr(self._book, name)


def router(*, catalog: Catalog | None = None) -> APIRouter:
    """The catalogue's routes, asking through `catalog` (a fresh one, with its own cache, by default)."""
    router = APIRouter()
    catalog = catalog or Catalog()

    @router.get("/catalog/search", response_model=CatalogSearchOut)
    def search_catalog(
        account_id: int | None = None,
        keywords: Term = None,
        title: Term = None,
        author: Term = None,
        narrator: Term = None,
        category_id: Annotated[str | None, Query(pattern=r"^[0-9]{1,20}$")] = None,
        sort: Sort = "relevance",
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    ) -> CatalogSearchOut:
        """
        A page of a catalogue search by any of `keywords`, `title`, `author`, `narrator`
        and `category_id` (at least one), in the account's marketplace.

        Audible pages through only so many results (`reachable`) and repeats its last
        page beyond that, so a page past them is a 422. Podcasts are left out, so a
        page can hold fewer than `page_size`; `total` is Audible's.
        """
        if not any((keywords, title, author, narrator, category_id)):
            raise HTTPException(status_code=422, detail="Search by keywords, title, author, narrator or category_id")
        account = account_for(catalog, account_id)
        found = ask(
            account,
            lambda: catalog.search(
                account,
                keywords=keywords,
                title=title,
                author=author,
                narrator=narrator,
                category_id=category_id,
                sort=sort,
                page=page,
                page_size=page_size,
            ),
        )
        # Audible's own limit was checked before asking (`PastReach`); this is the search's
        if page > 1 and (page - 1) * page_size >= found.reachable:
            raise HTTPException(status_code=422, detail=str(PastReach(found.reachable)))
        return CatalogSearchOut(
            items=marked(account.country_code, found.books, CatalogBookOut),
            total=found.total,
            reachable=found.reachable,
            page=page,
            page_size=page_size,
            account_id=account.id,
        )

    @router.get("/catalog/books/{asin}", response_model=CatalogBookDetail)
    def catalog_book(asin: Asin, account_id: int | None = None) -> CatalogBookDetail:
        """One book of the catalogue, with its description. 404 when Audible has no such book."""
        account = account_for(catalog, account_id)
        book = ask(account, lambda: catalog.book(account, asin), f"No book {asin} in the catalogue")
        if book is None:
            raise HTTPException(status_code=404, detail=f"No book {asin} in the catalogue")
        return marked(account.country_code, [book], CatalogBookDetail)[0]

    @router.get("/catalog/series", response_model=list[SeriesMatchOut])
    def find_series(
        name: Annotated[str, Query(min_length=1, max_length=200)],
        author: Term = None,
        account_id: int | None = None,
    ) -> list[SeriesMatchOut]:
        """
        The series called `name`, or whose title contains it, best first: the exact
        matches, then by how many books of a keyword search for the name are in each.
        Audible has no search by series, so this finds a series through its books;
        `author` narrows the search. Each is then read with `GET /api/catalog/series/{asin}`.
        """
        account = account_for(catalog, account_id)
        return ask(account, lambda: catalog.find_series(account, name, author))

    @router.get("/catalog/series/{asin}", response_model=CatalogSeriesOut)
    def catalog_series(asin: Asin, account_id: int | None = None) -> CatalogSeriesOut:
        """
        A series and every book Audible lists in it, in reading order. A position often
        has several books, the editions of one title. 404 when the ASIN is not a series.
        """
        account = account_for(catalog, account_id)
        series = ask(account, lambda: catalog.series(account, asin), f"No series {asin} in the catalogue")
        if series is None:
            raise HTTPException(status_code=404, detail=f"No series {asin} in the catalogue")
        books = marked(account.country_code, [entry.book for entry in series.books], CatalogBookOut)
        return CatalogSeriesOut(
            asin=series.asin,
            title=series.title,
            books=[
                SeriesBookOut(sequence=entry.sequence, book=book)
                for entry, book in zip(series.books, books, strict=True)
            ],
        )

    @router.get("/catalog/categories", response_model=list[CategoryOut])
    def catalog_categories(account_id: int | None = None) -> list[CategoryOut]:
        """The marketplace's categories, two levels deep; an id is a search's `category_id`."""
        account = account_for(catalog, account_id)
        return ask(account, lambda: catalog.categories(account))

    @router.get("/catalog/authors", response_model=list[AuthorOut])
    def catalog_authors(
        name: Annotated[str, Query(min_length=2, max_length=200)], account_id: int | None = None
    ) -> list[AuthorOut]:
        """
        Authors whose name matches, from Audible's search suggestions. Audible knows
        little more than the name; an author's books are a search by `author` name, as
        the id here can differ from the one on their books.
        """
        account = account_for(catalog, account_id)
        return ask(account, lambda: catalog.authors(account, name))

    return router
