import pytest

from audible_sync.audible_api import products
from audible_sync.audible_api.client import Audible
from audible_sync.model import CatalogAuthor, CatalogPrice


def make_product(asin="B06XNYQJTX", **overrides):
    """A catalogue product shaped the way Audible returned Old Man's War (2026-11-12), trimmed."""
    product = {
        "asin": asin,
        "title": "Old Man's War",
        "subtitle": "Old Man's War, Book 1",
        "content_delivery_type": "SinglePartBook",
        "content_type": "Product",
        "authors": [{"asin": "B001IGJOCA", "name": "John Scalzi"}],
        "narrators": [{"name": "William Dufris"}],
        "series": [
            {"asin": "B077ZHN611", "sequence": "12", "title": "Omnibus"},
            {"asin": "B00HNXRO2O", "sequence": "1", "title": "Old Man\N{RIGHT SINGLE QUOTATION MARK}s War"},
        ],
        "category_ladders": [
            {"ladder": [{"id": "1", "name": "Science Fiction & Fantasy"}, {"id": "2", "name": "Science Fiction"}]},
            {"ladder": [{"id": "1", "name": "Science Fiction & Fantasy"}, {"id": "3", "name": "Military"}]},
        ],
        "runtime_length_min": 595,
        "release_date": "2017-03-21",
        "language": "english",
        "publisher_name": "Macmillan Digital Audio",
        "format_type": "unabridged",
        "product_images": {"500": "https://img/500.jpg", "1215": "https://img/1215.jpg"},
        "plans": [{"plan_name": "AccessViaMusic"}],
        "price": {
            "credit_price": 1.0,
            "list_price": {"base": 8.609999656677246, "currency_code": "GBP", "type": "list"},
            "lowest_price": {"base": 6.489999771118164, "currency_code": "GBP", "type": "member"},
        },
        "rating": {"overall_distribution": {"average_rating": 4.473978494623656, "num_ratings": 2325}},
    }
    product.update(overrides)
    return product


def test_maps_a_complete_product():
    book = products.to_catalog_book(make_product(publisher_summary="<p>Old <b>soldiers</b>.</p>"))

    assert book.asin == "B06XNYQJTX"
    assert book.authors == [CatalogAuthor(name="John Scalzi", asin="B001IGJOCA")]
    assert book.narrators == ["William Dufris"]
    assert book.genres == ["Science Fiction & Fantasy", "Science Fiction", "Military"]
    assert book.length_seconds == 595 * 60
    assert book.language == "en"
    assert book.publisher == "Macmillan Digital Audio"
    assert book.abridged is False
    assert book.cover_url == "https://img/1215.jpg"
    assert book.rating == 4.47
    assert book.rating_count == 2325
    assert book.price == CatalogPrice(currency="GBP", list_price=8.61, member_price=6.49, credits=1.0)
    assert book.plus is False
    assert book.description_text == "Old soldiers."


def test_orders_the_series_by_the_library_rule():
    book = products.to_catalog_book(make_product())

    # The lowest position first, whatever order Audible used
    assert [entry["series_asin"] for entry in book.series] == ["B00HNXRO2O", "B077ZHN611"]
    assert book.primary_series["sequence"] == "1"


def test_a_price_with_no_member_price_says_none():
    # A request without credentials is told the list price as the lowest
    price = {
        "list_price": {"base": 8.61, "currency_code": "GBP", "type": "list"},
        "lowest_price": {"base": 8.61, "currency_code": "GBP", "type": "list"},
    }

    book = products.to_catalog_book(make_product(price=price))

    assert book.price.member_price is None
    assert book.price.list_price == 8.61


@pytest.mark.parametrize("plan", ["Audible-AYCL", "US Minerva"])
def test_a_plus_plan_marks_the_book_plus(plan):
    book = products.to_catalog_book(make_product(plans=[{"plan_name": "AccessViaMusic"}, {"plan_name": plan}]))

    assert book.plus is True


