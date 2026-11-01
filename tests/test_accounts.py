import json

import pytest
from audible import Authenticator

import src.database as database
from src import accounts
from src.accounts import (
    authenticator_for,
    display_name,
    ensure_account_from_auth_file,
    import_auth_file,
    persist_auth_if_changed,
)
from src.model import Account
from tests.conftest import make_account

# What `Authenticator.to_dict()` looks like for a UK login; enough for from_dict/from_file
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


def _auth_file(tmp_path, **overrides):
    path = tmp_path / "audible.json"
    path.write_text(json.dumps({**AUTH_BLOB, **overrides}))
    return path


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


def test_import_auth_file_creates_an_account_from_the_file(db, tmp_path):
    account_id = import_auth_file(_auth_file(tmp_path), monitor_existing=False)

    account = database.get_account(account_id)
    assert (account.name, account.country_code, account.customer_name) == ("Alex (UK)", "uk", "Alex")
    assert account.monitor_existing is False
    assert account.auth == AUTH_BLOB


def test_import_auth_file_takes_a_name(db, tmp_path):
    account_id = import_auth_file(_auth_file(tmp_path), name="Main")

    assert database.get_account(account_id).name == "Main"


def test_import_auth_file_raises_on_a_missing_file(db, tmp_path):
    with pytest.raises(FileNotFoundError, match="auth file not found"):
        import_auth_file(tmp_path / "nope.json")


def test_ensure_account_creates_the_first_account_from_the_file(db, tmp_path):
    assert ensure_account_from_auth_file(_auth_file(tmp_path)) is True

    (account,) = database.get_accounts()
    assert account.name == "Alex (UK)"
    assert account.needs_login is False


def test_ensure_account_does_nothing_without_a_file(db, tmp_path):
    assert ensure_account_from_auth_file(tmp_path / "nope.json") is False
    assert database.get_accounts() == []


def test_ensure_account_fills_in_the_migration_placeholder(db, tmp_path):
    """The library rebuild invents an account with no credentials; the file completes
    it under the same id, so the books already attached to it stay attached."""
    placeholder = database.add_account(database.LEGACY_ACCOUNT_NAME, "", auth=None)

    assert ensure_account_from_auth_file(_auth_file(tmp_path)) is True

    (account,) = database.get_accounts()
    assert account.id == placeholder
    assert (account.name, account.country_code, account.customer_name) == ("Alex (UK)", "uk", "Alex")
    assert account.auth == AUTH_BLOB


def test_ensure_account_leaves_a_working_account_alone(db, tmp_path):
    """Once there is a working account the API is the source of truth, not the file."""
    database.add_account("Alex (UK)", "uk", auth=dict(AUTH_BLOB))

    assert ensure_account_from_auth_file(_auth_file(tmp_path, access_token="Atna|other")) is False
    assert database.get_accounts()[0].auth["access_token"] == "Atna|access"


def test_ensure_account_leaves_several_accounts_alone(db, tmp_path):
    database.add_account("One", "uk", auth=None)
    database.add_account("Two", "us", auth=None)

    assert ensure_account_from_auth_file(_auth_file(tmp_path)) is False
    assert all(a.needs_login for a in database.get_accounts())


def test_add_account_from_authenticator_defaults_the_name(db):
    account_id = accounts.add_account_from_authenticator(Authenticator.from_dict(dict(AUTH_BLOB)))

    assert database.get_account(account_id).name == "Alex (UK)"
