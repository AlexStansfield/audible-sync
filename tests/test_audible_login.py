import base64
import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from audible import Authenticator

from src import audible_login
from src.audible_login import (
    IPHONE,
    MARKETPLACES,
    BrowserLogin,
    CodeLogin,
    LoginExpired,
    LoginFailed,
    LoginMethod,
    LoginStatus,
    PendingLogins,
    authorization_code_from,
    complete_login,
    credentials_from,
    discard_device,
    locale_for,
    login_interactively,
    login_with_code,
    poll_code_login,
    start_code_login,
    start_login,
    try_register,
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
    assert pending.method is LoginMethod.BROWSER
    assert pending.status is LoginStatus.PENDING
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


# --- the device-code flow ------------------------------------------------------------

CODE_PAIR = {
    "public_code": "ABCDE",
    "private_code": "private-123",
    "polling_interval_in_seconds": "5",
    "expires_in": "600",
}


def _tokens(blob=AUTH_BLOB):
    return {
        "bearer": {
            "access_token": blob["access_token"],
            "refresh_token": blob["refresh_token"],
            "expires_in": "3600",
        },
        "mac_dms": {"adp_token": blob["adp_token"], "device_private_key": blob["device_private_key"]},
        "website_cookies": [{"Name": "session-id", "Value": '"abc"'}],
        "store_authentication_cookie": blob["store_authentication_cookie"],
    }


def registered_response(blob=AUTH_BLOB):
    """A 200 from `/auth/register` once the code has been confirmed."""
    success = {
        "tokens": _tokens(blob),
        "extensions": {"device_info": blob["device_info"], "customer_info": blob["customer_info"]},
    }
    return httpx.Response(200, json={"response": {"success": success}})


# What Amazon answers until the user has entered the code
PENDING = httpx.Response(401, json={"response": {"error": {"code": "Unauthorized", "message": "not yet"}}})
REJECTED = httpx.Response(400, json={"response": {"error": {"code": "InvalidValue", "message": "no"}}})
OVERLOADED = httpx.Response(429, json={})


class FakeAmazon:
    """
    Amazon's two auth endpoints behind `httpx.MockTransport`.

    `polls` is the scripted sequence of answers to the registration poll, then `default`
    for ever after; `gate`, when set, holds every poll until it is released, so a test can
    look at a login while it is still waiting.
    """

    def __init__(self, *, polls=(), default=PENDING, code_pair=None, gate=None):
        self.polls = list(polls)
        self.default = default
        self.code_pair = httpx.Response(200, json=CODE_PAIR) if code_pair is None else code_pair
        self.gate = gate
        self.requests: list[httpx.Request] = []

    def handler(self, request):
        self.requests.append(request)
        if request.url.path == "/auth/create/codepair":
            if isinstance(self.code_pair, Exception):
                raise self.code_pair
            return self.code_pair
        if request.url.path == "/auth/register":
            if self.gate is not None:
                self.gate.wait()
            answer = self.polls.pop(0) if self.polls else self.default
            if isinstance(answer, Exception):
                raise answer
            return answer
        return httpx.Response(404)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def bodies(self, path):
        return [json.loads(r.content) for r in self.requests if r.url.path == path]


def _code_login(**overrides):
    fields = {
        "id": "login-1",
        "country_code": "uk",
        "serial": "S" * 40,
        "expires_at": NOW + timedelta(minutes=10),
        "code": "ABCDE",
        "verification_url": "https://www.amazon.co.uk/code",
        "private_code": "private-123",
        "interval": 5.0,
    }
    return CodeLogin(**{**fields, **overrides})


def test_start_code_login_asks_amazon_for_a_code_pair():
    amazon = FakeAmazon()

    login = start_code_login("uk", now=NOW, http=amazon.client())

    request = amazon.requests[0]
    assert str(request.url) == "https://api.amazon.co.uk/auth/create/codepair"
    assert request.headers["x-amzn-identity-auth-domain"] == "api.amazon.co.uk"
    assert request.headers["user-agent"] == IPHONE.user_agent
    assert request.headers["accept"] == "application/json"
    body = json.loads(request.content)
    assert body["code_data"]["device_type"] == "A2CZJZGLK2JJVM"
    assert body["code_data"]["domain"] == "Device"
    assert body["code_properties"] == {"code_duration": "600", "code_length": "5"}
    # A new 40-character serial for the device being registered
    assert body["code_data"]["device_serial"] == login.serial
    assert len(login.serial) == 40 and login.serial.isalnum()
    assert login.method is LoginMethod.CODE
    assert login.status is LoginStatus.PENDING
    assert (login.code, login.private_code) == ("ABCDE", "private-123")
    assert login.verification_url == "https://www.amazon.co.uk/code"
    assert login.interval == 5.0
    assert login.expires_at == NOW + timedelta(seconds=600)
    assert login.country_code == "uk"


def test_start_code_login_uses_the_marketplaces_domain():
    amazon = FakeAmazon()

    login = start_code_login("us", now=NOW, http=amazon.client())

    assert amazon.requests[0].url.host == "api.amazon.com"
    assert login.verification_url == "https://www.amazon.com/code"


def test_start_code_login_never_polls_faster_than_once_a_second():
    amazon = FakeAmazon(code_pair=httpx.Response(200, json={**CODE_PAIR, "polling_interval_in_seconds": "0"}))

    assert start_code_login("uk", http=amazon.client()).interval == 1.0


def test_start_code_login_rejects_an_unknown_marketplace():
    with pytest.raises(ValueError, match="unknown marketplace"):
        start_code_login("xx", http=FakeAmazon().client())


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(400, json={"response": {"error": {"code": "InvalidValue"}}}),
        httpx.Response(200, json={"error": "ServerError"}),
        httpx.Response(200, json={"public_code": "ABCDE"}),  # no private code
        httpx.Response(200, json={**CODE_PAIR, "expires_in": "soon"}),
        httpx.Response(503, text="<html>"),
    ],
)
def test_start_code_login_when_amazon_will_not_issue_a_code(answer):
    with pytest.raises(LoginFailed):
        start_code_login("uk", http=FakeAmazon(code_pair=answer).client())


