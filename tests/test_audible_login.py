import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from audible import Authenticator

from src import audible_login
from src.audible_login import (
    MARKETPLACES,
    PendingLogin,
    PendingLogins,
    authorization_code_from,
    complete_login,
    locale_for,
    login_interactively,
    start_login,
)
from tests.test_accounts import AUTH_BLOB

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

# What `register` hands back: the token half of an auth blob
REGISTERED = {k: v for k, v in AUTH_BLOB.items() if k not in ("locale_code", "with_username", "activation_bytes")}


def _landing(code="AUTHCODE", domain="co.uk"):
    return f"https://www.amazon.{domain}/ap/maplanding?openid.oa2.authorization_code={code}&openid.ns=x"


def test_marketplaces_come_from_the_library_with_readable_names():
    by_code = {m.country_code: m for m in MARKETPLACES}

    assert by_code["uk"].domain == "co.uk"
    assert by_code["uk"].name == "United Kingdom"
    assert by_code["us"].name == "United States"


def test_locale_for_rejects_an_unknown_marketplace():
    with pytest.raises(ValueError, match="unknown marketplace 'xx'"):
        locale_for("xx")


def test_start_login_builds_a_pkce_sign_in_url_for_the_marketplace():
    pending = start_login("uk", now=NOW)

    parts = urlsplit(pending.url)
    query = parse_qs(parts.query)
    assert parts.netloc == "www.amazon.co.uk"
    assert parts.path == "/ap/signin"
    assert query["marketPlaceId"] == ["A2I9A3Q2GNFNGQ"]
    assert query["openid.oa2.code_challenge_method"] == ["S256"]
    # The challenge in the URL is derived from the verifier kept for step two
    challenge = base64.urlsafe_b64encode(hashlib.sha256(pending.code_verifier).digest()).rstrip(b"=").decode()
    assert query["openid.oa2.code_challenge"] == [challenge]
    # The client id is the device serial the store keeps for registration
    assert query["openid.oa2.client_id"] == [f"device:{(pending.serial.encode() + b'#A2CZJZGLK2JJVM').hex()}"]
    assert pending.country_code == "uk"
    assert pending.expires_at == NOW + timedelta(minutes=15)
    assert len(pending.id) >= 16


def test_every_start_is_a_fresh_login():
    first, second = start_login("us"), start_login("us")

    assert first.id != second.id
    assert first.code_verifier != second.code_verifier
    assert first.serial != second.serial


def test_start_login_rejects_an_unknown_marketplace():
    with pytest.raises(ValueError, match="unknown marketplace"):
        start_login("xx")


def test_authorization_code_from_the_landing_page_url():
    assert authorization_code_from(_landing("ABC.123")) == "ABC.123"
    assert authorization_code_from(f"  {_landing('ABC')}\n") == "ABC"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.amazon.co.uk/ap/signin?openid.mode=checkid_setup",  # the sign-in URL pasted back
        "https://www.amazon.co.uk/",
        "https://www.amazon.co.uk/ap/maplanding?openid.oa2.authorization_code=",
        "not a url",
        "",
    ],
)
def test_authorization_code_from_a_url_without_one(url):
    with pytest.raises(ValueError, match="no authorization code"):
        authorization_code_from(url)


def test_complete_login_registers_with_what_start_kept(monkeypatch):
    pending = start_login("uk", now=NOW)
    calls = []

    def fake_register(**kwargs):
        calls.append(kwargs)
        return dict(REGISTERED)

    monkeypatch.setattr(audible_login, "register", fake_register)

    auth = complete_login(pending, _landing("THECODE"))

    assert calls == [
        {
            "authorization_code": "THECODE",
            "code_verifier": pending.code_verifier,
            "domain": "co.uk",
            "serial": pending.serial,
        }
    ]
    assert isinstance(auth, Authenticator)
    assert auth.locale.country_code == "uk"
    assert auth.with_username is False
    assert auth.access_token == AUTH_BLOB["access_token"]
    # What gets stored is the full blob, marketplace included
    assert auth.to_dict()["locale_code"] == "uk"


def test_complete_login_does_not_touch_amazon_for_a_url_without_a_code(monkeypatch):
    monkeypatch.setattr(audible_login, "register", lambda **kw: pytest.fail("must not register"))

    with pytest.raises(ValueError, match="no authorization code"):
        complete_login(start_login("uk"), "https://www.amazon.co.uk/")


def test_complete_login_lets_a_rejection_through(monkeypatch):
    def rejected(**kwargs):
        raise Exception({"response": {"error": "InvalidValue"}})

    monkeypatch.setattr(audible_login, "register", rejected)

    with pytest.raises(Exception, match="InvalidValue"):
        complete_login(start_login("uk"), _landing())


# --- the store ---------------------------------------------------------------------


def _store(*, at=NOW):
    clock = {"now": at}
    store = PendingLogins(now=lambda: clock["now"])
    return store, clock


def test_pending_logins_start_and_pop_once():
    store, _ = _store()

    pending = store.start("uk")

    assert isinstance(pending, PendingLogin)
    assert store.pop(pending.id) == pending
    # Single use: a pasted URL cannot register two devices
    assert store.pop(pending.id) is None


def test_pending_logins_forget_an_expired_login():
    store, clock = _store()
    pending = store.start("uk")

    clock["now"] = NOW + timedelta(minutes=15)

    assert store.pop(pending.id) is None


def test_pending_logins_keep_one_that_is_still_good():
    store, clock = _store()
    pending = store.start("uk")

    clock["now"] = NOW + timedelta(minutes=14, seconds=59)

    assert store.pop(pending.id) is not None


def test_pending_logins_unknown_id():
    store, _ = _store()

    assert store.pop("nope") is None


# --- the terminal --------------------------------------------------------------------


def test_login_interactively_shows_the_url_and_completes_with_the_pasted_one(monkeypatch):
    shown = []
    registered = []
    monkeypatch.setattr(audible_login, "register", lambda **kw: registered.append(kw) or dict(REGISTERED))

    auth = login_interactively("uk", prompt=lambda text: _landing("PASTED"), echo=shown.append)

    assert "https://www.amazon.co.uk/ap/signin" in shown[0]
    assert "page not found" in shown[0]
    assert registered[0]["authorization_code"] == "PASTED"
    assert auth.locale.country_code == "uk"
