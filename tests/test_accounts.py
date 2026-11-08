import logging
import sqlite3
import threading
import time

import pytest
from audible import Authenticator

import src.database as database
from src import accounts, crypto
from src.accounts import (
    AccountGone,
    AnotherCustomer,
    LoginAlreadyHeld,
    authenticator_for,
    display_name,
    persist_auth_if_changed,
    replace_account_auth,
)
from src.model import Account, BookStatus, StoredLogin
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
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
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
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
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
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
    other = database.add_account("Other", "us", auth=None)
    database.update_books(account_id, [make_book("B001"), make_book("B002", is_consumable=False)])
    database.update_books(other, [make_book("B001")])
    for account in (account_id, other):
        book = database.get_book_by_asin("B001", account_id=account)
        claim = database.claim_book_for_download(book.id)
        database.mark_book_unavailable(book.id, "Denied", claim=claim)
    monkeypatch.setattr(accounts, "authenticator_for", lambda account: _Deregistering())

    with caplog.at_level(logging.INFO):
        stored = replace_account_auth(database.get_account(account_id), Authenticator.from_dict(dict(AUTH_BLOB)))

    assert stored == StoredLogin(account_id, replaced=True, requeued=1)
    assert database.get_book_by_asin("B001", account_id=account_id).status is BookStatus.WAITING_DOWNLOAD
    # A title Audible withdrew is the sync's to restore; another account's books are not this device's
    assert database.get_book_by_asin("B002", account_id=account_id).status is BookStatus.UNAVAILABLE
    assert database.get_book_by_asin("B001", account_id=other).status is BookStatus.UNAVAILABLE
    assert "Queued 1 parked book(s)" in caplog.text


def test_replace_account_auth_refuses_an_account_deleted_while_the_login_ran(db, monkeypatch):
    """A login takes minutes; the account it was started for may be gone by the end."""
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
    stale = database.get_account(account_id)
    database.delete_account(account_id)
    monkeypatch.setattr(accounts, "authenticator_for", lambda account: pytest.fail("nothing to deregister"))

    with pytest.raises(AccountGone, match="was removed while the login was in progress"):
        replace_account_auth(stale, Authenticator.from_dict(dict(AUTH_BLOB)))

    assert database.get_accounts() == []


def test_replace_account_auth_refuses_when_the_account_goes_before_the_write(db, monkeypatch):
    account_id = database.add_account("Alex (UK)", "uk", auth=None)
    monkeypatch.setattr(accounts, "save_account_auth", lambda account_id, auth, **kw: False)

    with pytest.raises(AccountGone):
        replace_account_auth(database.get_account(account_id), Authenticator.from_dict(dict(AUTH_BLOB)))


def test_replace_account_auth_deregisters_the_device_stored_now_not_the_one_read_earlier(db, monkeypatch):
    """Another login may have replaced the credentials since this one started."""
    account_id = database.add_account(
        "Alex (UK)", "uk", auth={**AUTH_BLOB, "access_token": "Atna|first"}, customer_id="u1"
    )
    stale = database.get_account(account_id)
    database.save_account_auth(account_id, {**AUTH_BLOB, "access_token": "Atna|second"})
    seen = []
    monkeypatch.setattr(
        accounts, "authenticator_for", lambda account: seen.append(account.auth["access_token"]) or _Deregistering()
    )

    replace_account_auth(stale, Authenticator.from_dict(dict(AUTH_BLOB)))

    assert seen == ["Atna|second"]


def test_replace_account_auth_goes_ahead_when_the_old_device_cannot_deregister(db, monkeypatch, caplog):
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
    monkeypatch.setattr(accounts, "authenticator_for", lambda account: _Deregistering(RuntimeError("throttled")))

    replace_account_auth(
        database.get_account(account_id), Authenticator.from_dict({**AUTH_BLOB, "access_token": "Atna|new"})
    )

    assert database.get_account(account_id).auth["access_token"] == "Atna|new"
    assert "Could not deregister the previous device" in caplog.text


def test_replace_account_auth_can_skip_deregistering(db, monkeypatch):
    account_id = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
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
    account_id = database.add_account("Alex (US)", "us", auth=dict(AUTH_BLOB), customer_id="u1")
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