def test_try_register_sends_the_code_pair_with_the_device_profile():
    amazon = FakeAmazon(polls=[PENDING])
    login = _code_login()

    assert try_register(login, http=amazon.client()) is None

    request = amazon.requests[0]
    assert str(request.url) == "https://api.amazon.co.uk/auth/register"
    assert request.headers["x-amzn-identity-auth-domain"] == "api.amazon.co.uk"
    body = json.loads(request.content)
    assert body["requested_token_type"] == ["bearer", "mac_dms", "website_cookies"]
    assert body["cookies"] == {"website_cookies": [], "domain": ".amazon.co.uk"}
    assert body["auth_data"] == {
        "code_pair": {"public_code": "ABCDE", "private_code": "private-123"},
        "use_global_authentication": "true",
    }
    assert body["registration_data"] == {
        "domain": "Device",
        "app_version": "4.60",
        "device_serial": "S" * 40,
        "device_type": "A2CZJZGLK2JJVM",
        "device_name": IPHONE.device_name,
        "os_version": "26.2",
        "software_version": "46000856",
        "device_model": "iPhone",
        "app_name": "Audible",
    }
    assert body["requested_extensions"] == ["device_info", "customer_info"]


def test_try_register_returns_the_credentials_once_confirmed():
    credentials = try_register(_code_login(), http=FakeAmazon(polls=[registered_response()]).client())

    assert credentials["access_token"] == "Atna|access"
    assert credentials["adp_token"] == AUTH_BLOB["adp_token"]
    assert credentials["customer_info"] == {"user_id": "u1", "name": "Alex"}


@pytest.mark.parametrize("answer", [OVERLOADED, httpx.Response(500, text="oops"), httpx.Response(503, json={})])
def test_try_register_treats_an_overloaded_amazon_as_still_pending(answer, caplog):
    assert try_register(_code_login(), http=FakeAmazon(polls=[answer]).client()) is None
    assert "trying again" in caplog.text


@pytest.mark.parametrize(
    "answer",
    [
        REJECTED,
        httpx.Response(401, json={"response": {"error": {"code": "CodeExpired"}}}),  # a 401 that is not "Unauthorized"
        httpx.Response(200, json={"response": {"error": "ServerError"}}),  # a 200 with no success
        httpx.Response(403, text="forbidden"),
    ],
)
def test_try_register_when_amazon_refuses(answer):
    with pytest.raises(LoginFailed, match="refused the device registration"):
        try_register(_code_login(), http=FakeAmazon(polls=[answer]).client())