@pytest.mark.parametrize("missing", ["price", "rating", "runtime_length_min", "language", "format_type", "plans"])
def test_tolerates_a_missing_key(missing):
    product = make_product()
    del product[missing]

    book = products.to_catalog_book(product)

    assert book.asin == "B06XNYQJTX"


def test_an_unknown_language_has_no_code():
    assert products.to_catalog_book(make_product(language="klingon")).language is None


def test_abridged_is_read_from_the_format():
    assert products.to_catalog_book(make_product(format_type="abridged")).abridged is True


@pytest.mark.parametrize(
    "product",
    [
        {"asin": "B0ZZZZZZZZ"},  # an unknown ASIN: a 200 holding only the ASIN
        make_product(content_delivery_type="PodcastEpisode"),
        make_product(content_type="Podcast", content_delivery_type="PodcastParent"),
        make_product(content_delivery_type="BookSeries"),
    ],
)
def test_a_page_leaves_out_what_is_no_book(product):
    assert products.to_catalog_books([product, make_product("B000000001")])[0].asin == "B000000001"


def test_a_page_skips_only_the_product_it_cannot_read(caplog):
    books = products.to_catalog_books([make_product("B000000001", authors="nonsense"), make_product("B000000002")])

    assert [book.asin for book in books] == ["B000000002"]
    assert "could not be read" in caplog.text


def test_series_children_are_in_reading_order():
    product = {
        "relationships": [
            {
                "relationship_to_product": "child",
                "relationship_type": "series",
                "asin": "B3",
                "sequence": "4.5",
                "sort": "5",
            },
            {
                "relationship_to_product": "child",
                "relationship_type": "series",
                "asin": "B1",
                "sequence": "1",
                "sort": "1",
            },
            {"relationship_to_product": "parent", "relationship_type": "series", "asin": "B9", "sequence": "1"},
            {"relationship_to_product": "child", "relationship_type": "series", "asin": "B2", "sequence": "4"},
        ]
    }

    assert products.series_children(product) == [("B1", "1", 1.0), ("B2", "4", 4.0), ("B3", "4.5", 5.0)]


@pytest.mark.parametrize("credits", ["1", {"amount": 1}, True])
def test_a_credit_price_that_is_no_number_is_none(credits):
    price = {"credit_price": credits, "list_price": {"base": 8.61, "currency_code": "GBP"}}

    # Left as it came, it failed the response model and so the whole page
    assert products.to_catalog_book(make_product(price=price)).price.credits is None


def test_author_suggestions_keep_only_the_authors():
    answer = {
        "model": {
            "items": [
                {"model": {"__component_type": "AsinRow", "asin": "B098G4PVZ6"}},
                {
                    "model": {
                        "__component_type": "AuthorItemV2",
                        "person_metadata": {
                            "asin": "B001IGJOCA",
                            "name": {"value": "John Scalzi"},
                            "profile_image": {"url": "https://img/a.jpg"},
                        },
                    }
                },
                {"model": {"__component_type": "AuthorItemV2", "person_metadata": {"name": {"value": "No id"}}}},
            ]
        }
    }

    assert products.author_suggestions(answer) == [
        {"asin": "B001IGJOCA", "name": "John Scalzi", "image_url": "https://img/a.jpg"}
    ]


def test_categories_keep_the_tree():
    answer = {"categories": [{"id": "1", "name": "Fiction", "children": [{"id": "2", "name": "Crime"}]}]}

    assert products.categories(answer) == [
        {"id": "1", "name": "Fiction", "children": [{"id": "2", "name": "Crime", "children": []}]}
    ]


# --- the client's catalogue calls -----------------------------------------------


