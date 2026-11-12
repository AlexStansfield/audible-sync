"""
The Audible catalogue: what its search and product answers are mapped to.

The calls themselves are methods of `audible_api.client.Audible`; this module holds
what they ask for and how an answer becomes a `CatalogBook`. Everything is read
defensively, as the library's mapping is: the API is undocumented and leaves keys out
for whatever it likes.
"""

import logging
from typing import Any

from audible_sync.model import CatalogAuthor, CatalogBook, CatalogPrice, sort_series

logger = logging.getLogger(__name__)

# A search result: everything a list shows. The description (`product_extended_attrs`)
# makes an answer about 45% larger, so only a single book asks for it.
SEARCH_RESPONSE_GROUPS = (
    "contributors,media,price,product_attrs,product_desc,product_plans,rating,series,category_ladders"
)
BOOK_RESPONSE_GROUPS = SEARCH_RESPONSE_GROUPS + ",product_extended_attrs"
IMAGE_SIZES = "500,1215"

# A page of search results, and a lookup by ASIN, are capped at 50 by Audible
MAX_PAGE_SIZE = 50

# Our names for the sorts, and Audible's. Checked 2026-11-12; `-AvgRating`, `Price`
# and `Length` are refused. Relevance is no sort at all: asking for `Relevance` by
# name leaves results out (72 of "Scalzi"'s 138), the order without one does not
SORTS: dict[str, str | None] = {
    "relevance": None,
    "bestsellers": "BestSellers",
    "newest": "-ReleaseDate",
    "oldest": "ReleaseDate",
    "title": "Title",
}

# Audible clamps deep pages without an error, answering with the last page it allows
# again (measured 2026-11-03): about 1,000 results sorted by release date and 5,000 by
# bestsellers. The others were not measured, so they get the smaller limit
_REACHABLE = {"bestsellers": 5000}
_REACHABLE_DEFAULT = 1000

# The plan a Plus title carries, which differs by marketplace: `Audible-AYCL` in the
# UK, `US Minerva` in the US (checked 2026-11-03 and 2026-11-12). `AccessViaMusic` is
# on every title and means nothing here
PLUS_PLANS = frozenset({"Audible-AYCL", "US Minerva"})

# Audible names a language by a word; the marketplaces' common ones as ISO 639-1
_LANGUAGES = {
    "english": "en",
    "german": "de",
    "french": "fr",
    "spanish": "es",
    "italian": "it",
    "japanese": "ja",
    "portuguese": "pt",
    "dutch": "nl",
    "danish": "da",
    "swedish": "sv",
    "norwegian": "no",
    "finnish": "fi",
    "polish": "pl",
    "russian": "ru",
    "turkish": "tr",
    "hindi": "hi",
    "arabic": "ar",
    "hebrew": "he",
    "korean": "ko",
    "mandarin_chinese": "zh",
    "chinese": "zh",
    "catalan": "ca",
    "greek": "el",
    "czech": "cs",
    "hungarian": "hu",
    "romanian": "ro",
    "ukrainian": "uk",
    "welsh": "cy",
    "irish": "ga",
    "afrikaans": "af",
    "latin": "la",
}


def reachable_results(sort: str) -> int:
    """How many results of a search sorted this way Audible will page through."""
    return _REACHABLE.get(sort, _REACHABLE_DEFAULT)


def is_podcast(product: dict) -> bool:
    """Whether a product is a podcast (a show or an episode), which is no book to list."""
    return product.get("content_type") == "Podcast" or (product.get("content_delivery_type") or "").startswith(
        "Podcast"
    )


def is_series(product: dict) -> bool:
    """Whether a product is a series' own entry rather than a book of it."""
    return product.get("content_delivery_type") == "BookSeries"


def exists(product: dict) -> bool:
    """
    Whether Audible knows the product. An unknown ASIN is not an error: the answer is a
    200 holding only the ASIN back.
    """
    return bool(product.get("title"))


def language_code(word: str | None) -> str | None:
    """Audible's word for a language ("english") as ISO 639-1 ("en"); None when unknown."""
    if not word:
        return None
    code = _LANGUAGES.get(word.strip().lower())
    if code is None:
        logger.debug("No language code for %r", word)
    return code


def _price(product: dict) -> CatalogPrice | None:
    price = product.get("price") or {}
    list_price = price.get("list_price") or {}
    lowest = price.get("lowest_price") or {}
    currency = list_price.get("currency_code") or lowest.get("currency_code")
    if not currency:
        return None
    # `lowest_price` is the member's price only to a signed-in request, and the list
    # price again otherwise
    member = lowest.get("base") if lowest.get("type") == "member" else None
    return CatalogPrice(
        currency=currency,
        list_price=_money(list_price.get("base")),
        member_price=_money(member),
        credits=_number(price.get("credit_price")),
    )