def test_credentials_from_maps_the_registration_like_the_librarys_register():
    success = {"tokens": _tokens(), "extensions": {"device_info": {"d": 1}, "customer_info": {"name": "Alex"}}}

    credentials = credentials_from(success, now=NOW)

    assert credentials == {
        "adp_token": AUTH_BLOB["adp_token"],
        "device_private_key": AUTH_BLOB["device_private_key"],
        "access_token": "Atna|access",
        "refresh_token": "Atnr|refresh",
        "expires": (NOW + timedelta(hours=1)).timestamp(),
        # The quotes Amazon wraps cookie values in are stripped, as the library does
        "website_cookies": {"session-id": "abc"},
        "store_authentication_cookie": {"cookie": "x"},
        "device_info": {"d": 1},
        "customer_info": {"name": "Alex"},
    }
    # ...and what it builds is a blob `Authenticator` accepts
    auth = Authenticator.from_dict({**credentials, "locale_code": "uk", "with_username": False})
    assert auth.website_cookies == {"session-id": "abc"}


def test_credentials_from_without_cookies_or_extensions():
    tokens = {k: v for k, v in _tokens().items() if k in ("bearer", "mac_dms")}

    credentials = credentials_from({"tokens": tokens})

    assert credentials["website_cookies"] is None
    assert credentials["store_authentication_cookie"] is None
    assert credentials["device_info"] is None
    assert credentials["customer_info"] is None


@pytest.mark.parametrize(
    "success",
    [
        {},
        {"tokens": {"bearer": {"access_token": "a"}}},
        {"tokens": {**_tokens(), "bearer": {**_tokens()["bearer"], "expires_in": "later"}}},
    ],
)
def test_credentials_from_a_response_that_lacks_credentials(success):
    with pytest.raises(LoginFailed, match="lacks credentials"):
        credentials_from(success)


def _clock(at=NOW):
    """A clock and a `sleep` that moves it, so polling loops run without waiting."""
    clock = {"now": at}

    def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    return clock, (lambda: clock["now"]), sleep


def test_poll_code_login_waits_for_the_confirmation_then_builds_the_authenticator():
    amazon = FakeAmazon(polls=[PENDING, PENDING, registered_response()])
    slept = []
    _, now, _ = _clock()

    auth = poll_code_login(_code_login(), http=amazon.client(), sleep=slept.append, now=now)

    assert isinstance(auth, Authenticator)
    assert auth.locale.country_code == "uk"
    assert auth.with_username is False
    assert auth.access_token == "Atna|access"
    assert auth.to_dict()["locale_code"] == "uk"
    assert len(amazon.bodies("/auth/register")) == 3
    # Paced by Amazon's interval, and no sleep after the answer
    assert slept == [5.0, 5.0]


def test_poll_code_login_gives_up_when_the_code_expires():
    amazon = FakeAmazon()  # pending for ever
    _, now, sleep = _clock()

    with pytest.raises(LoginExpired, match="expired"):
        poll_code_login(_code_login(expires_at=NOW + timedelta(seconds=12)), http=amazon.client(), sleep=sleep, now=now)

    # Polled at 0, 5 and 10 seconds; the deadline passed before a fourth
    assert len(amazon.requests) == 3


def test_poll_code_login_rides_out_a_network_error():
    amazon = FakeAmazon(polls=[httpx.ConnectError("down"), registered_response()])
    _, now, sleep = _clock()

    auth = poll_code_login(_code_login(), http=amazon.client(), sleep=sleep, now=now)

    assert auth.access_token == "Atna|access"


def test_poll_code_login_stops_on_a_refusal():
    _, now, sleep = _clock()

    with pytest.raises(LoginFailed, match="refused"):
        poll_code_login(_code_login(), http=FakeAmazon(polls=[PENDING, REJECTED]).client(), sleep=sleep, now=now)


# --- the store ---------------------------------------------------------------------


def _store(*, amazon=None, at=NOW):
    clock, now, sleep = _clock(at)
    store = PendingLogins(now=now, http=(amazon or FakeAmazon()).client(), sleep=sleep)
    return store, clock


def _finish(record, account_id=7):
    def finish(auth):
        record.append(auth)
        return account_id

    return finish


def test_store_runs_a_code_login_to_a_confirmed_account():
    amazon = FakeAmazon(polls=[PENDING, registered_response()])
    store, _ = _store(amazon=amazon)
    finished = []

    login = store.start_code("uk", _finish(finished))
    store.join(login.id, timeout=5)

    assert isinstance(login, CodeLogin)
    assert login.status is LoginStatus.PENDING  # the copy handed back at the start
    done = store.get(login.id)
    assert done.status is LoginStatus.CONFIRMED
    assert done.account_id == 7
    assert done.error is None
    assert done.code == "ABCDE"
    assert len(finished) == 1 and finished[0].access_token == "Atna|access"
    assert len(amazon.bodies("/auth/register")) == 2