# --- one account per login -----------------------------------------------------------


def _login(user_id="u1", locale_code="uk", **fields):
    blob = {**AUTH_BLOB, "customer_info": {"user_id": user_id, "name": "Alex"}, "locale_code": locale_code, **fields}
    return Authenticator.from_dict(blob)


def _deregistered(monkeypatch):
    seen = []
    monkeypatch.setattr(Authenticator, "deregister_device", lambda self: seen.append(self.access_token))
    return seen


def test_store_login_adds_an_account_for_a_login_nobody_holds(db):
    stored = accounts.store_login(_login(), name="Main", monitor_existing=False)

    assert stored.replaced is False
    account = database.get_account(stored.account_id)
    assert (account.name, account.monitor_existing) == ("Main", False)


def test_store_login_gives_the_account_holding_the_login_new_credentials(db, monkeypatch):
    deregistered = _deregistered(monkeypatch)
    first = accounts.store_login(_login(access_token="Atna|old"), name="Main", monitor_existing=False)
    book = make_book("B001", id=None)
    database.update_books(first.account_id, [book])

    second = accounts.store_login(_login(access_token="Atna|new"), name="Other", monitor_existing=True)

    assert second == StoredLogin(first.account_id, replaced=True)
    assert [a.id for a in database.get_accounts()] == [first.account_id]
    account = database.get_account(first.account_id)
    assert account.auth["access_token"] == "Atna|new"
    # What was asked for describes a new account; this one keeps what it had
    assert (account.name, account.monitor_existing) == ("Main", False)
    assert database.get_book_by_asin("B001", account_id=first.account_id) is not None
    assert deregistered == ["Atna|old"]


def test_store_login_records_whose_login_it_is(db):
    stored = accounts.store_login(_login(user_id="u7"))

    assert database.get_account(stored.account_id).customer_id == "u7"


def test_store_login_finds_the_account_whose_credentials_cannot_be_read(db, monkeypatch):
    """
    After a lost key the account reads as needing a login. Its customer is in the clear,
    so signing in again restores it, rather than adding a second account that would
    download the whole library again.
    """
    deregistered = _deregistered(monkeypatch)
    first = accounts.store_login(_login(access_token="Atna|old"))
    database.update_books(first.account_id, [make_book("B001", id=None)])
    crypto.configure(crypto.generate_key())
    database._undecryptable_logged.clear()
    assert database.get_account(first.account_id).needs_login

    second = accounts.store_login(_login(access_token="Atna|new"))

    assert second.account_id == first.account_id and second.replaced
    assert [a.id for a in database.get_accounts()] == [first.account_id]
    assert database.get_account(first.account_id).auth["access_token"] == "Atna|new"
    # Its old device cannot be read to deregister, so it is left alone
    assert deregistered == []


