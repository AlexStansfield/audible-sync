import logging

import audible

from audible_sync.audible_api import products
from audible_sync.model import Book, CatalogBook, CatalogSeries, SeriesBook, WishlistItem, sort_series

logger = logging.getLogger(__name__)

# Only the groups `_prepare_book` actually reads. Asking for more (price, rating,
# relationships, ...) adds megabytes to a full sync that are parsed and discarded.
# `product_extended_attrs` is the description (`publisher_summary`): it makes a page
# about 45% larger, and `product_desc` despite its name only repeats the title.
RESPONSE_GROUPS = (
    "contributors, media, product_attrs, product_desc, product_extended_attrs, series, category_ladders, "
    "is_finished, percent_complete, pdf_url, customer_rights"
)

# The library endpoint caps a page at 1000 items.
_PAGE_SIZE = 1000

# `audible.Client` defaults to 10 seconds, which a full page of 1000 titles with
# these response groups does not come back in: a 306-title library measured 16.5s,
# so a first sync on an empty database raised NotResponding every time and aborted
# the run. Only the incremental sync, which returns a handful of items, was fast
# enough to fit. Pages are requested one at a time, so this bounds a single page.
_API_TIMEOUT = 60

# 5,000 books: far beyond any wishlist seen, and a bound on a read that would not end
_MAX_WISHLIST_PAGES = 100


def _is_podcast(item: dict) -> bool:
    """
    Whether a library item is a podcast rather than a book.

    Following a show puts it in the library listing as one item, with no length and no
    audio of its own (`PodcastParent`); its episodes (`PodcastEpisode`) only come back
    when asked for with `parent_asin`. This matches the podcast values rather than
    allowing only `content_type` `Product`, since real books can be `Performance`.
    """
    return item.get("content_type") == "Podcast" or (item.get("content_delivery_type") or "").startswith("Podcast")


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

    # `series_asin` is the series' own ASIN, which identifies it independently of a
    # title Audible has demonstrated it can typo ("His Dark Materialsik"). Sorted on
    # the way in so the stored order is canonical rather than whatever the response
    # happened to use; see `sort_series`.
    series = sort_series(
        [
            {
                "title": entry.get("title"),
                "sequence": entry.get("sequence"),
                "series_asin": entry.get("asin"),
            }
            for entry in item.get("series") or []
        ]
    )

    # Get highest resolution cover (prefer 1215px, fallback to 500px)
    product_images = item.get("product_images") or {}
    cover_url = product_images.get("1215") or product_images.get("500", "")

    # Check if PDF is available
    has_pdf = bool(item.get("pdf_url"))

    # A Plus ("AYCL") title the customer added while it was included stays in the library
    # after Audible withdraws it, but stops being licensable until it is offered again.
    # Default True so a missing response group cannot park the whole library as
    # unavailable: the licence request is still the thing that decides.
    is_consumable = (item.get("customer_rights") or {}).get("is_consumable", True)

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
        "is_consumable": is_consumable,
        # HTML; cleaned where it is shown or tagged (`html_to_text`), so a change to the
        # cleaning applies without reading the library again
        "description": item.get("publisher_summary") or None,
    }

    return Book(**data_row)


