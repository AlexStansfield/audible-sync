import itertools
import socket
from dataclasses import replace

import pytest

from src import crypto
from src.model import Account, Book
from src.settings import Settings


@pytest.fixture(autouse=True)
def secret_key(monkeypatch, tmp_path):
    """
    Every test runs with its own secret key, configured the way an entry point would.

    The key is also put in the environment and the default key file moved under
    `tmp_path`, so a test that reaches `load_secret_key` (the entry points) neither
    reads nor writes a key file in the repository.
    """
    key = crypto.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV, key.decode())
    monkeypatch.delenv(crypto.KEY_FILE_ENV, raising=False)
    monkeypatch.setattr(crypto, "DEFAULT_KEY_FILE", tmp_path / "secrets" / "secret.key")
    crypto.configure(key)
    yield key
    crypto.configure(None)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """
    No test reaches the network. Failing at the end as well as refusing the lookup, since
    best-effort code around Amazon (`deregister_device`) would swallow the refusal alone.
    """
    attempts = []

    def refuse(host, *args, **kwargs):
        attempts.append(host)
        raise OSError(f"tests must not reach the network: {host}")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield
    assert attempts == [], f"this test tried to reach the network: {attempts}"


# Every test book gets a distinct row id, the way one read from the database would
_book_ids = itertools.count(1)

# The account the `db` fixtures create first, and the one `make_book` files under
ACCOUNT_ID = 1


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
    lists, never the JSON strings the database column stores. `id` and `account_id`
    are filled in unless given, so a book behaves like one the database returned;
    pass `id=None` for one straight from the API.
    """
    kwargs.setdefault("id", next(_book_ids))
    kwargs.setdefault("account_id", ACCOUNT_ID)
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


def make_account(id=ACCOUNT_ID, name="Test (UK)", country_code="uk", *, auth=None, **kwargs) -> Account:
    """An `Account`; `auth` defaults to a stand-in blob so it does not read as needing a login."""
    if auth is None:
        auth = {"locale_code": country_code, "access_token": "Atna|token"}
    return Account(id=id, name=name, country_code=country_code, auth=auth, **kwargs)


def make_settings(**overrides) -> Settings:
    """
    `Settings` with the shipped defaults and whatever a test needs changed.

    Built with `dataclasses.replace`, so an override that would not survive
    `Settings.from_ini` still raises here.
    """
    return replace(Settings(), **overrides)


def mark_downloaded(book_id: int, encoding_format: str | None = None, **paths) -> None:
    """
    Take a stored book to `downloaded` as a run would: claim it, then record it.

    For tests that need a downloaded book rather than to test the transition, which
    `mark_book_downloaded` only allows to the run holding the claim.
    """
    from src import database

    claim = database.claim_book_for_download(book_id)
    assert claim is not None, "the book could not be claimed"
    assert database.mark_book_downloaded(book_id, encoding_format, claim=claim, **paths)