def _number(value: Any) -> float | None:
    # A bool is an int to Python, and no price
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _money(value: Any) -> float | None:
    # Audible sends floats with their binary noise (8.609999656677246)
    number = _number(value)
    return round(number, 2) if number is not None else None


def _rating(product: dict) -> tuple[float | None, int]:
    overall = (product.get("rating") or {}).get("overall_distribution") or {}
    count = overall.get("num_ratings") or 0
    average = overall.get("average_rating")
    if not count or not isinstance(average, int | float):
        return None, 0
    return round(float(average), 2), int(count)


def _genres(product: dict) -> list[str]:
    seen: dict[str, None] = {}
    for genre in product.get("category_ladders") or []:
        for rung in genre.get("ladder") or []:
            if rung.get("name"):
                seen.setdefault(rung["name"], None)
    return list(seen)


def _abridged(format_type: str | None) -> bool | None:
    if not format_type:
        return None
    return format_type.strip().lower() == "abridged"


def to_catalog_book(product: dict) -> CatalogBook:
    """
    Map one catalogue product onto a `CatalogBook`.

    Raises:
        KeyError: for a product with no ASIN
    """
    images = product.get("product_images") or {}
    runtime = product.get("runtime_length_min")
    rating, rating_count = _rating(product)
    return CatalogBook(
        asin=product["asin"],
        title=product.get("title") or "",
        subtitle=product.get("subtitle") or "",
        authors=[
            CatalogAuthor(name=author["name"], asin=author.get("asin") or None)
            for author in product.get("authors") or []
            if author.get("name")
        ],
        narrators=[narrator["name"] for narrator in product.get("narrators") or [] if narrator.get("name")],
        series=sort_series(
            [
                {"title": entry.get("title"), "sequence": entry.get("sequence"), "series_asin": entry.get("asin")}
                for entry in product.get("series") or []
            ]
        ),
        genres=_genres(product),
        length_seconds=runtime * 60 if isinstance(runtime, int) and runtime > 0 else None,
        release_date=product.get("release_date") or None,
        language=language_code(product.get("language")),
        publisher=product.get("publisher_name") or None,
        abridged=_abridged(product.get("format_type")),
        cover_url=images.get("1215") or images.get("500") or None,
        rating=rating,
        rating_count=rating_count,
        price=_price(product),
        plus=any(plan.get("plan_name") in PLUS_PLANS for plan in product.get("plans") or []),
        multipart=product.get("content_delivery_type") == "MultiPartBook",
        description=product.get("publisher_summary") or None,
    )


def to_catalog_books(products: list[dict]) -> list[CatalogBook]:
    """
    Map a page of products, leaving out podcasts, series entries and unknown ASINs, and
    skipping one that cannot be read rather than failing the page.
    """
    books = []
    for product in products:
        if is_podcast(product) or is_series(product) or not exists(product):
            continue
        try:
            books.append(to_catalog_book(product))
        except (KeyError, TypeError, AttributeError, ValueError):
            logger.exception("Skipping catalogue product that could not be read: %s", product.get("asin"))
    return books


def series_children(product: dict) -> list[tuple[str, str | None, float]]:
    """
    The books a series product lists, as (ASIN, sequence, sort): the relationships it
    holds as their parent. `sort` orders them, and falls back to the sequence.
    """
    children = []
    for relation in product.get("relationships") or []:
        if relation.get("relationship_to_product") != "child" or relation.get("relationship_type") != "series":
            continue
        asin = relation.get("asin")
        if not asin:
            continue
        sequence = relation.get("sequence") or None
        children.append((asin, sequence, _order(relation.get("sort"), sequence)))
    return sorted(children, key=lambda child: (child[2], child[0]))


def _order(*values: Any) -> float:
    for value in values:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return float("inf")


def author_suggestions(answer: dict) -> list[dict[str, str | None]]:
    """
    The authors in a `searchsuggestions` answer, as ASIN, name and picture.

    The answer is a list of rows of mixed kinds (titles, authors, collections); the
    authors are the `AuthorItemV2` ones, each with a `person_metadata`.
    """
    authors = []
    for item in (answer.get("model") or {}).get("items") or []:
        model = item.get("model") or {}
        if model.get("__component_type") != "AuthorItemV2":
            continue
        person = model.get("person_metadata") or {}
        name = person.get("name")
        if isinstance(name, dict):
            name = name.get("value")
        if not person.get("asin") or not name:
            continue
        image = person.get("profile_image") or {}
        authors.append({"asin": person["asin"], "name": name, "image_url": image.get("url") or None})
    return authors


def categories(answer: dict) -> list[dict]:
    """The category tree as {id, name, children}, each level as Audible orders it."""

    def node(entry: dict) -> dict:
        return {
            "id": entry["id"],
            "name": entry.get("name") or "",
            "children": [node(child) for child in entry.get("children") or [] if child.get("id")],
        }

    return [node(entry) for entry in answer.get("categories") or [] if entry.get("id")]