class Audible:
    """One marketplace's API, bound to the authenticator (and so the locale) it is given."""

    def __init__(self, auth: audible.Authenticator):
        self.auth = auth
        self.client = audible.Client(self.auth, timeout=_API_TIMEOUT)

    def close(self) -> None:
        """Close the HTTP session; for a client made for one request rather than a run."""
        self.client.close()

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

    def is_podcast(self, asin: str) -> bool:
        """
        Whether Audible says this ASIN is a podcast. It still does for a show that has
        been unfollowed, which the library listing no longer returns.
        """
        response = self.client.get(path=f"library/{asin}", params={"response_groups": "product_attrs"})
        return _is_podcast(response.get("item") or {})

    # The catalogue. It is the marketplace's whole range, not the customer's library;
    # it answers without credentials too, but only a signed-in request is told the
    # member's price

    def search_catalog(
        self,
        *,
        keywords: str | None = None,
        title: str | None = None,
        author: str | None = None,
        narrator: str | None = None,
        category_id: str | None = None,
        sort: str = "relevance",
        page: int = 1,
        page_size: int = products.MAX_PAGE_SIZE,
    ) -> tuple[list[CatalogBook], int]:
        """
        One page of a catalogue search and Audible's count of everything it matched;
        `page` counts from 1.

        Podcasts are left out of the page, so a page can hold fewer books than
        `page_size`; the total is Audible's, podcasts included.

        Raises:
            ValueError: for a sort not in `products.SORTS`
        """
        if sort not in products.SORTS:
            raise ValueError(f"sort must be one of {', '.join(products.SORTS)}, got {sort!r}")
        params = {
            "response_groups": products.SEARCH_RESPONSE_GROUPS,
            "image_sizes": products.IMAGE_SIZES,
            "num_results": min(page_size, products.MAX_PAGE_SIZE),
            # The catalogue counts its pages from 0, unlike the library (checked 2026-11-12)
            "page": page - 1,
        }
        if products.SORTS[sort] is not None:
            params["products_sort_by"] = products.SORTS[sort]
        filters = {"keywords": keywords, "title": title, "author": author, "narrator": narrator}
        params.update({key: value for key, value in filters.items() if value})
        if category_id:
            params["category_id"] = category_id
        answer = self.client.get("catalog/products", params=params)
        return products.to_catalog_books(answer.get("products") or []), int(answer.get("total_results") or 0)

    def get_catalog_book(self, asin: str) -> CatalogBook | None:
        """One book of the catalogue with its description; None when Audible has no such book."""
        answer = self.client.get(
            f"catalog/products/{asin}",
            params={"response_groups": products.BOOK_RESPONSE_GROUPS, "image_sizes": products.IMAGE_SIZES},
        )
        books = products.to_catalog_books([answer.get("product") or {}])
        return books[0] if books else None

    def get_catalog_books(self, asins: list[str]) -> list[CatalogBook]:
        """
        The books of these ASINs, in fifties (Audible's limit), leaving out any it does
        not know. Never asks with none: `asins=` empty answers with arbitrary products.
        """
        books: list[CatalogBook] = []
        for start in range(0, len(asins), products.MAX_PAGE_SIZE):
            chunk = asins[start : start + products.MAX_PAGE_SIZE]
            answer = self.client.get(
                "catalog/products",
                params={
                    "asins": ",".join(chunk),
                    "response_groups": products.SEARCH_RESPONSE_GROUPS,
                    "image_sizes": products.IMAGE_SIZES,
                },
            )
            books.extend(products.to_catalog_books(answer.get("products") or []))
        return books

    def get_series(self, asin: str) -> CatalogSeries | None:
        """
        A series and every book it lists, in reading order; None when the ASIN is not
        a series Audible knows.
        """
        answer = self.client.get(
            f"catalog/products/{asin}", params={"response_groups": "product_desc,product_attrs,relationships"}
        )
        product = answer.get("product") or {}
        if not products.exists(product) or not products.is_series(product):
            return None
        children = products.series_children(product)
        found = {book.asin: book for book in self.get_catalog_books([child[0] for child in children])}
        return CatalogSeries(
            asin=asin,
            title=product.get("title") or "",
            books=[
                SeriesBook(sequence=sequence, book=found[child_asin])
                for child_asin, sequence, _ in children
                if child_asin in found
            ],
        )

    def get_categories(self) -> list[dict]:
        """The catalogue's categories, two levels deep, as {id, name, children}."""
        return products.categories(self.client.get("catalog/categories"))

    # The wishlist: one per marketplace login

    def get_wishlist(self, account_id: int) -> list[WishlistItem]:
        """
        Every book on the wishlist, newest first, in fifties (Audible's limit), with the
        time each was added. Like the catalogue, it counts its pages from 0. Stops at
        Audible's total, at a page that only repeats books already read, and at
        `_MAX_WISHLIST_PAGES` whatever happens.
        """
        items: list[WishlistItem] = []
        seen: set[str] = set()
        page = 0
        while True:
            answer = self.client.get(
                "wishlist",
                params={
                    "num_results": products.MAX_PAGE_SIZE,
                    "page": page,
                    "sort_by": "-DateAdded",
                    "response_groups": products.SEARCH_RESPONSE_GROUPS,
                    "image_sizes": products.IMAGE_SIZES,
                },
            )
            page_products = answer.get("products") or []
            asins = {product.get("asin") for product in page_products}
            # The catalogue answers a page past its reach with its last page again, rather
            # than an empty one; the wishlist may too, and a loop waiting for a short page
            # would never end
            if asins and asins <= seen:
                logger.warning("The wishlist repeated a page at page %d; stopping with %d books", page, len(items))
                return items
            seen |= asins
            added = {product.get("asin"): product.get("added_timestamp") for product in page_products}
            items.extend(
                WishlistItem(account_id=account_id, book=book, added_at=added.get(book.asin))
                for book in products.to_catalog_books(page_products)
            )
            total = answer.get("total_results")
            if len(page_products) < products.MAX_PAGE_SIZE or (isinstance(total, int) and len(seen) >= total):
                return items
            page += 1
            if page >= _MAX_WISHLIST_PAGES:
                logger.warning("Stopped reading the wishlist at %d pages (%d books)", page, len(items))
                return items

    def add_to_wishlist(self, asin: str) -> None:
        """
        Put a book on the wishlist. Audible answers the same whether it was already
        there or not (checked 2026-11-06), so the caller says which from its own table.
        """
        self.client.post("wishlist", body={"asin": asin})

    def remove_from_wishlist(self, asin: str) -> None:
        """Take a book off the wishlist; as silent as adding when it was not there."""
        self.client.delete(f"wishlist/{asin}")

    def search_authors(self, name: str) -> list[dict[str, str | None]]:
        """
        Authors whose name matches, from Audible's search suggestions: ASIN, name and
        picture. The ASIN can differ from the one the same author has on their books.
        """
        # Without `site_variant` the endpoint answers 400
        answer = self.client.get("searchsuggestions", params={"key_strokes": name, "site_variant": "desktop"})
        return products.author_suggestions(answer)


def _prepare_books(items: list[dict]) -> list[Book]:
    """
    Map a page of library items, leaving out podcasts and skipping any single item that
    cannot be read.

    One unexpected item shape should cost that one book, not the entire run:
    sync happens before downloading, so an exception here stops books that are
    already waiting from being processed too.
    """
    books = []
    for item in items:
        try:
            if _is_podcast(item):
                logger.debug("Leaving out podcast %s", item.get("asin", "unknown ASIN"))
                continue
            books.append(_prepare_book(item))
        except (KeyError, TypeError, AttributeError):
            logger.exception("Skipping library item that could not be read: %s", item.get("asin", "unknown ASIN"))
    return books