def test_store_reports_a_code_login_as_pending_while_amazon_waits_for_the_user():
    gate = threading.Event()
    store, _ = _store(amazon=FakeAmazon(polls=[registered_response()], gate=gate))

    login = store.start_code("uk", _finish([]))
    try:
        assert store.get(login.id).status is LoginStatus.PENDING
        assert store.get(login.id).account_id is None
    finally:
        gate.set()
    store.join(login.id, timeout=5)

    assert store.get(login.id).status is LoginStatus.CONFIRMED


def test_store_marks_a_code_login_the_user_never_confirmed_expired():
    store, _ = _store(amazon=FakeAmazon())  # pending for ever; the sleep moves the clock
    finished = []

    login = store.start_code("uk", _finish(finished))
    store.join(login.id, timeout=5)

    assert store.get(login.id).status is LoginStatus.EXPIRED
    assert finished == []


def test_store_marks_a_code_login_amazon_refused_failed():
    store, _ = _store(amazon=FakeAmazon(polls=[REJECTED]))

    login = store.start_code("uk", _finish([]))
    store.join(login.id, timeout=5)

    done = store.get(login.id)
    assert done.status is LoginStatus.FAILED
    assert "refused the device registration" in done.error
    assert done.account_id is None


def test_store_marks_a_code_login_failed_when_the_account_cannot_be_stored(monkeypatch, caplog):
    """The device Amazon registered is held by nothing, so it is deregistered."""
    deregistered = []
    monkeypatch.setattr(Authenticator, "deregister_device", lambda self: deregistered.append(self.access_token))
    store, _ = _store(amazon=FakeAmazon(polls=[registered_response()]))

    def broken(auth):
        raise RuntimeError("disk full")

    login = store.start_code("uk", broken)
    store.join(login.id, timeout=5)

    done = store.get(login.id)
    assert done.status is LoginStatus.FAILED
    assert "confirmed but the account could not be stored: disk full" in done.error
    assert "the new device was deregistered" in done.error
    assert deregistered == ["Atna|access"]
    assert "Could not store the account" in caplog.text


def test_store_still_fails_the_login_when_the_unstored_device_cannot_be_deregistered(monkeypatch, caplog):
    def unreachable(self):
        raise RuntimeError("amazon down")

    monkeypatch.setattr(Authenticator, "deregister_device", unreachable)
    store, _ = _store(amazon=FakeAmazon(polls=[registered_response()]))

    def broken(auth):
        raise RuntimeError("disk full")

    login = store.start_code("uk", broken)
    store.join(login.id, timeout=5)

    done = store.get(login.id)
    assert done.status is LoginStatus.FAILED
    assert "deregistered" not in done.error
    assert "Could not deregister the device of a login that was not stored" in caplog.text


def test_discard_device_reports_whether_amazon_took_it():
    class Device:
        def __init__(self, error=None):
            self.error = error

        def deregister_device(self):
            if self.error:
                raise self.error

    assert discard_device(Device()) is True
    assert discard_device(Device(RuntimeError("throttled"))) is False


def test_store_start_code_lets_a_refused_code_pair_through():
    store, _ = _store(amazon=FakeAmazon(code_pair=REJECTED))

    with pytest.raises(LoginFailed, match="refused to create a device code"):
        store.start_code("uk", _finish([]))


def test_store_unknown_login():
    store, _ = _store()

    assert store.get("nope") is None
    with pytest.raises(KeyError):
        store.complete("nope", _landing())


def test_store_forgets_a_login_a_while_after_it_expired():
    store, clock = _store(amazon=FakeAmazon(polls=[registered_response()]))
    login = store.start_code("uk", _finish([]))
    store.join(login.id, timeout=5)

    clock["now"] = NOW + timedelta(minutes=10) + timedelta(minutes=14, seconds=59)
    assert store.get(login.id).status is LoginStatus.CONFIRMED

    clock["now"] = NOW + timedelta(minutes=10) + timedelta(minutes=15)
    assert store.get(login.id) is None


def test_store_hands_out_copies():
    store, _ = _store(amazon=FakeAmazon(polls=[registered_response()]))
    login = store.start_code("uk", _finish([]))
    store.join(login.id, timeout=5)

    copy = store.get(login.id)
    copy.status = LoginStatus.FAILED

    assert store.get(login.id).status is LoginStatus.CONFIRMED


