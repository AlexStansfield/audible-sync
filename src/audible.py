import logging

import audible

from src.model import Book

logger = logging.getLogger(__name__)

# Only the groups `_prepare_book` actually reads. Asking for more (price, rating,
# relationships, ...) adds megabytes to a full sync that are parsed and discarded.
RESPONSE_GROUPS = (
    "contributors, media, product_attrs, product_desc, series, category_ladders, is_finished, percent_complete, pdf_url"
)

# The library endpoint caps a page at 1000 items.
_PAGE_SIZE = 1000

# `audible.Client` defaults to 10 seconds, which a full page of 1000 titles with
# these response groups does not come back in: a 306-title library measured 16.5s,
# so a first sync on an empty database raised NotResponding every time and aborted
# the run. Only the incremental sync, which returns a handful of items, was fast
# enough to fit. Pages are requested one at a time, so this bounds a single page.
_API_TIMEOUT = 60


def _prepare_book(item: dict) -> Book:
    """
    Map one library item from the Audible API onto a `Book`.

    Every field is read defensively: the API omits keys for podcasts, periodicals
    and unnumbered series entries, and a single missing key used to abort the
    whole sync before anything was written.
    """
    genres = set()
    for genre in item.get("category_ladders") or []:
        for ladder in genre.get("ladder") or []:
            genres.add(ladder["name"])

    series = [{"title": entry.get("title"), "sequence": entry.get("sequence")} for entry in item.get("series") or []]

    # Get highest resolution cover (prefer 1215px, fallback to 500px)
    product_images = item.get("product_images") or {}
    cover_url = product_images.get("1215") or product_images.get("500", "")

    # Check if PDF is available
    has_pdf = bool(item.get("pdf_url"))

    data_row = {
        "asin": item["asin"],
        "title": item.get("title", ""),
        "subtitle": item.get("subtitle", ""),
        "authors": [author["name"] for author in item.get("authors") or []],
        "narrators": [narrator["name"] for narrator in item.get("narrators") or []],
        "series": series,
        "genres": list(genres),
        "length": item.get("runtime_length_min", 0),
        "is_finished": item.get("is_finished", False),
        "percent_complete": item.get("percent_complete", 0.0),
        "date_added": (item.get("library_status") or {}).get("date_added"),
        "release_date": item.get("release_date"),
        "cover_url": cover_url,
        "has_pdf": has_pdf,
    }

    return Book(**data_row)


class Audible:
    def __init__(self, auth_file: str):
        self.auth = audible.Authenticator.from_file(filename=auth_file)
        self.client = audible.Client(self.auth, timeout=_API_TIMEOUT)

    def get_library(self, purchased_after: str | None = None) -> list[Book]:
        """
        Fetch the library, following pagination until a short page comes back.

        A library larger than one page used to be silently truncated: only the
        oldest 1000 titles were ever seen, and the incremental cursor then made
        sure the rest were never fetched.
        """
        books: list[Book] = []
        page = 1

        while True:
            params = {
                "response_groups": RESPONSE_GROUPS,
                "sort_by": "PurchaseDate",
                "num_results": _PAGE_SIZE,
                "page": page,
            }
            if purchased_after is not None:
                params["purchased_after"] = purchased_after

            items = self.client.get("library", params=params).get("items") or []
            books.extend(_prepare_books(items))

            if len(items) < _PAGE_SIZE:
                return books
            page += 1

    def get_book(self, asin: str) -> Book:
        response = self.client.get(path=f"library/{asin}", params={"response_groups": RESPONSE_GROUPS})

        return _prepare_book(response["item"])


def _prepare_books(items: list[dict]) -> list[Book]:
    """
    Map a page of library items, skipping any single item that cannot be read.

    One unexpected item shape should cost that one book, not the entire run:
    sync happens before downloading, so an exception here stops books that are
    already waiting from being processed too.
    """
    books = []
    for item in items:
        try:
            books.append(_prepare_book(item))
        except (KeyError, TypeError, AttributeError):
            logger.exception("Skipping library item that could not be read: %s", item.get("asin", "unknown ASIN"))
    return books