class FakeClient:
    """Answers each path from `answers`, a dict of path to answer or to a function of the params."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def get(self, path, params=None, **kwargs):
        self.calls.append((path, params))
        answer = self.answers[path]
        return answer(params) if callable(answer) else answer


def make_audible(answers):
    audible = Audible.__new__(Audible)
    audible.client = FakeClient(answers)
    return audible


def test_search_sends_only_the_filters_given():
    audible = make_audible({"catalog/products": {"products": [make_product()], "total_results": 28}})

    books, total = audible.search_catalog(author="John Scalzi", sort="newest", page=2, page_size=20)

    assert [book.asin for book in books] == ["B06XNYQJTX"]
    assert total == 28
    params = audible.client.calls[0][1]
    assert params["author"] == "John Scalzi"
    assert params["products_sort_by"] == "-ReleaseDate"
    # Audible counts its pages from 0
    assert (params["page"], params["num_results"]) == (1, 20)
    assert not {"keywords", "title", "narrator", "category_id"} & set(params)


def test_search_by_relevance_sends_no_sort():
    audible = make_audible({"catalog/products": {"products": [], "total_results": 0}})

    audible.search_catalog(keywords="Scalzi")

    # Asking for `Relevance` by name leaves results out
    assert "products_sort_by" not in audible.client.calls[0][1]
    assert audible.client.calls[0][1]["page"] == 0


def test_search_refuses_an_unknown_sort():
    with pytest.raises(ValueError, match="sort must be one of"):
        make_audible({}).search_catalog(keywords="x", sort="price")


def test_a_book_audible_does_not_know_is_none():
    audible = make_audible({"catalog/products/B0ZZZZZZZZ": {"product": {"asin": "B0ZZZZZZZZ"}}})

    assert audible.get_catalog_book("B0ZZZZZZZZ") is None


def test_books_by_asin_are_asked_in_fifties_and_never_with_none():
    def answer(params):
        return {"products": [make_product(asin) for asin in params["asins"].split(",")]}

    audible = make_audible({"catalog/products": answer})

    assert audible.get_catalog_books([]) == []
    assert audible.client.calls == []

    asins = [f"B{n:09d}" for n in range(120)]
    books = audible.get_catalog_books(asins)

    assert [book.asin for book in books] == asins
    assert [len(params["asins"].split(",")) for _, params in audible.client.calls] == [50, 50, 20]


def test_a_series_lists_its_books_in_order_with_their_positions():
    series = {
        "asin": "B00HNXRO2O",
        "title": "Old Man\N{RIGHT SINGLE QUOTATION MARK}s War",
        "content_delivery_type": "BookSeries",
        "relationships": [
            {"relationship_to_product": "child", "relationship_type": "series", "asin": "B000000002", "sequence": "2"},
            {"relationship_to_product": "child", "relationship_type": "series", "asin": "B000000001", "sequence": "1"},
            # Audible no longer knows this one: left out
            {"relationship_to_product": "child", "relationship_type": "series", "asin": "B000000003", "sequence": "3"},
        ],
    }

    def answer(params):
        return {"products": [make_product(asin) for asin in params["asins"].split(",") if asin != "B000000003"]}

    audible = make_audible({"catalog/products/B00HNXRO2O": {"product": series}, "catalog/products": answer})

    found = audible.get_series("B00HNXRO2O")

    assert found.title == "Old Man\N{RIGHT SINGLE QUOTATION MARK}s War"
    assert [(entry.sequence, entry.book.asin) for entry in found.books] == [("1", "B000000001"), ("2", "B000000002")]


def test_a_book_is_not_a_series():
    audible = make_audible({"catalog/products/B06XNYQJTX": {"product": make_product()}})

    assert audible.get_series("B06XNYQJTX") is None


def test_author_search_names_the_site():
    audible = make_audible({"searchsuggestions": {"model": {"items": []}}})

    assert audible.search_authors("Scalzi") == []
    # Audible answers 400 without it
    assert audible.client.calls[0][1] == {"key_strokes": "Scalzi", "site_variant": "desktop"}
