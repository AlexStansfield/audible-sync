import pytest

import audible_sync.database as database
from audible_sync.model import CatalogBook, WishlistItem
from audible_sync.wishlist import NotInCatalogue, OwnedBook, Wishlists, read_wishlist
from tests.conftest import make_book


class FakeAuth:
    def __init__(self, blob):
        self.blob = blob

    def to_dict(self):
        return self.blob


class FakeAudible:
    """The wishlist's side of `Audible`, with Audible's own wishlist as a dict."""

    def __init__(self, account, on_audible=(), catalogue=()):
        self.auth = FakeAuth(account.auth)
        self.on_audible = dict.fromkeys(on_audible, True)
        self.catalogue = {asin: CatalogBook(asin=asin, title=f"Title {asin}") for asin in catalogue}
        self.calls = []
        self.fail = None
        self.closed = False

    def _call(self, *call):
        self.calls.append(call)
        if self.fail is not None:
            raise self.fail

    def get_wishlist(self, account_id):
        self._call("read")
        return [
            WishlistItem(
                account_id=account_id, book=CatalogBook(asin=asin, title=asin), added_at="2026-01-01T00:00:00Z"
            )
            for asin in self.on_audible
        ]

    def get_catalog_book(self, asin):
        self._call("book", asin)
        return self.catalogue.get(asin)

    def add_to_wishlist(self, asin):
        self._call("add", asin)
        self.on_audible[asin] = True

    def remove_from_wishlist(self, asin):
        self._call("remove", asin)
        self.on_audible.pop(asin, None)

    def close(self):
        self.closed = True


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()
    account_id = database.add_account("Alex (UK)", "uk", auth={"locale_code": "uk", "access_token": "Atna|t"})
    return database.get_account(account_id)


def test_reading_stores_the_wishlist(account):
    assert read_wishlist(FakeAudible(account, on_audible=["B000000001", "B000000002"]), account) is True

    assert database.wishlist_count(account_id=account.id) == 2


def test_a_read_that_fails_is_recorded_and_never_raises(account):
    database.replace_wishlist(account.id, [WishlistItem(account.id, CatalogBook(asin="B000000001", title=""))])
    fake = FakeAudible(account)
    fake.fail = RuntimeError("Audible down")

    assert read_wishlist(fake, account) is False

    assert database.wishlist_reads()[account.id]["error"] == "RuntimeError: Audible down"
    # The books stay as of the last read that worked
    assert database.wishlist_count(account_id=account.id) == 1


def test_a_read_that_cannot_be_stored_never_raises(account, monkeypatch):
    import audible_sync.wishlist as wishlist_module

    def refuse(account_id, items):
        raise database.sqlite3.OperationalError("locked")

    monkeypatch.setattr(wishlist_module, "replace_wishlist", refuse)

    assert read_wishlist(FakeAudible(account, on_audible=["B000000001"]), account) is False


def make_wishlists(fake):
    return Wishlists(client_for=lambda account: fake)


def test_adding_reads_the_book_then_tells_audible_and_the_table(account):
    fake = FakeAudible(account, catalogue=["B000000001"])

    item, added = make_wishlists(fake).add(account, "B000000001")

    assert added is True
    assert item.book.title == "Title B000000001"
    assert item.added_at.endswith("Z")
    assert [call[0] for call in fake.calls] == ["book", "add"]
    assert database.get_wishlist_item(account.id, "B000000001") is not None
    assert fake.closed


def test_adding_a_book_already_there_says_so_and_keeps_its_date(account):
    database.add_wishlist_item(
        WishlistItem(account.id, CatalogBook(asin="B000000001", title="Old"), added_at="2020-01-01T00:00:00Z")
    )
    fake = FakeAudible(account, catalogue=["B000000001"])

    item, added = make_wishlists(fake).add(account, "B000000001")

    assert added is False
    assert item.added_at == "2020-01-01T00:00:00Z"
    # Audible is still told: the table could be behind it
    assert "add" in [call[0] for call in fake.calls]


def test_a_book_the_account_owns_is_refused_without_asking_audible(account):
    database.update_books(account.id, [make_book("B000000001", id=None, account_id=None)])
    fake = FakeAudible(account, catalogue=["B000000001"])

    with pytest.raises(OwnedBook, match="owns this book"):
        make_wishlists(fake).add(account, "B000000001")

    assert fake.calls == []


def test_a_book_another_account_owns_can_still_be_added(account):
    other = database.add_account("Sam (UK)", "uk", auth={"locale_code": "uk"})
    database.update_books(other, [make_book("B000000001", id=None, account_id=None)])

    _, added = make_wishlists(FakeAudible(account, catalogue=["B000000001"])).add(account, "B000000001")

    assert added is True


def test_an_asin_audible_does_not_know_is_never_sent(account):
    fake = FakeAudible(account)

    with pytest.raises(NotInCatalogue):
        make_wishlists(fake).add(account, "B0ZZZZZZZZ")

    assert [call[0] for call in fake.calls] == ["book"]


def test_removing_tells_audible_even_when_the_table_has_no_such_book(account):
    database.add_wishlist_item(WishlistItem(account.id, CatalogBook(asin="B000000001", title="")))
    fake = FakeAudible(account)
    wishlists = make_wishlists(fake)

    assert wishlists.remove(account, "B000000001") is True
    assert wishlists.remove(account, "B000000002") is False

    assert fake.calls == [("remove", "B000000001"), ("remove", "B000000002")]


def test_a_failed_add_leaves_the_table_alone(account):
    fake = FakeAudible(account, catalogue=["B000000001"])

    def refuse(asin):
        raise RuntimeError("refused")

    fake.add_to_wishlist = refuse

    with pytest.raises(RuntimeError):
        make_wishlists(fake).add(account, "B000000001")

    assert database.get_wishlist_item(account.id, "B000000001") is None