# the browser flow through the store


def test_store_browser_login_is_pending_until_completed(monkeypatch):
    monkeypatch.setattr(audible_login, "register", lambda **kw: dict(REGISTERED))
    store, _ = _store()
    finished = []

    login = store.start("uk", _finish(finished, account_id=3))

    assert isinstance(login, BrowserLogin)
    assert store.get(login.id).status is LoginStatus.PENDING
    assert store.get(login.id).url.startswith("https://www.amazon.co.uk/ap/signin")

    done = store.complete(login.id, _landing("PASTED"))

    assert done.status is LoginStatus.CONFIRMED
    assert done.account_id == 3
    assert store.get(login.id).status is LoginStatus.CONFIRMED
    assert finished[0].access_token == "Atna|access"
    # Single use: a pasted URL cannot register two devices
    with pytest.raises(KeyError):
        store.complete(login.id, _landing("PASTED"))


def test_store_browser_login_stays_open_after_a_paste_without_a_code(monkeypatch):
    monkeypatch.setattr(audible_login, "register", lambda **kw: pytest.fail("must not register"))
    store, _ = _store()
    login = store.start("uk", _finish([]))

    with pytest.raises(ValueError, match="no authorization code"):
        store.complete(login.id, "https://www.amazon.co.uk/")

    assert store.get(login.id).status is LoginStatus.PENDING


def test_store_browser_login_amazon_rejects_is_failed(monkeypatch):
    def rejected(**kwargs):
        raise Exception({"response": {"error": "InvalidValue"}})

    monkeypatch.setattr(audible_login, "register", rejected)
    store, _ = _store()
    login = store.start("uk", _finish([]))

    with pytest.raises(Exception, match="InvalidValue"):
        store.complete(login.id, _landing())

    done = store.get(login.id)
    assert done.status is LoginStatus.FAILED
    assert "Amazon rejected the login" in done.error
    with pytest.raises(KeyError):
        store.complete(login.id, _landing())


def test_store_browser_login_expires():
    store, clock = _store()
    login = store.start("uk", _finish([]))

    clock["now"] = NOW + timedelta(minutes=15)

    assert store.get(login.id).status is LoginStatus.EXPIRED
    with pytest.raises(KeyError):
        store.complete(login.id, _landing())
    # ...and is forgotten a while later
    clock["now"] = NOW + timedelta(minutes=30)
    assert store.get(login.id) is None


def test_store_browser_login_still_good_a_second_before_expiry(monkeypatch):
    monkeypatch.setattr(audible_login, "register", lambda **kw: dict(REGISTERED))
    store, clock = _store()
    login = store.start("uk", _finish([]))

    clock["now"] = NOW + timedelta(minutes=14, seconds=59)

    assert store.complete(login.id, _landing()).status is LoginStatus.CONFIRMED


def test_store_complete_refuses_a_code_login():
    store, _ = _store(amazon=FakeAmazon(polls=[registered_response()]))
    login = store.start_code("uk", _finish([]))
    store.join(login.id, timeout=5)

    with pytest.raises(KeyError):
        store.complete(login.id, _landing())


# --- the terminal --------------------------------------------------------------------


def test_login_with_code_shows_the_code_and_waits_for_it():
    amazon = FakeAmazon(polls=[PENDING, registered_response()])
    shown = []
    slept = []

    auth = login_with_code("uk", echo=shown.append, http=amazon.client(), sleep=slept.append)

    assert "https://www.amazon.co.uk/code" in shown[0]
    assert "ABCDE" in shown[0]
    assert "10 minutes" in shown[0]
    assert slept == [5.0]
    assert auth.locale.country_code == "uk"
    assert auth.access_token == "Atna|access"


def test_login_with_code_lets_a_refusal_through():
    with pytest.raises(LoginFailed):
        login_with_code("uk", echo=lambda text: None, http=FakeAmazon(polls=[REJECTED]).client(), sleep=lambda s: None)


def test_login_interactively_shows_the_url_and_completes_with_the_pasted_one(monkeypatch):
    shown = []
    registered = []
    monkeypatch.setattr(audible_login, "register", lambda **kw: registered.append(kw) or dict(REGISTERED))

    auth = login_interactively("uk", prompt=lambda text: _landing("PASTED"), echo=shown.append)

    assert "https://www.amazon.co.uk/ap/signin" in shown[0]
    assert "page not found" in shown[0]
    assert registered[0]["authorization_code"] == "PASTED"
    assert auth.locale.country_code == "uk"