def test_store_login_stores_on_an_account_another_process_added_meanwhile(db, monkeypatch):
    """A CLI login beside the service: the lock is this process's, the index is everyone's."""
    _deregistered(monkeypatch)
    real_lookup = accounts.get_account_by_customer
    looks = []

    def racing_lookup(customer_id, country_code):
        looks.append(customer_id)
        if len(looks) == 1:
            # The other process stores the same login just after this one looked
            database.add_account("Added elsewhere", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
            return None
        return real_lookup(customer_id, country_code)

    monkeypatch.setattr(accounts, "get_account_by_customer", racing_lookup)

    stored = accounts.store_login(_login(access_token="Atna|new"), name="Mine")

    assert stored.replaced is True
    assert [a.name for a in database.get_accounts()] == ["Added elsewhere"]
    assert database.get_account(stored.account_id).auth["access_token"] == "Atna|new"


def test_replace_account_auth_refuses_a_login_another_process_stored_meanwhile(db, monkeypatch):
    _deregistered(monkeypatch)
    placeholder = database.add_account("Audible", "", auth=None)
    monkeypatch.setattr(accounts, "get_account_by_customer", lambda *a: None)
    database.add_account("Added elsewhere", "uk", auth=dict(AUTH_BLOB), customer_id="u1")

    with pytest.raises(LoginAlreadyHeld, match="already holds this Audible login"):
        replace_account_auth(database.get_account(placeholder), _login())

    assert database.get_account(placeholder).needs_login


def test_another_customer_is_refused_even_when_the_credentials_cannot_be_read(db, monkeypatch):
    deregistered = _deregistered(monkeypatch)
    account_id = database.add_account("Alex", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
    crypto.configure(crypto.generate_key())
    database._undecryptable_logged.clear()

    with pytest.raises(AnotherCustomer):
        replace_account_auth(database.get_account(account_id), _login(user_id="someone-else"))

    assert database.get_account(account_id).customer_id == "u1"
    assert deregistered == []


def _save_fails(monkeypatch):
    def locked(account_id, auth, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(accounts, "save_account_auth", locked)


@pytest.mark.parametrize("through", ["store_login", "replace_account_auth"])
def test_a_failed_store_leaves_the_old_device_working(db, monkeypatch, through):
    """It still holds the account: deregistering it first would leave the account with nothing."""
    deregistered = _deregistered(monkeypatch)
    account_id = database.add_account("Alex", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
    _save_fails(monkeypatch)

    with pytest.raises(sqlite3.OperationalError):
        if through == "store_login":
            accounts.store_login(_login())
        else:
            replace_account_auth(database.get_account(account_id), _login())

    assert deregistered == []
    assert database.get_account(account_id).auth == AUTH_BLOB


def test_the_old_device_is_deregistered_only_after_the_new_one_is_stored(db, monkeypatch):
    account_id = database.add_account("Alex", "uk", auth={**AUTH_BLOB, "access_token": "Atna|old"}, customer_id="u1")
    stored_then = []
    monkeypatch.setattr(
        Authenticator,
        "deregister_device",
        lambda self: stored_then.append(database.get_account(account_id).auth["access_token"]),
    )

    accounts.store_login(_login(access_token="Atna|new"))

    assert stored_then == ["Atna|new"]


def test_a_failed_requeue_does_not_read_as_the_login_not_stored(db, monkeypatch, caplog):
    """The caller discards the new device on an exception, and that device is now the account's."""
    _deregistered(monkeypatch)
    account_id = database.add_account("Alex", "uk", auth=dict(AUTH_BLOB), customer_id="u1")

    def locked(account_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(accounts, "requeue_unavailable", locked)

    assert accounts.store_login(_login(access_token="Atna|new")) == StoredLogin(account_id, replaced=True, requeued=0)
    assert database.get_account(account_id).auth["access_token"] == "Atna|new"
    assert "Could not queue the parked books" in caplog.text


@pytest.mark.parametrize("through", ["store_login", "replace_account_auth"])
def test_other_logins_are_not_held_up_while_the_old_device_is_deregistered(db, monkeypatch, through):
    """Amazon can be slow, and a login waiting on the lock holds its own cancel up too."""
    account_id = database.add_account("Alex", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
    locked = []
    monkeypatch.setattr(Authenticator, "deregister_device", lambda self: locked.append(accounts._store_lock.locked()))

    if through == "store_login":
        accounts.store_login(_login())
    else:
        replace_account_auth(database.get_account(account_id), _login())

    assert locked == [False]


def test_replace_account_auth_refuses_a_login_another_account_holds(db, monkeypatch):
    deregistered = _deregistered(monkeypatch)
    holder = database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
    placeholder = database.add_account("Audible", "", auth=None)

    with pytest.raises(LoginAlreadyHeld, match=r"Alex \(UK\) already holds this Audible login"):
        replace_account_auth(database.get_account(placeholder), _login(access_token="Atna|new"))

    assert database.get_account(placeholder).needs_login is True
    assert database.get_account(holder).auth == AUTH_BLOB
    assert deregistered == []


def test_replace_account_auth_lets_a_duplicate_sign_in_on_its_own_row(db, monkeypatch):
    """Rows from before the rule: each must still be able to get working credentials."""
    _deregistered(monkeypatch)
    database.add_account("First", "uk", auth=dict(AUTH_BLOB), customer_id="u1")
    second = database.add_account("Second", "uk", auth=dict(AUTH_BLOB))  # a duplicate from before the rule

    replace_account_auth(database.get_account(second), _login(access_token="Atna|new"))

    assert database.get_account(second).auth["access_token"] == "Atna|new"
    # The customer stays with the account holding it, which the index allows only once
    assert database.get_account(second).customer_id is None


def test_replace_account_auth_refuses_another_customer_and_leaves_the_device_working(db, monkeypatch):
    """Its library is that customer's: the wrong login would ask for its books and never fetch its own."""
    deregistered = _deregistered(monkeypatch)
    account_id = database.add_account("Alex", "uk", auth=dict(AUTH_BLOB), customer_name="Alex", customer_id="u1")

    with pytest.raises(AnotherCustomer, match="different Amazon login from the one Alex belongs to"):
        replace_account_auth(database.get_account(account_id), _login(user_id="someone-else"))

    account = database.get_account(account_id)
    assert account.auth == AUTH_BLOB
    assert account.customer_name == "Alex"
    assert deregistered == []


@pytest.mark.parametrize(
    "stored",
    [None, {**AUTH_BLOB, "customer_info": {"name": "Alex"}}, {**AUTH_BLOB, "customer_info": None}],
    ids=["no credentials", "no customer id", "no customer info"],
)
def test_replace_account_auth_takes_a_login_when_the_account_does_not_say_whose_it_is(db, monkeypatch, stored):
    _deregistered(monkeypatch)
    account_id = database.add_account("Alex", "uk", auth=stored)

    replace_account_auth(database.get_account(account_id), _login(user_id="someone"))

    assert database.get_account(account_id).auth["customer_info"]["user_id"] == "someone"


def test_replace_account_auth_takes_a_login_that_does_not_say_whose_it_is(db, monkeypatch):
    _deregistered(monkeypatch)
    account_id = database.add_account("Alex", "uk", auth=dict(AUTH_BLOB), customer_id="u1")

    replace_account_auth(
        database.get_account(account_id), Authenticator.from_dict({**AUTH_BLOB, "customer_info": {"name": "Alex"}})
    )

    assert database.get_account(account_id).auth["customer_info"] == {"name": "Alex"}


def test_a_failed_store_leaves_the_placeholder_able_to_take_any_marketplace(db, monkeypatch):
    """A retry in the library's own marketplace must not be refused for one that was never kept."""
    placeholder = database.add_account("Audible", "", auth=None)

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(database, "_encode_auth", locked)

    with pytest.raises(sqlite3.OperationalError):
        replace_account_auth(database.get_account(placeholder), _login())

    account = database.get_account(placeholder)
    assert account.country_code == ""
    assert account.needs_login is True


def test_store_login_can_leave_the_previous_device_registered(db, monkeypatch):
    deregistered = _deregistered(monkeypatch)
    accounts.store_login(_login())

    accounts.store_login(_login(), deregister_previous=False)

    assert deregistered == []


@pytest.mark.parametrize(
    "other",
    [{"user_id": "someone-else"}, {"locale_code": "us"}],
    ids=["another customer", "another marketplace"],
)
def test_store_login_adds_an_account_for_another_customer_or_marketplace(db, other):
    first = accounts.store_login(_login())

    second = accounts.store_login(_login(**other))

    assert second.replaced is False
    assert second.account_id != first.account_id


def test_store_login_matches_the_marketplace_whatever_its_case(db, monkeypatch):
    _deregistered(monkeypatch)
    account_id = database.add_account("Alex", "UK", auth=dict(AUTH_BLOB), customer_id="u1")

    assert accounts.store_login(_login()) == StoredLogin(account_id, replaced=True)


@pytest.mark.parametrize("customer_info", [{}, {"name": "Alex"}, None])
def test_store_login_adds_an_account_when_the_login_does_not_say_whose_it_is(db, customer_info):
    database.add_account("Alex", "uk", auth=dict(AUTH_BLOB), customer_id="u1")

    stored = accounts.store_login(Authenticator.from_dict({**AUTH_BLOB, "customer_info": customer_info}))

    assert stored.replaced is False


def test_store_login_does_not_match_two_logins_that_both_do_not_say_whose_they_are(db):
    blob = {**AUTH_BLOB, "customer_info": {"name": "Alex"}}
    first = accounts.store_login(Authenticator.from_dict(dict(blob)))

    second = accounts.store_login(Authenticator.from_dict(dict(blob)))

    assert second.replaced is False
    assert second.account_id != first.account_id


def test_store_login_does_not_guess_at_an_account_with_no_credentials(db):
    """The migration's placeholder could be anybody's; Sign in on its card is the way in."""
    placeholder = database.add_account("Audible", "uk", auth=None)

    stored = accounts.store_login(_login())

    assert stored.account_id != placeholder


def test_store_login_adds_an_account_when_the_match_is_removed_meanwhile(db, monkeypatch):
    accounts.store_login(_login())

    def removed(account, auth):
        database.delete_account(account.id)
        raise accounts.AccountGone("removed")

    monkeypatch.setattr(accounts, "_store_auth", removed)

    stored = accounts.store_login(_login())

    assert stored.replaced is False
    assert [a.id for a in database.get_accounts()] == [stored.account_id]


def test_store_login_adds_only_one_account_for_two_logins_at_once(db, monkeypatch):
    _deregistered(monkeypatch)
    together = threading.Barrier(2)
    add = accounts.add_account_from_authenticator
    results = []

    def run():
        together.wait(5)
        results.append(accounts.store_login(_login()))

    def slow_add(auth, **kwargs):
        time.sleep(0.05)  # the window a second login would find no account in
        return add(auth, **kwargs)

    monkeypatch.setattr(accounts, "add_account_from_authenticator", slow_add)
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert len(database.get_accounts()) == 1
    assert sorted(r.replaced for r in results) == [False, True]


def _devices_at_rest():
    assert accounts._devices_in_use == set()
    assert accounts._replaced_devices == {}


def test_a_login_during_a_run_on_the_account_waits_to_deregister_the_old_device(db, monkeypatch):
    """Deregistering it at once ended the run `aborted`, saying to log in again."""
    deregistered = _deregistered(monkeypatch)
    first = accounts.store_login(_login(access_token="Atna|old"))

    with accounts.device_in_use(first.account_id):
        accounts.store_login(_login(access_token="Atna|new"))
        # Stored straight away, the old device untouched for the run
        assert database.get_account(first.account_id).auth["access_token"] == "Atna|new"
        assert deregistered == []

    assert deregistered == ["Atna|old"]
    _devices_at_rest()


def test_replacing_an_account_during_its_run_waits_too(db, monkeypatch):
    deregistered = _deregistered(monkeypatch)
    account_id = database.add_account(
        "Alex (UK)", "uk", auth={**AUTH_BLOB, "access_token": "Atna|old"}, customer_id="u1"
    )

    with accounts.device_in_use(account_id):
        replace_account_auth(database.get_account(account_id), _login(access_token="Atna|new"))
        assert deregistered == []

    assert deregistered == ["Atna|old"]


def test_a_second_login_during_the_run_retires_both_devices_when_it_ends(db, monkeypatch):
    deregistered = _deregistered(monkeypatch)
    first = accounts.store_login(_login(access_token="Atna|one"))

    with accounts.device_in_use(first.account_id):
        accounts.store_login(_login(access_token="Atna|two"))
        accounts.store_login(_login(access_token="Atna|three"))

    assert deregistered == ["Atna|one", "Atna|two"]
    assert database.get_account(first.account_id).auth["access_token"] == "Atna|three"


def test_a_run_on_another_account_does_not_hold_up_the_old_device(db, monkeypatch):
    deregistered = _deregistered(monkeypatch)
    first = accounts.store_login(_login(access_token="Atna|old"))
    other = database.add_account("Alex (US)", "us", auth=None)

    with accounts.device_in_use(other):
        accounts.store_login(_login(access_token="Atna|new"))
        assert deregistered == ["Atna|old"]

    assert database.get_account(first.account_id).auth["access_token"] == "Atna|new"


def test_a_login_after_the_run_deregisters_at_once(db, monkeypatch):
    deregistered = _deregistered(monkeypatch)
    first = accounts.store_login(_login(access_token="Atna|old"))

    with accounts.device_in_use(first.account_id):
        pass
    accounts.store_login(_login(access_token="Atna|new"))

    assert deregistered == ["Atna|old"]


def test_a_run_that_raises_still_retires_the_device(db, monkeypatch):
    deregistered = _deregistered(monkeypatch)
    first = accounts.store_login(_login(access_token="Atna|old"))

    with pytest.raises(RuntimeError), accounts.device_in_use(first.account_id):
        accounts.store_login(_login(access_token="Atna|new"))
        raise RuntimeError("the run failed")

    assert deregistered == ["Atna|old"]
    _devices_at_rest()


def test_a_device_that_cannot_deregister_after_the_run_is_only_a_warning(db, monkeypatch, caplog):
    monkeypatch.setattr(Authenticator, "deregister_device", lambda self: (_ for _ in ()).throw(OSError("no answer")))
    first = accounts.store_login(_login(access_token="Atna|old"))

    with accounts.device_in_use(first.account_id):
        accounts.store_login(_login(access_token="Atna|new"))

    assert "Could not deregister the previous device" in caplog.text
    _devices_at_rest()


def _parked_book(account_id, asin="B001"):
    """A book the old device was refused a licence for."""
    database.update_books(account_id, [make_book(asin, id=None)])
    book = database.get_book_by_asin(asin, account_id=account_id)
    claim = database.claim_book_for_download(book.id)
    assert claim
    database.mark_book_unavailable(book.id, "Audible did not grant a license", claim=claim)
    return book.id


def test_books_the_old_device_parks_after_the_login_are_queued_when_the_run_ends(db, monkeypatch):
    """The login queued what was parked then; the run, still on the old device, can park more."""
    _deregistered(monkeypatch)
    first = accounts.store_login(_login(access_token="Atna|old"))

    with accounts.device_in_use(first.account_id):
        accounts.store_login(_login(access_token="Atna|new"))
        book_id = _parked_book(first.account_id)
        assert database.get_book(book_id).status == BookStatus.UNAVAILABLE

    assert database.get_book(book_id).status == BookStatus.WAITING_DOWNLOAD


def test_those_books_are_queued_even_when_the_old_device_is_kept(db, monkeypatch):
    deregistered = _deregistered(monkeypatch)
    first = accounts.store_login(_login(access_token="Atna|old"))

    with accounts.device_in_use(first.account_id):
        accounts.store_login(_login(access_token="Atna|new"), deregister_previous=False)
        book_id = _parked_book(first.account_id)

    assert database.get_book(book_id).status == BookStatus.WAITING_DOWNLOAD
    assert deregistered == []
    _devices_at_rest()


def test_sign_in_again_keeping_the_old_device_queues_those_books_too(db, monkeypatch):
    deregistered = _deregistered(monkeypatch)
    account_id = database.add_account(
        "Alex (UK)", "uk", auth={**AUTH_BLOB, "access_token": "Atna|old"}, customer_id="u1"
    )

    with accounts.device_in_use(account_id):
        replace_account_auth(
            database.get_account(account_id), _login(access_token="Atna|new"), deregister_previous=False
        )
        book_id = _parked_book(account_id)

    assert database.get_book(book_id).status == BookStatus.WAITING_DOWNLOAD
    assert deregistered == []


def test_a_run_nobody_signed_in_during_leaves_parked_books_parked(db, monkeypatch):
    """A refusal stays parked until retried, or two refused books block `max-download = 2` every run."""
    _deregistered(monkeypatch)
    first = accounts.store_login(_login(access_token="Atna|old"))

    with accounts.device_in_use(first.account_id):
        book_id = _parked_book(first.account_id)

    assert database.get_book(book_id).status == BookStatus.UNAVAILABLE


def test_a_failed_requeue_after_the_run_is_only_logged(db, monkeypatch, caplog):
    deregistered = _deregistered(monkeypatch)
    first = accounts.store_login(_login(access_token="Atna|old"))
    monkeypatch.setattr(accounts, "requeue_unavailable", lambda account_id: (_ for _ in ()).throw(OSError("locked")))

    with accounts.device_in_use(first.account_id):
        accounts.store_login(_login(access_token="Atna|new"))

    assert deregistered == ["Atna|old"]
    assert "Could not queue the parked books" in caplog.text
    _devices_at_rest()
