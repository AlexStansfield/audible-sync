import logging

import pytest
from audible import Authenticator

import src.database as database
from src import accounts
from src.accounts import (
    AccountGone,
    authenticator_for,
    display_name,
    persist_auth_if_changed,
    replace_account_auth,
)
from src.model import Account, BookStatus
from tests.conftest import make_account, make_book

# What `Authenticator.to_dict()` looks like for a UK login; enough for from_dict
AUTH_BLOB = {
    "website_cookies": {"session-id": "abc"},
    "adp_token": "{enc:e}{key:k}{iv:i}{name:n}{serial:Mg==}",
    "access_token": "Atna|access",
    "refresh_token": "Atnr|refresh",
    "device_private_key": "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----\n",
    "store_authentication_cookie": {"cookie": "x"},
    "device_info": {"device_name": "Audible for iPhone"},
    "customer_info": {"user_id": "u1", "name": "Alex"},
    "expires": 1_800_000_000.0,
    "locale_code": "uk",
    "with_username": False,
    "activation_bytes": None,
}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()


def test_authenticator_for_rebuilds_the_stored_blob():
    auth = authenticator_for(make_account(auth=dict(AUTH_BLOB)))

    assert isinstance(auth, Authenticator)
    assert auth.locale.country_code == "uk"
    assert auth.access_token == "Atna|access"
    assert auth.to_dict() == AUTH_BLOB


def test_authenticator_for_leaves_the_accounts_blob_alone():
    """`from_dict` pops the locale out of what it is given; the account must keep its copy."""
    account = make_account(auth=dict(AUTH_BLOB))

    authenticator_for(account)

    assert account.auth == AUTH_BLOB


def test_authenticator_for_refuses_an_account_without_credentials():
    with pytest.raises(ValueError, match="no credentials"):
        authenticator_for(Account(id=1, name="Pending", country_code="uk", auth=None))


def test_display_name_is_the_customer_and_the_marketplace():
    assert display_name(Authenticator.from_dict(dict(AUTH_BLOB))) == "Alex (UK)"


def test_display_name_without_a_customer_name():
    auth = Authenticator.from_dict({**AUTH_BLOB, "customer_info": {}})

    assert display_name(auth) == "Audible (UK)"


def test_persist_auth_if_changed_writes_only_a_changed_blob(db):
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB))
    account = database.get_account(account_id)
    auth = authenticator_for(account)

    assert persist_auth_if_changed(account, auth) is False

    auth.access_token = "Atna|refreshed"
    auth.expires = 1_900_000_000.0
    assert persist_auth_if_changed(account, auth) is True
    stored = database.get_account(account_id).auth
    assert (stored["access_token"], stored["expires"]) == ("Atna|refreshed", 1_900_000_000.0)


def test_persist_auth_if_changed_leaves_credentials_a_re_login_replaced(db):
    """A run must not write its refreshed old device back over a re-login made while it ran."""
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB))
    snapshot = database.get_account(account_id)
    auth = authenticator_for(snapshot)
    database.save_account_auth(account_id, {**AUTH_BLOB, "refresh_token": "Atnr|new-device"})

    auth.access_token = "Atna|refreshed"
    assert persist_auth_if_changed(snapshot, auth) is False
    assert database.get_account(account_id).auth["refresh_token"] == "Atnr|new-device"


def test_add_account_from_authenticator_defaults_the_name(db):
    account_id = accounts.add_account_from_authenticator(Authenticator.from_dict(dict(AUTH_BLOB)))

    assert database.get_account(account_id).name == "Alex (UK)"


# --- replacing credentials -----------------------------------------------------------


class _Deregistering:
    def __init__(self, raise_error=None):
        self.calls = 0
        self.raise_error = raise_error

    def deregister_device(self):
        self.calls += 1
        if self.raise_error:
            raise self.raise_error


def test_replace_account_auth_keeps_the_account_and_stores_the_new_credentials(db, monkeypatch):
    """The recovery for a registration Audible stopped licensing: same id, same library."""
    account_id = database.add_account(
        "Alex (UK)", "uk", auth={**AUTH_BLOB, "access_token": "Atna|old"}, customer_name="Old"
    )
    database.update_books(account_id, [])
    old_device = _Deregistering()
    monkeypatch.setattr(accounts, "authenticator_for", lambda account: old_device)
    new_auth = Authenticator.from_dict({**AUTH_BLOB, "customer_info": {"name": "Alexander"}})

    replace_account_auth(database.get_account(account_id), new_auth)

    account = database.get_account(account_id)
    assert account.auth["access_token"] == "Atna|access"
    assert account.customer_name == "Alexander"
    assert account.name == "Alex (UK)"  # the user's name for it is theirs
    assert old_device.calls == 1


