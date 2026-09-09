from dataclasses import replace

from src.model import Book
from src.settings import Settings


def make_book(
    asin="B001",
    title="Title",
    *,
    subtitle="",
    authors=("Author One",),
    narrators=("Narrator",),
    series=(),
    genres=("Fiction",),
    release_date="2020-05-01",
    date_added="2024-01-01T00:00:00",
    cover_url="",
    has_pdf=False,
    **kwargs,
):
    """
    A `Book` with sensible defaults for tests.

    Sequence arguments are copied into real lists: a book holds decoded Python
    lists, never the JSON strings the database column stores.
    """
    return Book(
        asin=asin,
        title=title,
        subtitle=subtitle,
        authors=list(authors),
        narrators=list(narrators),
        series=list(series or ()),
        genres=list(genres),
        release_date=release_date,
        date_added=date_added,
        cover_url=cover_url,
        has_pdf=has_pdf,
        **kwargs,
    )


def make_settings(**overrides) -> Settings:
    """
    `Settings` with the shipped defaults and whatever a test needs changed.

    Built with `dataclasses.replace`, so an override that would not survive
    `Settings.from_ini` still raises here.
    """
    return replace(Settings(), **overrides)