def test_replace_account_auth_queues_the_books_the_old_device_was_refused(db, monkeypatch, caplog):
    """A new device is exactly the moment to try the licence-refused books again."""
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB))
    other = database.add_account("Other", "us", auth=None)
    database.update_books(account_id, [make_book("B001"), make_book("B002", is_consumable=False)])
    database.update_books(other, [make_book("B001")])
    for account in (account_id, other):
        book = database.get_book_by_asin("B001", account_id=account)
        database.claim_book_for_download(book.id)
        database.mark_book_unavailable(book.id, "Denied")
    monkeypatch.setattr(accounts, "authenticator_for", lambda account: _Deregistering())

    with caplog.at_level(logging.INFO):
        replace_account_auth(database.get_account(account_id), Authenticator.from_dict(dict(AUTH_BLOB)))

    assert database.get_book_by_asin("B001", account_id=account_id).status is BookStatus.WAITING_DOWNLOAD
    # A title Audible withdrew is the sync's to restore; another account's books are not this device's
    assert database.get_book_by_asin("B002", account_id=account_id).status is BookStatus.UNAVAILABLE
    assert database.get_book_by_asin("B001", account_id=other).status is BookStatus.UNAVAILABLE
    assert "Queued 1 parked book(s)" in caplog.text


def test_replace_account_auth_refuses_an_account_deleted_while_the_login_ran(db, monkeypatch):
    """A login takes minutes; the account it was started for may be gone by the end."""
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB))
    stale = database.get_account(account_id)
    database.delete_account(account_id)
    monkeypatch.setattr(accounts, "authenticator_for", lambda account: pytest.fail("nothing to deregister"))

    with pytest.raises(AccountGone, match="was removed while the login was in progress"):
        replace_account_auth(stale, Authenticator.from_dict(dict(AUTH_BLOB)))

    assert database.get_accounts() == []


def test_replace_account_auth_refuses_when_the_account_goes_before_the_write(db, monkeypatch):
    account_id = database.add_account("Alex (UK)", "uk", auth=None)
    monkeypatch.setattr(accounts, "save_account_auth", lambda account_id, auth: False)

    with pytest.raises(AccountGone):
        replace_account_auth(database.get_account(account_id), Authenticator.from_dict(dict(AUTH_BLOB)))


def test_replace_account_auth_deregisters_the_device_stored_now_not_the_one_read_earlier(db, monkeypatch):
    """Another login may have replaced the credentials since this one started."""
    account_id = database.add_account("Alex (UK)", "uk", auth={**AUTH_BLOB, "access_token": "Atna|first"})
    stale = database.get_account(account_id)
    database.save_account_auth(account_id, {**AUTH_BLOB, "access_token": "Atna|second"})
    seen = []
    monkeypatch.setattr(
        accounts, "authenticator_for", lambda account: seen.append(account.auth["access_token"]) or _Deregistering()
    )

    replace_account_auth(stale, Authenticator.from_dict(dict(AUTH_BLOB)))

    assert seen == ["Atna|second"]


def test_replace_account_auth_goes_ahead_when_the_old_device_cannot_deregister(db, monkeypatch, caplog):
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB))
    monkeypatch.setattr(accounts, "authenticator_for", lambda account: _Deregistering(RuntimeError("throttled")))

    replace_account_auth(
        database.get_account(account_id), Authenticator.from_dict({**AUTH_BLOB, "access_token": "Atna|new"})
    )

    assert database.get_account(account_id).auth["access_token"] == "Atna|new"
    assert "Could not deregister the previous device" in caplog.text


def test_replace_account_auth_can_skip_deregistering(db, monkeypatch):
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB))
    monkeypatch.setattr(accounts, "authenticator_for", lambda account: pytest.fail("must not deregister"))

    replace_account_auth(
        database.get_account(account_id), Authenticator.from_dict(dict(AUTH_BLOB)), deregister_previous=False
    )


def test_replace_account_auth_on_an_account_that_had_none(db, monkeypatch):
    account_id = database.add_account("Pending", "", auth=None)
    monkeypatch.setattr(accounts, "authenticator_for", lambda account: pytest.fail("nothing to deregister"))

    replace_account_auth(database.get_account(account_id), Authenticator.from_dict(dict(AUTH_BLOB)))

    account = database.get_account(account_id)
    assert account.needs_login is False
    assert account.country_code == "uk"


def test_replace_account_auth_refuses_another_marketplaces_login(db, monkeypatch):
    account_id = database.add_account("Alex (US)", "us", auth=dict(AUTH_BLOB))
    monkeypatch.setattr(accounts, "authenticator_for", lambda account: pytest.fail("nothing is deregistered"))

    with pytest.raises(ValueError, match="US account"):
        replace_account_auth(database.get_account(account_id), Authenticator.from_dict(dict(AUTH_BLOB)))

    account = database.get_account(account_id)
    assert account.country_code == "us"
    assert account.auth == AUTH_BLOB


@pytest.mark.parametrize(
    ("account_country", "login_country", "accepted"),
    [("uk", "uk", True), ("UK", "uk", True), ("", "us", True), ("uk", "us", False)],
)
def test_check_marketplace(account_country, login_country, accepted):
    account = make_account(country_code=account_country)
    if accepted:
        accounts.check_marketplace(account, login_country)
    else:
        with pytest.raises(ValueError):
            accounts.check_marketplace(account, login_country)
