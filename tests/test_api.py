import logging
import re
import sqlite3
import threading

import httpx
import pytest
from audible import Authenticator
from fastapi.testclient import TestClient

import audible_sync.database as database
from audible_sync.api.app import create_app, read_version
from audible_sync.api.routes import accounts as api_accounts
from audible_sync.api.routes import books as api_books
from audible_sync.api.routes import settings as api_settings
from audible_sync.audible_api import login as login_module
from audible_sync.audible_api.login import PendingLogins
from audible_sync.logbuffer import RingBufferHandler
from audible_sync.model import StoredLogin, SyncOutcome
from audible_sync.paths import REPO_ROOT
from audible_sync.pipeline.runstate import RunStage, RunState
from audible_sync.security import webauth
from audible_sync.security.apitoken import ApiToken
from audible_sync.settings import Settings
from tests.test_accounts import AUTH_BLOB
from tests.test_audible_login import PENDING, REJECTED, FakeAmazon, registered_response

TOKEN = "s3cret-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeScheduler:
    """Duck-types the four methods and the status the API touches."""

    def __init__(self, *, running=False):
        self.running = running
        self.triggered = 0
        self.full = []
        self.cancelled = 0
        self.woken = 0
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def trigger(self, *, full=False):
        if self.running:
            return False
        self.triggered += 1
        self.full.append(full)
        return True

    def cancel(self):
        if not self.running:
            return False
        self.cancelled += 1
        return True

    def wake(self):
        self.woken += 1

    def status(self, settings=None):
        return {
            "enabled": settings.sync_enabled,
            "interval_minutes": settings.sync_interval_minutes,
            "next_run_at": "2026-09-12T18:00:00+00:00",
            "running": self.running,
        }


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()


@pytest.fixture
def api(db):
    """An app on a fresh database, with its fakes reachable for assertions."""
    scheduler = FakeScheduler()
    state = RunState()
    # Amazon's auth endpoints are faked; a code login's polling thread barely sleeps, and
    # sleeps on `stop` so the teardown can end a login a test left pending
    amazon = FakeAmazon()
    stop = threading.Event()
    logins = PendingLogins(http=amazon.client(), sleep=lambda seconds: stop.wait(min(seconds, 0.01)))
    log_buffer = RingBufferHandler(capacity=50)
    app = create_app(
        scheduler=scheduler,
        state=state,
        api_token=ApiToken(TOKEN, "stored"),
        version="test",
        logins=logins,
        log_buffer=log_buffer,
    )
    # No context manager: the lifespan (and so the scheduler) is exercised on its own
    client = TestClient(app)
    client.scheduler = scheduler
    client.state = state
    client.logins = logins
    client.amazon = amazon
    client.log_buffer = log_buffer
    yield client
    # End any poll still waiting: Amazon now refuses, the sleep returns at once
    amazon.polls, amazon.default = [], REJECTED
    stop.set()
    for thread in list(logins._threads.values()):
        thread.join(timeout=5)


# --- auth ------------------------------------------------------------------------


def test_health_needs_no_token(api):
    response = api.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "test"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}])
def test_everything_else_needs_the_token(api, headers):
    response = api.get("/api/status", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"detail": "Not authenticated"}


# --- the web app's sign-in -------------------------------------------------------

PASSWORD = "correct horse battery"


@pytest.fixture
def user(api):
    webauth.ensure_admin()
    webauth.set_password(PASSWORD)


def _sign_in(client, *, remember=False, password=PASSWORD, username="admin"):
    return client.post("/api/auth/login", json={"username": username, "password": password, "remember": remember})


def test_signing_in_sets_a_session_cookie_that_works_on_every_route(api, user):
    response = _sign_in(api)

    assert response.status_code == 200
    assert response.json() == {"username": "admin", "generated_password": False}
    cookie = response.headers["set-cookie"]
    assert "audible_sync_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    # Plain http: a Secure cookie would never come back
    assert "Secure" not in cookie
    # No "Keep me signed in": the cookie ends with the browser
    assert "Max-Age" not in cookie
    assert api.get("/api/status").status_code == 200


def test_keep_me_signed_in_makes_the_cookie_last_thirty_days(api, user):
    cookie = _sign_in(api, remember=True).headers["set-cookie"]

    assert f"Max-Age={30 * 24 * 60 * 60}" in cookie


def test_the_cookie_is_secure_over_https(api, user):
    client = TestClient(api.app, base_url="https://testserver")

    assert "Secure" in _sign_in(client).headers["set-cookie"]


def test_secure_cookie_marks_the_cookie_secure_over_http_too(db, user):
    app = create_app(
        scheduler=FakeScheduler(), state=RunState(), api_token=ApiToken(TOKEN, "stored"), secure_cookie=True
    )

    assert "Secure" in _sign_in(TestClient(app)).headers["set-cookie"]


def test_signing_in_again_ends_the_previous_session(api, user):
    _sign_in(api)
    previous = api.cookies["audible_sync_session"]

    _sign_in(api)

    assert not webauth.check_session(previous)
    assert api.get("/api/status").status_code == 200


def test_an_overlong_password_is_refused_before_it_is_hashed(api, user, monkeypatch):
    monkeypatch.setattr(webauth, "check_password", lambda *a: pytest.fail("must not hash"))

    response = _sign_in(api, password="x" * (webauth.MAX_PASSWORD_LENGTH + 1))

    assert response.status_code == 422


@pytest.mark.parametrize(("username", "password"), [("admin", "wrong password!"), ("nobody", PASSWORD)])
def test_a_wrong_username_or_password_gets_one_message(api, user, username, password):
    response = _sign_in(api, username=username, password=password)

    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong username or password"}
    assert "set-cookie" not in response.headers
    assert api.get("/api/status").status_code == 401


def test_a_made_up_cookie_is_refused(api, user):
    api.cookies.set("audible_sync_session", "made up")

    assert api.get("/api/status").status_code == 401


def test_the_bearer_token_still_works_beside_sessions(api, user):
    assert api.get("/api/status", headers=AUTH).status_code == 200


def test_a_token_outside_ascii_is_a_401_not_a_500(api):
    response = api.get("/api/status", headers={"Authorization": b"Bearer t\xf6ken"})

    assert response.status_code == 401


def test_signing_out_ends_the_session(api, user):
    _sign_in(api)
    token = api.cookies["audible_sync_session"]

    response = api.post("/api/auth/logout")

    assert response.status_code == 204
    assert 'audible_sync_session=""' in response.headers["set-cookie"]
    # The session itself is gone, not only the cookie
    assert not webauth.check_session(token)


def test_signing_out_without_a_session_still_answers(api):
    assert api.post("/api/auth/logout").status_code == 204


def test_after_five_failures_even_the_right_password_waits(api, user):
    for _ in range(5):
        assert _sign_in(api, password="wrong password!").status_code == 401

    response = _sign_in(api)

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_me_says_whether_the_generated_password_is_in_use(api):
    webauth.ensure_admin()

    assert api.get("/api/auth/me", headers=AUTH).json() == {"username": "admin", "generated_password": True}


def test_me_needs_auth(api, user):
    assert api.get("/api/auth/me").status_code == 401


def test_changing_the_username_needs_the_current_password(api, user):
    _sign_in(api)

    wrong = api.put("/api/auth/username", json={"username": "alex", "current_password": "wrong password!"})
    right = api.put("/api/auth/username", json={"username": "alex", "current_password": PASSWORD})

    assert wrong.status_code == 403
    assert wrong.json() == {"detail": "The current password is wrong"}
    assert right.status_code == 200
    assert right.json()["username"] == "alex"
    assert webauth.verify("alex", PASSWORD)


def test_an_empty_username_is_a_422(api, user):
    response = api.put("/api/auth/username", headers=AUTH, json={"username": " ", "current_password": PASSWORD})

    assert response.status_code == 422
    assert response.json() == {"detail": "The username cannot be empty"}


def test_changing_the_password_signs_out_every_other_session_but_this_one(api, user):
    other = TestClient(api.app)
    _sign_in(other)
    _sign_in(api)

    response = api.put("/api/auth/password", json={"current_password": PASSWORD, "new_password": "a new long password"})

    assert response.status_code == 200
    assert response.json() == {"username": "admin", "generated_password": False}
    assert api.get("/api/status").status_code == 200
    assert other.get("/api/status").status_code == 401
    assert webauth.verify("admin", "a new long password")


def test_changing_the_password_with_the_token_signs_out_every_session(api, user):
    browser = TestClient(api.app)
    _sign_in(browser)

    TestClient(api.app).put(
        "/api/auth/password",
        headers=AUTH,
        json={"current_password": PASSWORD, "new_password": "a new long password"},
    )

    assert browser.get("/api/status").status_code == 401


def test_a_short_new_password_is_a_422_and_changes_nothing(api, user):
    response = api.put("/api/auth/password", headers=AUTH, json={"current_password": PASSWORD, "new_password": "short"})

    assert response.status_code == 422
    assert "at least 12" in response.json()["detail"]
    assert webauth.verify("admin", PASSWORD)


def test_wrong_current_passwords_count_towards_the_throttle(api, user):
    for _ in range(5):
        api.put("/api/auth/password", headers=AUTH, json={"current_password": "wrong!", "new_password": "x" * 12})

    assert _sign_in(api).status_code == 429


def test_the_web_user_never_shows_in_the_settings(api, user):
    body = api.get("/api/settings", headers=AUTH).json()

    assert not any(key.startswith("web_") for key in body)


def test_read_version_comes_from_pyproject():
    assert read_version() not in ("", "unknown")


# --- status and sync -------------------------------------------------------------


def test_status_when_idle_on_a_fresh_database(api):
    response = api.get("/api/status", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "scheduler": {
            "enabled": True,
            "interval_minutes": 360,
            "next_run_at": "2026-09-12T18:00:00+00:00",
            "running": False,
        },
        "current_run": None,
        "last_run": None,
        "accounts": [],
    }


def test_status_shows_the_run_in_flight_and_the_last_run(api, monkeypatch):
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-09-12T10:00:00+00:00")
    run_id = database.start_sync_run()
    database.finish_sync_run(run_id, outcome=SyncOutcome.PARTIAL, books_seen=4, books_failed=1, error=None)
    api.state.begin(run_id + 1)
    api.state.set_stage(RunStage.DOWNLOADING)
    api.state.set_book(make_book("B1", "Book", id=7), done=0, total=1)
    api.state.transfer_started("Book", 100)
    api.state.transfer_advanced(40)

    body = api.get("/api/status", headers=AUTH).json()

    assert body["current_run"]["run_id"] == run_id + 1
    assert body["current_run"]["stage"] == "downloading"
    assert body["current_run"]["book"] == {"id": 7, "asin": "B1", "title": "Book"}
    assert body["current_run"]["transfer"] == {"desc": "Book", "bytes": 40, "total": 100}
    assert body["last_run"]["id"] == run_id
    assert body["last_run"]["outcome"] == "partial"
    assert body["last_run"]["books_failed"] == 1


def test_start_sync_triggers_the_scheduler(api):
    response = api.post("/api/sync", headers=AUTH)

    assert response.status_code == 202
    assert response.json() == {"status": "started"}
    assert api.scheduler.triggered == 1
    # Without a body it is an ordinary run
    assert api.scheduler.full == [False]


@pytest.mark.parametrize(("body", "full"), [({"full": True}, True), ({"full": False}, False), ({}, False)])
def test_start_sync_asks_for_a_full_refresh_when_told_to(api, body, full):
    response = api.post("/api/sync", headers=AUTH, json=body)

    assert response.status_code == 202
    assert api.scheduler.full == [full]


@pytest.mark.parametrize("body", [{"full": "yes please"}, {"fulll": True}])
def test_start_sync_refuses_a_body_it_does_not_understand(api, body):
    response = api.post("/api/sync", headers=AUTH, json=body)

    assert response.status_code == 422
    assert api.scheduler.triggered == 0


def test_start_sync_is_a_conflict_while_one_is_running(api):
    api.scheduler.running = True

    response = api.post("/api/sync", headers=AUTH)

    assert response.status_code == 409
    assert response.json() == {"detail": "A sync is already running"}


def test_cancel_sync_asks_the_scheduler_to_stop(api):
    api.scheduler.running = True

    response = api.post("/api/sync/cancel", headers=AUTH)

    assert response.status_code == 202
    assert response.json() == {"status": "cancelling"}
    assert api.scheduler.cancelled == 1


def test_cancel_sync_is_a_conflict_when_nothing_is_running(api):
    response = api.post("/api/sync/cancel", headers=AUTH)

    assert response.status_code == 409
    assert response.json() == {"detail": "No sync is running"}


# --- run history -----------------------------------------------------------------


def _runs(monkeypatch, outcomes):
    ids = []
    for day, outcome in enumerate(outcomes, start=1):
        monkeypatch.setattr(database, "_utcnow", lambda day=day: f"2026-09-{day:02d}T10:00:00+00:00")
        run_id = database.start_sync_run()
        database.finish_sync_run(run_id, outcome=outcome, books_seen=day)
        ids.append(run_id)
    return ids


def test_list_runs_pages_newest_first(api, monkeypatch):
    ids = _runs(monkeypatch, [SyncOutcome.SUCCESS, SyncOutcome.FAILED, SyncOutcome.SUCCESS])

    body = api.get("/api/sync/runs", headers=AUTH, params={"limit": 2}).json()
    assert [r["id"] for r in body["items"]] == [ids[2], ids[1]]
    assert body["total"] == 3
    assert body["items"][1]["outcome"] == "failed"

    body = api.get("/api/sync/runs", headers=AUTH, params={"limit": 2, "offset": 2}).json()
    assert [r["id"] for r in body["items"]] == [ids[0]]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_list_runs_rejects_a_bad_page(api, params):
    assert api.get("/api/sync/runs", headers=AUTH, params=params).status_code == 422


def test_get_run(api, monkeypatch):
    (run_id,) = _runs(monkeypatch, [SyncOutcome.SUCCESS])

    body = api.get(f"/api/sync/runs/{run_id}", headers=AUTH).json()

    assert body == {
        "id": run_id,
        "account_id": None,
        "started_at": "2026-09-01T10:00:00+00:00",
        "finished_at": "2026-09-01T10:00:00+00:00",
        "outcome": "success",
        "books_seen": 1,
        "books_added": 0,
        "books_downloaded": 0,
        "books_failed": 0,
        "error": None,
        "books_recorded": True,
        "full_refresh": False,
    }


def test_get_run_that_does_not_exist(api):
    response = api.get("/api/sync/runs/99", headers=AUTH)

    assert response.status_code == 404
    assert response.json() == {"detail": "No run with id 99"}


# --- settings --------------------------------------------------------------------


def test_get_settings_returns_every_runtime_setting_typed(api):
    body = api.get("/api/settings", headers=AUTH).json()

    defaults = Settings()
    assert body["bitrate"] == defaults.bitrate
    assert body["max_download"] is None
    assert body["download_folder"] == str(defaults.download_folder)
    assert body["sync_interval_minutes"] == 360
    assert set(body) == set(Settings().to_db_values())


def test_update_settings_changes_only_what_is_sent_and_persists_it(api):
    response = api.put("/api/settings", headers=AUTH, json={"bitrate": 32, "encoding_format": "oga"})

    assert response.status_code == 200
    body = response.json()
    assert (body["bitrate"], body["encoding_format"]) == (32, "oga")
    assert body["max_attempts"] == 3
    stored = Settings.from_db()
    assert (stored.bitrate, stored.encoding_format) == (32, "oga")
    # The thread asleep on the old interval is told to look again
    assert api.scheduler.woken == 1


def test_update_settings_null_clears_an_optional_setting(api):
    api.put("/api/settings", headers=AUTH, json={"max_download": 5, "webhook_url": "https://hooks/x"})

    body = api.put("/api/settings", headers=AUTH, json={"max_download": None, "webhook_url": None}).json()

    assert body["max_download"] is None
    assert body["webhook_url"] is None
    assert Settings.from_db().max_download is None


@pytest.mark.parametrize("field", ["debug", "sync_interval_minutes", "bitrate", "audiobook_folder"])
def test_update_settings_refuses_null_for_a_setting_that_cannot_be_unset(api, field):
    """Stored, a null would be an empty value no later read could parse."""
    response = api.put("/api/settings", headers=AUTH, json={field: None})

    assert response.status_code == 422
    assert api.get("/api/settings", headers=AUTH).status_code == 200


def test_update_settings_rejects_a_bad_value_with_the_validator_message(api):
    response = api.put("/api/settings", headers=AUTH, json={"bitrate": 0})

    assert response.status_code == 422
    assert "bitrate" in response.json()["detail"]
    assert Settings.from_db().bitrate == Settings().bitrate


def test_update_settings_rejects_an_unknown_key(api):
    response = api.put("/api/settings", headers=AUTH, json={"bitrat": 32})

    assert response.status_code == 422


def test_update_settings_rejects_the_wrong_type(api):
    response = api.put("/api/settings", headers=AUTH, json={"bitrate": "lots"})

    assert response.status_code == 422


def test_update_settings_anchors_a_relative_folder(api):
    body = api.put("/api/settings", headers=AUTH, json={"audiobook_folder": "books"}).json()

    assert body["audiobook_folder"].endswith("/books")
    assert body["audiobook_folder"].startswith("/")


def test_update_settings_applies_debug_to_the_logger_at_once(api):
    root = logging.getLogger()
    before = root.level
    try:
        api.put("/api/settings", headers=AUTH, json={"debug": True})
        assert root.level == logging.DEBUG
        api.put("/api/settings", headers=AUTH, json={"debug": False})
        assert root.level == logging.INFO
    finally:
        root.setLevel(before)


# --- wiring ----------------------------------------------------------------------


def test_lifespan_starts_and_stops_the_scheduler(db):
    scheduler = FakeScheduler()
    app = create_app(scheduler=scheduler, state=RunState(), api_token=ApiToken(TOKEN, "stored"))

    with TestClient(app):
        assert (scheduler.started, scheduler.stopped) == (1, 0)

    assert (scheduler.started, scheduler.stopped) == (1, 1)


def test_cors_allows_the_configured_origin(db):
    app = create_app(
        scheduler=FakeScheduler(),
        state=RunState(),
        api_token=ApiToken(TOKEN, "stored"),
        cors_origins=["http://ui:5173"],
    )
    client = TestClient(app)

    response = client.options(
        "/api/status",
        headers={"Origin": "http://ui:5173", "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://ui:5173"
    # ... with the session cookie, for a web app served from that origin
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_is_off_unless_configured(api):
    response = api.get("/api/health", headers={"Origin": "http://ui:5173"})

    assert "access-control-allow-origin" not in response.headers


# --- accounts --------------------------------------------------------------------


def _account(name="Alex (UK)", country_code="uk", *, auth=None, **kwargs):
    if auth is None:
        auth = {"locale_code": country_code, "access_token": "Atna|token"}
    return database.add_account(name, country_code, auth=auth, customer_name="Alex", **kwargs)


def test_list_accounts_never_shows_the_credentials(api, monkeypatch):
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-09-12T10:00:00+00:00")
    first = _account()
    pending = database.add_account("Pending", "us", auth=None)

    response = api.get("/api/accounts", headers=AUTH)

    assert response.json() == [
        {
            "id": first,
            "name": "Alex (UK)",
            "country_code": "uk",
            "customer_name": "Alex",
            "enabled": True,
            "monitor_existing": True,
            "needs_login": False,
            "created_at": "2026-09-12T10:00:00+00:00",
            "last_synced_at": None,
        },
        {
            "id": pending,
            "name": "Pending",
            "country_code": "us",
            "customer_name": None,
            "enabled": True,
            "monitor_existing": True,
            "needs_login": True,
            "created_at": "2026-09-12T10:00:00+00:00",
            "last_synced_at": None,
        },
    ]
    assert "Atna|token" not in response.text


def test_status_lists_the_accounts(api):
    _account()

    body = api.get("/api/status", headers=AUTH).json()

    assert [a["name"] for a in body["accounts"]] == ["Alex (UK)"]


def test_get_account(api):
    account_id = _account()

    assert api.get(f"/api/accounts/{account_id}", headers=AUTH).json()["name"] == "Alex (UK)"
    response = api.get("/api/accounts/99", headers=AUTH)
    assert response.status_code == 404
    assert response.json() == {"detail": "No account with id 99"}


def test_patch_account_changes_only_what_is_sent(api):
    account_id = _account()

    body = api.patch(f"/api/accounts/{account_id}", headers=AUTH, json={"enabled": False}).json()

    assert (body["name"], body["enabled"]) == ("Alex (UK)", False)
    assert database.get_account(account_id).enabled is False
    assert api.patch("/api/accounts/99", headers=AUTH, json={"enabled": False}).status_code == 404


@pytest.mark.parametrize("body", [{"name": ""}, {"country_code": "us"}, {"auth": {}}])
def test_patch_account_rejects_what_cannot_be_changed(api, body):
    account_id = _account()

    assert api.patch(f"/api/accounts/{account_id}", headers=AUTH, json=body).status_code == 422


def test_delete_account_removes_it_and_its_rows(api):
    account_id = _account()

    response = api.delete(f"/api/accounts/{account_id}", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    assert database.get_account(account_id) is None
    assert api.delete(f"/api/accounts/{account_id}", headers=AUTH).status_code == 404


def test_delete_account_can_deregister_the_device_first(api, monkeypatch):
    account_id = _account()
    calls = []

    class FakeAuthenticator:
        def deregister_device(self):
            calls.append("deregistered")

    monkeypatch.setattr(api_accounts, "authenticator_for", lambda account: FakeAuthenticator())

    api.delete(f"/api/accounts/{account_id}", headers=AUTH, params={"deregister": "true"})

    assert calls == ["deregistered"]
    assert database.get_account(account_id) is None


def test_delete_account_still_removes_it_when_deregistering_fails(api, monkeypatch, caplog):
    """A login that no longer works cannot deregister itself; the user still wants it gone."""
    account_id = _account()

    class FakeAuthenticator:
        def deregister_device(self):
            raise RuntimeError("token expired")

    monkeypatch.setattr(api_accounts, "authenticator_for", lambda account: FakeAuthenticator())

    response = api.delete(f"/api/accounts/{account_id}", headers=AUTH, params={"deregister": "true"})

    assert response.status_code == 200
    assert database.get_account(account_id) is None
    assert "Could not deregister" in caplog.text


def test_delete_account_does_not_try_to_deregister_one_without_credentials(api, monkeypatch):
    account_id = database.add_account("Pending", "us", auth=None)
    monkeypatch.setattr(api_accounts, "authenticator_for", lambda account: pytest.fail("nothing to deregister"))

    assert api.delete(f"/api/accounts/{account_id}", headers=AUTH, params={"deregister": "true"}).status_code == 200


def test_delete_account_is_refused_while_the_run_is_on_it(api):
    account_id = _account()
    api.state.begin(5)
    api.state.set_account({"id": account_id, "name": "Main", "country_code": "uk"})

    response = api.delete(f"/api/accounts/{account_id}", headers=AUTH)

    assert response.status_code == 409
    assert "cancel the run first" in response.json()["detail"]
    assert database.get_account(account_id) is not None


def test_delete_account_is_allowed_while_the_run_is_on_another(api):
    account_id = _account()
    other = _account("Other", "us")
    api.state.begin(5)
    api.state.set_account({"id": other, "name": "Other", "country_code": "us"})

    assert api.delete(f"/api/accounts/{account_id}", headers=AUTH).status_code == 200


def test_list_runs_can_be_limited_to_one_account(api):
    account_id = _account()
    other = _account("Other", "us")
    database.start_sync_run(account_id)
    database.start_sync_run(other)

    body = api.get("/api/sync/runs", headers=AUTH, params={"account_id": other}).json()

    assert [r["account_id"] for r in body["items"]] == [other]
    assert body["total"] == 1


# --- login ---------------------------------------------------------------------------


def test_marketplaces(api):
    body = api.get("/api/marketplaces", headers=AUTH).json()

    assert {"country_code": "uk", "domain": "co.uk", "name": "United Kingdom"} in body
    assert len(body) >= 10


def _start(api, **body):
    response = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "uk", **body})
    assert response.status_code == 202, response.text
    return response.json()


def _confirmed(api, login_id):
    """Wait for the polling thread, then read the login back."""
    api.logins.join(login_id, timeout=5)
    response = api.get(f"/api/accounts/login/{login_id}", headers=AUTH)
    assert response.status_code == 200
    return response.json()


def test_login_with_a_code_starts_pending_with_the_code_to_enter(api):
    api.amazon.polls = [PENDING]  # then pending for ever; the fixture ends it

    body = _start(api)

    assert body["method"] == "code"
    assert body["status"] == "pending"
    assert body["code"] == "ABCDE"
    assert body["verification_url"] == "https://www.amazon.co.uk/code"
    assert body["url"] is None
    assert body["account"] is None
    assert body["error"] is None
    assert body["expires_at"].endswith("+00:00")
    # Amazon was asked for the code pair for the marketplace
    assert api.amazon.requests[0].url.host == "api.amazon.co.uk"
    # ...and the status can be polled
    status = api.get(f"/api/accounts/login/{body['login_id']}", headers=AUTH)
    assert status.status_code == 200
    assert status.json()["status"] == "pending"


def test_login_with_a_code_becomes_the_account_once_confirmed(api):
    api.amazon.polls = [PENDING, registered_response()]

    started = _start(api, monitor_existing=False)
    body = _confirmed(api, started["login_id"])

    assert body["status"] == "confirmed"
    assert body["error"] is None
    account = body["account"]
    assert (account["name"], account["country_code"], account["monitor_existing"], account["needs_login"]) == (
        "Alex (UK)",
        "uk",
        False,
        False,
    )
    assert database.get_account(account["id"]).auth["access_token"] == "Atna|access"
    assert [a.id for a in database.get_accounts()] == [account["id"]]


def _recording_replace(replaced):
    """A stand-in for `replace_account_auth` that notes its call and stores the credentials."""

    def replace(account, auth, *, deregister_previous):
        replaced.append((account.id, deregister_previous))
        database.save_account_auth(account.id, auth.to_dict())
        return StoredLogin(account.id, replaced=True)

    return replace


@pytest.mark.parametrize("through", ["store_login", "account_id"])
def test_a_login_says_how_many_parked_books_it_queued(api, monkeypatch, through):
    """A withdrawn title stays parked, so the count is what the sign-in screen can truthfully say."""
    monkeypatch.setattr(Authenticator, "deregister_device", lambda self: None)
    api.amazon.polls = [registered_response()]
    first = _confirmed(api, _start(api)["login_id"])
    account_id = first["account"]["id"]
    assert first["requeued"] == 0
    (refused, _) = _shelve(account_id, make_book("B001"), make_book("B002", is_consumable=False))
    claim = database.claim_book_for_download(refused.id)
    database.mark_book_unavailable(refused.id, "Denied", claim=claim)

    api.amazon.polls = [registered_response()]
    start = {"account_id": account_id} if through == "account_id" else {}
    assert _confirmed(api, _start(api, **start)["login_id"])["requeued"] == 1

    api.amazon.polls = [registered_response()]
    assert _confirmed(api, _start(api, **start)["login_id"])["requeued"] == 0


def test_login_for_an_account_that_holds_it_replaces_its_credentials(api, monkeypatch):
    monkeypatch.setattr(Authenticator, "deregister_device", lambda self: None)
    api.amazon.polls = [registered_response()]
    first = _confirmed(api, _start(api, name="Main")["login_id"])
    assert first["replaced"] is False
    (book,) = _shelve(first["account"]["id"], make_book("B001"))

    api.amazon.polls = [registered_response()]
    second = _confirmed(api, _start(api, name="Other", monitor_existing=False)["login_id"])

    assert second["status"] == "confirmed"
    assert second["replaced"] is True
    assert second["account"]["id"] == first["account"]["id"]
    assert second["account"]["name"] == "Main"
    assert [a.id for a in database.get_accounts()] == [first["account"]["id"]]
    assert database.get_book(book.id) is not None


def test_sign_in_again_with_a_login_another_account_holds_fails_and_discards_the_device(api, monkeypatch):
    deregistered = []
    monkeypatch.setattr(Authenticator, "deregister_device", lambda self: deregistered.append(self.access_token))
    api.amazon.polls = [registered_response()]
    holder = _confirmed(api, _start(api, name="Main")["login_id"])["account"]
    placeholder = database.add_account("Audible", "", auth=None)

    api.amazon.polls = [registered_response()]
    body = _confirmed(api, _start(api, account_id=placeholder)["login_id"])

    assert body["status"] == "failed"
    assert "Main already holds this Audible login" in body["error"]
    assert deregistered == ["Atna|access"]
    assert database.get_account(placeholder).needs_login is True
    assert database.get_account(holder["id"]).needs_login is False


def test_sign_in_again_as_another_customer_fails_and_keeps_the_account_working(api, monkeypatch):
    deregistered = []
    monkeypatch.setattr(Authenticator, "deregister_device", lambda self: deregistered.append(self.access_token))
    account_id = database.add_account(
        "Main", "uk", auth={**AUTH_BLOB, "access_token": "Atna|mine", "customer_info": {"user_id": "someone-else"}}
    )

    api.amazon.polls = [registered_response()]
    body = _confirmed(api, _start(api, account_id=account_id)["login_id"])

    assert body["status"] == "failed"
    assert "different Amazon login from the one Main belongs to" in body["error"]
    assert "log in again" not in body["error"]
    assert database.get_account(account_id).auth["access_token"] == "Atna|mine"
    assert deregistered == ["Atna|access"]  # the new device, and not the working one


def test_browser_sign_in_for_an_account_removed_meanwhile_does_not_blame_amazon(api, monkeypatch):
    monkeypatch.setattr(Authenticator, "deregister_device", lambda self: None)
    _fake_complete(monkeypatch)
    account_id = database.add_account("Main", "uk", auth=dict(AUTH_BLOB))
    login_id = _start(api, method="browser", account_id=account_id)["login_id"]
    database.delete_account(account_id)

    response = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": LANDING})

    assert response.status_code == 409
    assert "was removed" in response.json()["detail"]
    assert "Amazon rejected" not in response.json()["detail"]


def test_browser_sign_in_that_cannot_be_stored_does_not_ask_to_paste_again(api, monkeypatch):
    """The login is over and its device gone by then, so a 400 would invite a paste that can only 404."""
    monkeypatch.setattr(Authenticator, "deregister_device", lambda self: None)
    _fake_complete(monkeypatch)
    placeholder = database.add_account("Audible", "", auth=None)
    login_id = _start(api, method="browser", account_id=placeholder)["login_id"]
    database.update_account(placeholder, country_code="us")  # another sign-in got there first

    response = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": LANDING})

    assert response.status_code == 409
    assert "US account" in response.json()["detail"]
    assert api.get(f"/api/accounts/login/{login_id}", headers=AUTH).json()["status"] == "failed"


def test_browser_sign_in_with_a_login_another_account_holds_is_a_409(api, monkeypatch):
    monkeypatch.setattr(Authenticator, "deregister_device", lambda self: None)
    _fake_complete(monkeypatch)
    api.amazon.polls = [registered_response()]
    _confirmed(api, _start(api, name="Main")["login_id"])
    placeholder = database.add_account("Audible", "", auth=None)
    login_id = _start(api, method="browser", account_id=placeholder)["login_id"]

    response = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": LANDING})

    assert response.status_code == 409
    assert "Main already holds this Audible login" in response.json()["detail"]
    assert api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": LANDING}).status_code == 404


def test_login_with_a_code_takes_a_name(api):
    api.amazon.polls = [registered_response()]

    body = _confirmed(api, _start(api, name="Main")["login_id"])

    assert body["account"]["name"] == "Main"
    assert body["account"]["monitor_existing"] is True


def test_login_with_a_code_amazon_refuses_is_failed(api):
    api.amazon.polls = [REJECTED]

    body = _confirmed(api, _start(api)["login_id"])

    assert body["status"] == "failed"
    assert "refused the device registration" in body["error"]
    assert body["account"] is None
    assert database.get_accounts() == []


def test_login_when_amazon_will_not_issue_a_code(api):
    api.amazon.code_pair = REJECTED

    response = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "uk"})

    assert response.status_code == 502
    assert "refused to create a device code" in response.json()["detail"]


def test_login_when_amazon_cannot_be_reached(api):
    api.amazon.code_pair = httpx.ConnectError("no route")

    response = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "uk"})

    assert response.status_code == 502
    assert "Could not reach Amazon" in response.json()["detail"]


def test_login_rejects_an_unknown_marketplace(api):
    response = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "xx"})

    assert response.status_code == 422
    assert "unknown marketplace" in response.json()["detail"]
    assert api.amazon.requests == []


def test_login_rejects_an_unknown_method(api):
    response = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "uk", "method": "carrier pigeon"})

    assert response.status_code == 422


def test_cancel_a_pending_code_login(api):
    login_id = _start(api)["login_id"]  # pending for ever

    response = api.delete(f"/api/accounts/login/{login_id}", headers=AUTH)
    api.logins.join(login_id, timeout=5)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["account"] is None
    assert api.get(f"/api/accounts/login/{login_id}", headers=AUTH).json()["status"] == "cancelled"
    assert database.get_accounts() == []


def test_cancel_reports_a_code_confirmed_before_it(api):
    api.amazon.polls = [registered_response()]
    login_id = _start(api)["login_id"]
    api.logins.join(login_id, timeout=5)

    body = api.delete(f"/api/accounts/login/{login_id}", headers=AUTH).json()

    assert body["status"] == "confirmed"
    assert body["account"]["name"] == "Alex (UK)"
    assert [a.id for a in database.get_accounts()] == [body["account"]["id"]]


def test_cancel_a_browser_login_then_its_address_is_refused(api, monkeypatch):
    calls = _fake_complete(monkeypatch)
    login_id = _start(api, method="browser")["login_id"]

    assert api.delete(f"/api/accounts/login/{login_id}", headers=AUTH).json()["status"] == "cancelled"
    response = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": LANDING})

    assert response.status_code == 404
    assert calls == []
    assert database.get_accounts() == []


def test_cancel_an_unknown_login(api):
    response = api.delete("/api/accounts/login/nope", headers=AUTH)

    assert response.status_code == 404
    assert "start again" in response.json()["detail"]


def test_cancel_needs_auth(api):
    assert TestClient(api.app).delete("/api/accounts/login/nope").status_code == 401


def test_login_status_of_an_unknown_login(api):
    response = api.get("/api/accounts/login/nope", headers=AUTH)

    assert response.status_code == 404
    assert "start again" in response.json()["detail"]


def test_login_with_a_code_can_replace_an_existing_accounts_credentials(api, monkeypatch):
    """The recovery for a registration Audible stopped licensing: the library stays attached."""
    api.amazon.polls = [registered_response()]
    account_id = _account(auth={"locale_code": "uk", "access_token": "Atna|old"})
    _shelve(account_id, make_book("B001"))
    replaced = []
    monkeypatch.setattr(
        api_accounts,
        "replace_account_auth",
        _recording_replace(replaced),
    )

    started = _start(api, account_id=account_id, deregister_previous=False)
    body = _confirmed(api, started["login_id"])

    assert body["status"] == "confirmed"
    assert body["account"]["id"] == account_id
    assert replaced == [(account_id, False)]
    assert database.get_account(account_id).auth["access_token"] == "Atna|access"
    assert len(database.get_accounts()) == 1
    assert database.get_books(account_id=account_id)[0].asin == "B001"


def test_login_for_an_unknown_account_is_a_404_before_amazon_is_asked(api):
    response = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "uk", "account_id": 99})

    assert response.status_code == 404
    assert "No account with id 99" in response.json()["detail"]
    assert api.amazon.requests == []


def test_login_for_another_marketplaces_account_is_a_422_before_amazon_is_asked(api):
    account_id = database.add_account("Alex (US)", "us", auth=None)

    response = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "uk", "account_id": account_id})

    assert response.status_code == 422
    assert "US account" in response.json()["detail"]
    assert api.amazon.requests == []


# the browser method


LANDING = "https://www.amazon.co.uk/ap/maplanding?openid.oa2.authorization_code=X"


def _fake_complete(monkeypatch, *, raises=None):

    calls = []

    def fake(pending, response_url):
        calls.append((pending.country_code, response_url))
        if raises is not None:
            raise raises
        return Authenticator.from_dict(dict(AUTH_BLOB))

    monkeypatch.setattr(login_module, "complete_login", fake)
    return calls


def test_browser_login_step_one_returns_the_sign_in_url(api):
    body = _start(api, method="browser")

    assert body["method"] == "browser"
    assert body["status"] == "pending"
    assert body["url"].startswith("https://www.amazon.co.uk/ap/signin?")
    assert body["code"] is None
    assert body["verification_url"] is None
    assert body["expires_at"].endswith("+00:00")
    # Nothing goes to Amazon until the address is pasted
    assert api.amazon.requests == []
    assert api.get(f"/api/accounts/login/{body['login_id']}", headers=AUTH).json()["status"] == "pending"


def test_browser_login_step_two_creates_the_account(api, monkeypatch):
    calls = _fake_complete(monkeypatch)
    login_id = _start(api, method="browser", monitor_existing=False)["login_id"]

    response = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": LANDING})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "confirmed"
    account = body["account"]
    assert (account["name"], account["country_code"], account["monitor_existing"], account["needs_login"]) == (
        "Alex (UK)",
        "uk",
        False,
        False,
    )
    assert calls == [("uk", LANDING)]
    assert database.get_account(account["id"]).auth["access_token"] == "Atna|access"
    # The status endpoint agrees; a second paste is refused
    assert api.get(f"/api/accounts/login/{login_id}", headers=AUTH).json()["status"] == "confirmed"
    assert api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": "x"}).status_code == 404


def test_browser_login_step_two_takes_the_name_given_at_the_start(api, monkeypatch):
    _fake_complete(monkeypatch)
    login_id = _start(api, method="browser", name="Main")["login_id"]

    body = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": LANDING}).json()

    assert body["account"]["name"] == "Main"


def test_browser_login_step_two_for_an_unknown_or_expired_login(api):
    response = api.post("/api/accounts/login/nope", headers=AUTH, json={"response_url": "u"})

    assert response.status_code == 404
    assert "start again" in response.json()["detail"]


def test_browser_login_step_two_with_a_url_that_carries_no_code_keeps_the_login_open(api, monkeypatch):
    _fake_complete(monkeypatch, raises=ValueError("that URL carries no authorization code"))
    login_id = _start(api, method="browser")["login_id"]

    response = api.post(
        f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": "https://www.amazon.co.uk/"}
    )

    assert response.status_code == 400
    assert "no authorization code" in response.json()["detail"]
    assert database.get_accounts() == []
    assert api.get(f"/api/accounts/login/{login_id}", headers=AUTH).json()["status"] == "pending"


def test_browser_login_step_two_when_amazon_rejects_the_code(api, monkeypatch, caplog):
    _fake_complete(monkeypatch, raises=Exception({"error": "InvalidValue"}))
    login_id = _start(api, method="browser")["login_id"]

    response = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": LANDING})

    assert response.status_code == 502
    assert "Amazon rejected the login" in response.json()["detail"]
    assert "InvalidValue" in caplog.text
    body = api.get(f"/api/accounts/login/{login_id}", headers=AUTH).json()
    assert body["status"] == "failed"
    assert "InvalidValue" in body["error"]


def test_browser_login_step_two_refuses_a_code_login(api):
    api.amazon.polls = [registered_response()]
    login_id = _start(api)["login_id"]
    api.logins.join(login_id, timeout=5)

    response = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": "u"})

    assert response.status_code == 404


def test_browser_login_step_two_rejects_account_fields(api):
    """They belong to step one now, for both methods."""
    login_id = _start(api, method="browser")["login_id"]

    response = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": "u", "name": "Main"})

    assert response.status_code == 422


def test_browser_login_can_replace_an_existing_accounts_credentials(api, monkeypatch):
    _fake_complete(monkeypatch)
    account_id = _account(auth={"locale_code": "uk", "access_token": "Atna|old"})
    replaced = []
    monkeypatch.setattr(
        api_accounts,
        "replace_account_auth",
        _recording_replace(replaced),
    )
    login_id = _start(api, method="browser", account_id=account_id)["login_id"]

    response = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": LANDING})

    assert response.status_code == 201
    assert response.json()["account"]["id"] == account_id
    assert replaced == [(account_id, True)]
    assert len(database.get_accounts()) == 1


# --- books ---------------------------------------------------------------------------

from audible_sync.model import BookStatus  # noqa: E402
from tests.conftest import make_book, mark_downloaded  # noqa: E402


def _shelve(account_id, *books, **kwargs):
    database.update_books(account_id, list(books), **kwargs)
    return [database.get_book_by_asin(b.asin, account_id=account_id) for b in books]


def test_list_books_pages_and_filters(api):
    account_id = _account()
    other = _account("Other", "us")
    _shelve(
        account_id,
        make_book("B001", "Dune", authors=["Frank Herbert"]),
        make_book("B002", "Foundation", date_added="2025-01-01T00:00:00Z"),
    )
    _shelve(other, make_book("B003", "Dune Messiah", authors=["Frank Herbert"]), monitor_new=False)

    body = api.get("/api/books", headers=AUTH, params={"page_size": 2}).json()
    assert [b["asin"] for b in body["items"]] == ["B002", "B001"]
    assert (body["total"], body["page"], body["page_size"]) == (3, 1, 2)

    body = api.get("/api/books", headers=AUTH, params={"q": "dune", "sort": "title", "order": "asc"}).json()
    assert [b["title"] for b in body["items"]] == ["Dune", "Dune Messiah"]

    body = api.get("/api/books", headers=AUTH, params={"account_id": other, "monitored": "false"}).json()
    assert [b["asin"] for b in body["items"]] == ["B003"]
    assert body["items"][0]["monitored"] is False

    body = api.get("/api/books", headers=AUTH, params={"status": "waiting_download", "page": 2, "page_size": 2}).json()
    assert len(body["items"]) == 1


@pytest.mark.parametrize(
    "params", [{"sort": "colour"}, {"order": "sideways"}, {"page": 0}, {"page_size": 201}, {"status": "lost"}]
)
def test_list_books_rejects_bad_parameters(api, params):
    assert api.get("/api/books", headers=AUTH, params=params).status_code == 422


def test_get_book_gives_the_description_as_plain_text(api):
    account_id = _account()
    (book,) = _shelve(
        account_id, make_book("B001", description="<p>A <b>tale</b> &amp; more.</p><script>x()</script><p>Two.</p>")
    )

    body = api.get(f"/api/books/{book.id}", headers=AUTH).json()

    assert body["description"] == "A tale & more.\n\nTwo."
    # And with its formatting, rebuilt from the allow-list: no script, nothing else of Audible's
    assert body["description_html"] == "<p>A <strong>tale</strong> &amp; more.</p><p>Two.</p>"


def test_get_book_without_a_description_gives_an_empty_one(api):
    (book,) = _shelve(_account(), make_book("B001", description=None))

    body = api.get(f"/api/books/{book.id}", headers=AUTH).json()

    assert (body["description"], body["description_html"]) == ("", "")


def test_the_book_list_leaves_the_description_out(api):
    _shelve(_account(), make_book("B001", description="<p>Long.</p>"))

    (item,) = api.get("/api/books", headers=AUTH).json()["items"]

    assert "description" not in item
    assert "description_html" not in item


def test_patch_gives_the_description_too(api):
    (book,) = _shelve(_account(), make_book("B001", description="<p>Told.</p>"))

    body = api.patch(f"/api/books/{book.id}", headers=AUTH, json={"monitored": False}).json()

    assert body["description"] == "Told."


def test_refresh_gives_the_description_it_read(api, monkeypatch):
    (book,) = _shelve(_account(), make_book("B001", description=None))
    monkeypatch.setattr(
        api_books.library,
        "refresh",
        lambda b: (
            database.update_books(b.account_id, [make_book("B001", description="<p>New.</p>")])
            or database.get_book(b.id)
        ),
    )

    assert api.post(f"/api/books/{book.id}/refresh", headers=AUTH).json()["description"] == "New."


def test_get_book_carries_the_series_it_files_under(api):
    account_id = _account()
    (book,) = _shelve(
        account_id,
        make_book(
            "B001",
            "Dune",
            series=[
                {"title": "The Dune Sequence", "sequence": "12", "series_asin": "S2"},
                {"title": "Dune", "sequence": "1", "series_asin": "S1"},
            ],
        ),
    )

    body = api.get(f"/api/books/{book.id}", headers=AUTH).json()

    assert body["primary_series"] == {"title": "Dune", "sequence": "1", "series_asin": "S1"}
    # The list is stored as given; the primary one is worked out, not first
    assert [s["title"] for s in body["series"]] == ["The Dune Sequence", "Dune"]
    assert body["id"] == book.id
    assert body["account_id"] == account_id
    assert body["status"] == "waiting_download"
    assert body["file_path"] is None
    assert api.get("/api/books/999", headers=AUTH).status_code == 404


def test_patch_book_monitored(api):
    (book,) = _shelve(_account(), make_book("B001"))

    body = api.patch(f"/api/books/{book.id}", headers=AUTH, json={"monitored": False}).json()

    assert body["monitored"] is False
    assert database.get_book(book.id).monitored is False
    assert api.patch(f"/api/books/{book.id}", headers=AUTH, json={"status": "failed"}).status_code == 422
    assert api.patch("/api/books/999", headers=AUTH, json={"monitored": True}).status_code == 404


def _downloaded(api, tmp_path, status=BookStatus.DOWNLOADED):
    """A downloaded book whose files exist under the configured library folder."""
    library_folder = tmp_path / "audiobooks"
    api.put(
        "/api/settings",
        headers=AUTH,
        json={"audiobook_folder": str(library_folder), "download_folder": str(tmp_path / "dl")},
    )
    (book,) = _shelve(_account(), make_book("B001"))
    folder = library_folder / "Author One" / "Title"
    folder.mkdir(parents=True)
    audio, cover = folder / "Title.m4b", folder / "Title_cover.jpg"
    audio.write_bytes(b"a")
    cover.write_bytes(b"\xff\xd8cover")
    mark_downloaded(book.id, "m4b", file_path=str(audio), cover_path=str(cover))
    if status is not BookStatus.DOWNLOADED:
        database.claim_book_for_download(book.id) if status is BookStatus.DOWNLOADING else None
    return database.get_book(book.id), audio, cover


def test_delete_files_removes_them_and_unmonitors(api, tmp_path):
    book, audio, cover = _downloaded(api, tmp_path)

    response = api.post(f"/api/books/{book.id}/delete-files", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "removed": [str(audio), str(cover)]}
    assert not audio.exists() and not cover.exists()
    after = api.get(f"/api/books/{book.id}", headers=AUTH).json()
    assert (after["status"], after["monitored"], after["file_path"]) == ("waiting_download", False, None)


def test_redownload_removes_the_files_and_queues(api, tmp_path):
    book, audio, _ = _downloaded(api, tmp_path)

    response = api.post(f"/api/books/{book.id}/redownload", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert not audio.exists()
    after = api.get(f"/api/books/{book.id}", headers=AUTH).json()
    assert (after["status"], after["monitored"], after["attempts"]) == ("waiting_download", True, 0)


@pytest.mark.parametrize("action", ["delete-files", "redownload", "retry"])
def test_file_actions_conflict_while_a_run_holds_the_book(api, tmp_path, action):
    (book,) = _shelve(_account(), make_book("B001"))
    database.claim_book_for_download(book.id)

    response = api.post(f"/api/books/{book.id}/{action}", headers=AUTH)

    assert response.status_code == 409
    assert "cancel the run first" in response.json()["detail"]


@pytest.mark.parametrize("action", ["delete-files", "redownload", "retry", "refresh"])
def test_book_actions_404_for_an_unknown_book(api, action):
    assert api.post(f"/api/books/999/{action}", headers=AUTH).status_code == 404


def test_retry_queues_a_failed_book(api):
    (book,) = _shelve(_account(), make_book("B001"))
    claim = database.claim_book_for_download(book.id)
    database.mark_book_failed(book.id, "boom", max_attempts=1, claim=claim)

    response = api.post(f"/api/books/{book.id}/retry", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"status": "queued", "removed": []}
    after = database.get_book(book.id)
    assert (after.status, after.attempts, after.last_error) == (BookStatus.WAITING_DOWNLOAD, 0, None)


def test_retry_refuses_a_downloaded_book(api, tmp_path):
    book, _, _ = _downloaded(api, tmp_path)

    response = api.post(f"/api/books/{book.id}/retry", headers=AUTH)

    assert response.status_code == 409
    assert "use redownload" in response.json()["detail"]


def _park(book):
    claim = database.claim_book_for_download(book.id)
    database.mark_book_unavailable(book.id, "Denied", claim=claim)


def test_retry_unavailable_queues_the_parked_books_of_one_account(api):
    account_id = _account()
    other = _account("Other", "us")
    mine, withdrawn = _shelve(account_id, make_book("B001"), make_book("B002", is_consumable=False))
    (theirs,) = _shelve(other, make_book("B003"))
    _park(mine)
    _park(theirs)

    response = api.post("/api/books/retry-unavailable", json={"account_id": account_id}, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"requeued": 1}
    assert database.get_book(mine.id).status is BookStatus.WAITING_DOWNLOAD
    assert database.get_book(withdrawn.id).status is BookStatus.UNAVAILABLE
    assert database.get_book(theirs.id).status is BookStatus.UNAVAILABLE


def test_retry_unavailable_with_no_body_means_every_account(api):
    account_id = _account()
    other = _account("Other", "us")
    for book in _shelve(account_id, make_book("B001")) + _shelve(other, make_book("B002")):
        _park(book)

    response = api.post("/api/books/retry-unavailable", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"requeued": 2}
    assert api.post("/api/books/retry-unavailable", json={}, headers=AUTH).json() == {"requeued": 0}


def test_retry_unavailable_404s_for_an_unknown_account(api):
    assert api.post("/api/books/retry-unavailable", json={"account_id": 999}, headers=AUTH).status_code == 404


def test_retry_unavailable_rejects_an_unknown_field(api):
    response = api.post("/api/books/retry-unavailable", json={"status": "failed"}, headers=AUTH)
    assert response.status_code == 422


def test_bulk_by_ids(api):
    waiting, failed = _shelve(_account(), make_book("B001"), make_book("B002"), monitor_new=False)
    claim = database.claim_book_for_download(failed.id)
    database.mark_book_failed(failed.id, "boom", max_attempts=1, claim=claim)

    response = api.post(
        "/api/books/bulk", json={"action": "monitor", "ids": [waiting.id, failed.id, 999]}, headers=AUTH
    )

    assert response.status_code == 200
    assert response.json() == {"updated": 2, "queued": 1, "skipped": [{"id": 999, "reason": "not_found"}]}
    assert database.get_book(waiting.id).monitored and database.get_book(failed.id).monitored


def test_bulk_by_filter_acts_on_what_the_listing_matches(api):
    account_id = _account()
    other = _account("Other", "us")
    _shelve(account_id, make_book("B001", "Dune"), make_book("B002", "Foundation"), monitor_new=False)
    (theirs,) = _shelve(other, make_book("B003", "Dune Messiah"), monitor_new=False)
    query = {"account_id": account_id, "monitored": False, "q": "dune"}
    listed = api.get("/api/books", params=query, headers=AUTH).json()

    response = api.post("/api/books/bulk", json={"action": "monitor", "filter": query}, headers=AUTH)

    assert response.json() == {"updated": listed["total"], "queued": 1, "skipped": []}
    assert [b["asin"] for b in api.get("/api/books", params={"monitored": True}, headers=AUTH).json()["items"]] == [
        "B001"
    ]
    assert database.get_book(theirs.id).monitored is False


def test_bulk_skips_a_book_a_run_holds(api):
    (book,) = _shelve(_account(), make_book("B001"))
    database.claim_book_for_download(book.id)

    response = api.post("/api/books/bulk", json={"action": "unmonitor", "ids": [book.id]}, headers=AUTH)

    assert response.json() == {"updated": 0, "queued": 0, "skipped": [{"id": book.id, "reason": "busy"}]}


def test_bulk_delete_files(api, tmp_path):
    book, audio, _ = _downloaded(api, tmp_path)

    response = api.post("/api/books/bulk", json={"action": "delete_files", "ids": [book.id]}, headers=AUTH)

    assert response.json() == {"updated": 1, "queued": 0, "skipped": []}
    assert not audio.exists()
    assert database.get_book(book.id).monitored is False


@pytest.mark.parametrize(
    "body",
    [
        {"action": "monitor"},
        {"action": "monitor", "ids": [1], "filter": {}},
        {"action": "explode", "ids": [1]},
        {"action": "monitor", "filter": {"colour": "red"}},
        {"action": "monitor", "ids": [1], "extra": True},
        {"action": "monitor", "ids": [0]},
        {"action": "monitor", "ids": [10**20]},
    ],
)
def test_bulk_rejects_a_bad_body(api, body):
    assert api.post("/api/books/bulk", json=body, headers=AUTH).status_code == 422


def test_bulk_filter_404s_for_an_unknown_account(api):
    response = api.post("/api/books/bulk", json={"action": "monitor", "filter": {"account_id": 999}}, headers=AUTH)
    assert response.status_code == 404


def test_bulk_needs_auth(api):
    assert api.post("/api/books/bulk", json={"action": "monitor", "ids": []}).status_code == 401


def test_refresh_re_reads_the_book(api, monkeypatch):
    (book,) = _shelve(_account(), make_book("B001", "Old"))
    monkeypatch.setattr(
        api_books.library,
        "refresh",
        lambda b: database.update_books(b.account_id, [make_book("B001", "New")]) or database.get_book(b.id),
    )

    body = api.post(f"/api/books/{book.id}/refresh", headers=AUTH).json()

    assert body["title"] == "New"


def test_refresh_reports_audible_trouble_as_a_502(api, monkeypatch):
    (book,) = _shelve(_account(), make_book("B001"))

    def down(b):
        raise RuntimeError("timeout")

    monkeypatch.setattr(api_books.library, "refresh", down)

    response = api.post(f"/api/books/{book.id}/refresh", headers=AUTH)

    assert response.status_code == 502
    assert "timeout" in response.json()["detail"]


def test_refresh_refuses_a_book_whose_account_needs_a_login(api):
    account_id = database.add_account("Pending", "uk", auth=None)
    (book,) = _shelve(account_id, make_book("B001"))

    response = api.post(f"/api/books/{book.id}/refresh", headers=AUTH)

    assert response.status_code == 409
    assert "no credentials" in response.json()["detail"]


def test_cover_serves_the_file_once_downloaded(api, tmp_path):
    book, _, cover = _downloaded(api, tmp_path)

    response = api.get(f"/api/books/{book.id}/cover", headers=AUTH)

    assert response.status_code == 200
    assert response.content == cover.read_bytes()


def test_cover_redirects_to_audible_before_that(api):
    (book,) = _shelve(_account(), make_book("B001", cover_url="https://m.media-amazon.com/x.jpg"))

    response = api.get(f"/api/books/{book.id}/cover", headers=AUTH, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://m.media-amazon.com/x.jpg"


def test_cover_404_when_there_is_none(api):
    (book,) = _shelve(_account(), make_book("B001", cover_url=""))

    assert api.get(f"/api/books/{book.id}/cover", headers=AUTH).status_code == 404


# --- extras ----------------------------------------------------------------------------


def test_logs_returns_the_buffered_lines(api):
    log = logging.getLogger("tests.api.extras")
    log.setLevel(logging.INFO)
    log.addHandler(api.log_buffer)
    try:
        log.info("hello")
        log.warning("careful")
    finally:
        log.removeHandler(api.log_buffer)

    body = api.get("/api/logs", headers=AUTH).json()
    assert [(e["level"], e["message"]) for e in body["items"]][-2:] == [("INFO", "hello"), ("WARNING", "careful")]

    body = api.get("/api/logs", headers=AUTH, params={"level": "WARNING", "limit": 1}).json()
    assert [e["message"] for e in body["items"]] == ["careful"]


@pytest.mark.parametrize("params", [{"level": "LOUD"}, {"limit": 0}, {"limit": 1001}])
def test_logs_rejects_bad_parameters(api, params):
    assert api.get("/api/logs", headers=AUTH, params=params).status_code == 422


def test_logs_is_empty_without_a_buffer(db):
    app = create_app(scheduler=FakeScheduler(), state=RunState(), api_token=ApiToken(TOKEN, "stored"))

    assert TestClient(app).get("/api/logs", headers=AUTH).json() == {"items": []}


def test_stats(api, tmp_path, monkeypatch):
    account_id = _account()
    other = _account("Other", "us")
    (book,) = _shelve(account_id, make_book("B001"))
    _shelve(other, make_book("B002"), make_book("B003"), monitor_new=False)
    audio = tmp_path / "a.m4b"
    audio.write_bytes(b"x" * 1234)
    mark_downloaded(book.id, "m4b", file_path=str(audio))
    mark_downloaded(database.get_book_by_asin("B002", account_id=other).id, "m4b", file_path=str(tmp_path / "gone.m4b"))
    monkeypatch.setattr(database, "_utcnow", lambda: "2026-09-12T10:00:00+00:00")
    database.finish_sync_run(database.start_sync_run(account_id), outcome="success")

    body = api.get("/api/stats", headers=AUTH).json()

    assert body["books"]["total"] == 3
    assert body["books"]["by_status"]["downloaded"] == 2
    assert (body["books"]["monitored"], body["books"]["unmonitored"]) == (1, 2)
    assert body["bytes_on_disk"] == 1234  # the file that has gone counts for nothing
    assert body["next_run_at"] == "2026-09-12T18:00:00+00:00"
    assert body["last_run"]["outcome"] == "success"
    mine, theirs = body["accounts"]
    assert mine["account"]["name"] == "Alex (UK)"
    assert mine["books"]["total"] == 1
    assert mine["bytes_on_disk"] == 1234
    assert mine["last_run"]["account_id"] == account_id
    assert theirs["books"]["unmonitored"] == 2
    assert theirs["last_run"] is None
    assert "auth" not in mine["account"]


def test_stats_count_the_retryable_unavailable_books_and_the_monitored_by_status(api):
    account_id = _account()
    refused, withdrawn, unwanted = _shelve(
        account_id, make_book("B001"), make_book("B002", is_consumable=False), make_book("B003")
    )
    for book in (refused, withdrawn, unwanted):
        claim = database.claim_book_for_download(book.id)
        database.mark_book_unavailable(book.id, "Denied", claim=claim)
    database.set_monitored(unwanted.id, False)

    body = api.get("/api/stats", headers=AUTH).json()

    assert body["books"]["by_status"]["unavailable"] == 3
    assert body["books"]["monitored_by_status"]["unavailable"] == 2
    assert body["books"]["retryable_unavailable"] == 2  # the withdrawn title is the sync's
    assert body["accounts"][0]["books"]["retryable_unavailable"] == 2
    retried = api.post("/api/books/retry-unavailable", headers=AUTH, json={"account_id": account_id}).json()
    assert retried["requeued"] == 2
    assert api.get("/api/stats", headers=AUTH).json()["books"]["retryable_unavailable"] == 0


def test_stats_on_an_empty_database(api):
    body = api.get("/api/stats", headers=AUTH).json()

    assert body["accounts"] == []
    assert body["books"]["total"] == 0
    assert body["last_run"] is None


def test_webhook_test_sends_to_the_configured_url(api, monkeypatch):
    sent = []
    monkeypatch.setattr(api_settings, "send_webhook", lambda url, payload: sent.append((url, payload["event"])))
    api.put("/api/settings", headers=AUTH, json={"webhook_url": "https://hooks/configured"})

    response = api.post("/api/settings/webhook/test", headers=AUTH, json={})

    assert response.status_code == 200
    assert response.json() == {"status": "sent"}
    assert sent == [("https://hooks/configured", "test")]


def test_webhook_test_can_try_a_url_before_saving_it(api, monkeypatch):
    sent = []
    monkeypatch.setattr(api_settings, "send_webhook", lambda url, payload: sent.append(url))

    api.post("/api/settings/webhook/test", headers=AUTH, json={"url": "https://hooks/try"})

    assert sent == ["https://hooks/try"]


def test_webhook_test_without_a_url(api):
    response = api.post("/api/settings/webhook/test", headers=AUTH, json={})

    assert response.status_code == 400
    assert response.json() == {"detail": "No webhook URL configured"}


def test_webhook_test_reports_a_failure(api, monkeypatch):
    def down(url, payload):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(api_settings, "send_webhook", down)

    response = api.post("/api/settings/webhook/test", headers=AUTH, json={"url": "https://hooks/x"})

    assert response.status_code == 502
    assert "connection refused" in response.json()["detail"]


# --- the web app's build ---------------------------------------------------------


@pytest.fixture
def web(db, tmp_path):
    """An app serving a stand-in build: index.html, a hashed asset and a favicon."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Audible Sync</title>")
    (dist / "assets" / "app-1a2b.js").write_text("console.log('app')")
    (dist / "favicon.svg").write_text("<svg/>")
    (dist / "manifest.webmanifest").write_text('{"name": "Audible Sync"}')
    (dist / "sw.js").write_text("const BUILD = {};")
    (tmp_path / "secret.txt").write_text("outside")
    app = create_app(scheduler=FakeScheduler(), state=RunState(), api_token=ApiToken(TOKEN, "stored"), web_dir=dist)
    return TestClient(app)


@pytest.mark.parametrize("path", ["/", "/library", "/library?status=failed", "/books/12", "/settings/"])
def test_every_page_of_the_app_gets_index_html(web, path):
    response = web.get(path)

    assert response.status_code == 200
    assert "<title>Audible Sync</title>" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_an_asset_is_served_and_cached_for_good(web):
    response = web.get("/assets/app-1a2b.js")

    assert response.status_code == 200
    assert response.text == "console.log('app')"
    assert "immutable" in response.headers["cache-control"]


def test_a_file_outside_assets_is_served_without_the_long_cache(web):
    response = web.get("/favicon.svg")

    assert response.text == "<svg/>"
    assert "immutable" not in response.headers.get("cache-control", "")


@pytest.mark.parametrize(
    ("path", "media_type"),
    [("/manifest.webmanifest", "application/manifest+json"), ("/sw.js", "text/javascript")],
)
def test_the_manifest_and_service_worker_are_checked_on_every_load(web, path, media_type):
    # A changed sw.js is how an installed app finds a new build, so no cache may hide it
    response = web.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == media_type
    assert response.headers["cache-control"] == "no-cache"


def test_a_missing_asset_is_a_404_not_the_page(web):
    assert web.get("/assets/gone-9z9z.js").status_code == 404


@pytest.mark.parametrize("path", ["/api/nope", "/api", "/api/books/1/nothing"])
def test_an_unknown_api_path_is_json_never_the_page(web, path):
    response = web.get(path, headers=AUTH)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_the_api_still_answers_beside_the_app(web):
    assert web.get("/api/health").json()["status"] == "ok"
    assert web.get("/api/status").status_code == 401
    assert web.get("/openapi.json").status_code == 200


@pytest.mark.parametrize("path", ["/..%2Fsecret.txt", "/assets/..%2F..%2Fsecret.txt", "/%2E%2E/secret.txt"])
def test_a_path_cannot_climb_out_of_the_build(web, path):
    response = web.get(path)

    assert "outside" not in response.text


def test_nothing_is_served_without_a_build(db, tmp_path):
    app = create_app(
        scheduler=FakeScheduler(), state=RunState(), api_token=ApiToken(TOKEN, "stored"), web_dir=tmp_path / "missing"
    )
    client = TestClient(app)

    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200


# --- API token and system ----------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from audible_sync import system  # noqa: E402


def test_the_token_is_shown_masked(api):
    assert api.get("/api/settings/api-token", headers=AUTH).json() == {"last4": TOKEN[-4:], "source": "stored"}


def test_a_regenerated_token_works_at_once_and_the_old_one_does_not(api, user):
    _sign_in(api)

    response = api.post("/api/settings/api-token/regenerate")

    assert response.status_code == 200
    new = response.json()["token"]
    fresh = TestClient(api.app)  # no cookies: the token alone
    assert fresh.get("/api/status", headers=AUTH).status_code == 401
    assert fresh.get("/api/status", headers={"Authorization": f"Bearer {new}"}).status_code == 200
    assert fresh.get("/api/settings/api-token", headers={"Authorization": f"Bearer {new}"}).json()["last4"] == new[-4:]


def test_a_token_from_the_environment_is_not_regenerated(db):
    app = create_app(scheduler=FakeScheduler(), state=RunState(), api_token=ApiToken(TOKEN, "env"))
    client = TestClient(app)
    webauth.ensure_admin()
    webauth.set_password(PASSWORD)
    _sign_in(client)

    response = client.post("/api/settings/api-token/regenerate")

    assert response.status_code == 409
    assert "AUDIBLE_SYNC_API_TOKEN" in response.json()["detail"]
    assert client.get("/api/status", headers=AUTH).status_code == 200
    assert client.get("/api/settings/api-token", headers=AUTH).json()["source"] == "env"


def test_the_bearer_token_alone_cannot_regenerate_itself(api, user):
    """A leaked token must not be able to lock its owner's scripts out."""
    response = api.post("/api/settings/api-token/regenerate", headers=AUTH)

    assert response.status_code == 403
    assert api.get("/api/status", headers=AUTH).status_code == 200


def test_the_token_endpoints_need_auth(api):
    assert api.get("/api/settings/api-token").status_code == 401
    assert api.post("/api/settings/api-token/regenerate").status_code == 401


def test_system_reports_ffmpeg_the_disk_the_key_and_the_database(api, monkeypatch, tmp_path):
    monkeypatch.setattr(system, "ffmpeg", lambda: system.Ffmpeg("ffmpeg version 7.1", True))
    monkeypatch.setattr(
        system.shutil, "disk_usage", lambda path: SimpleNamespace(total=10**12, used=0, free=30 * 10**9)
    )
    api.put("/api/settings", headers=AUTH, json={"audiobook_folder": str(tmp_path / "books")})

    response = api.get("/api/system", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert (body["version"], body["ffmpeg_version"], body["libopus"]) == ("test", "ffmpeg version 7.1", True)
    assert body["audiobook_folder"] == {
        "path": str(tmp_path / "books"),
        "free_bytes": 30 * 10**9,
        "total_bytes": 10**12,
        "low_space": True,
    }
    # The test environment sets AUDIBLE_SYNC_SECRET_KEY (tests/conftest.py)
    assert (body["key_source"], body["key_file"]) == ("env", None)
    assert body["database_bytes"] > 0


def test_system_survives_a_folder_it_cannot_enter(api, monkeypatch, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    api.put("/api/settings", headers=AUTH, json={"audiobook_folder": str(locked / "inner" / "books")})
    monkeypatch.setattr(system, "ffmpeg", lambda: system.Ffmpeg(None, False))
    locked.chmod(0)
    try:
        response = api.get("/api/system", headers=AUTH)
    finally:
        locked.chmod(0o755)

    assert response.status_code == 200
    assert response.json()["audiobook_folder"]["free_bytes"] is None


def test_system_with_no_ffmpeg(api, monkeypatch):
    monkeypatch.setattr(system, "ffmpeg", lambda: system.Ffmpeg(None, False))

    body = api.get("/api/system", headers=AUTH).json()

    assert (body["ffmpeg_version"], body["libopus"]) == (None, False)


# --- the queue ------------------------------------------------------------------------


def test_the_queue_lists_the_next_runs_books_in_order(api):
    first = _account()
    second = _account("Other", "us")
    _shelve(first, *(make_book(f"A{n}", date_added=f"2024-01-0{n}T00:00:00Z") for n in (3, 1, 2)))
    _shelve(second, make_book("B1", "Second account's"))
    api.put("/api/settings", headers=AUTH, json={"max_download": 2})

    response = api.get("/api/queue", params={"limit": 2}, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert [(i["position"], i["account_id"], i["book"]["asin"]) for i in body["items"]] == [
        (1, first, "A1"),
        (2, first, "A2"),
    ]
    assert (body["total"], body["waiting"], body["max_download"]) == (3, 4, 2)
    assert body["items"][0]["book"]["title"]


def test_an_empty_queue(api):
    assert api.get("/api/queue", headers=AUTH).json() == {
        "items": [],
        "total": 0,
        "waiting": 0,
        "max_download": None,
    }


@pytest.mark.parametrize("limit", [0, 201])
def test_the_queue_limit_is_bounded(api, limit):
    assert api.get("/api/queue", params={"limit": limit}, headers=AUTH).status_code == 422


def test_the_queue_needs_auth(api):
    assert api.get("/api/queue").status_code == 401


# --- a run's books ----------------------------------------------------------------------


def test_a_runs_books_come_back_in_their_three_outcomes(api):
    account_id = _account()
    one, two, three, four = _shelve(account_id, *(make_book(f"B00{n}", f"Book {n}") for n in range(1, 5)))
    run_id = database.start_sync_run(account_id)
    database.record_run_book(run_id, one, "downloaded", bytes=2048, seconds=30.0)
    database.record_run_book(run_id, two, "failed", error="RuntimeError: boom")
    database.record_run_book(run_id, three, "refused", error="Denied")
    database.record_run_book(run_id, four, "downloaded", bytes=10, seconds=1.0)

    response = api.get(f"/api/sync/runs/{run_id}/books", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert (body["run_id"], body["recorded"]) == (run_id, True)
    assert [b["asin"] for b in body["downloaded"]] == ["B001", "B004"]
    assert body["downloaded"][0] | {"finished_at": None} == {
        "book_id": one.id,
        "asin": "B001",
        "title": "Book 1",
        "cover_url": one.cover_url,
        "error": None,
        "bytes": 2048,
        "seconds": 30.0,
        "finished_at": None,
    }
    assert [(b["asin"], b["error"]) for b in body["failed"]] == [("B002", "RuntimeError: boom")]
    assert [(b["asin"], b["error"]) for b in body["refused"]] == [("B003", "Denied")]


def test_a_run_from_before_the_records_says_so(api):
    run_id = database.start_sync_run(_account())
    conn = sqlite3.connect(database.DB_FILE)
    conn.execute("UPDATE sync_runs SET books_recorded = 0 WHERE id = ?", (run_id,))
    conn.commit()
    conn.close()

    body = api.get(f"/api/sync/runs/{run_id}/books", headers=AUTH).json()

    assert body == {"run_id": run_id, "recorded": False, "downloaded": [], "failed": [], "refused": []}
    assert api.get(f"/api/sync/runs/{run_id}", headers=AUTH).json()["books_recorded"] is False


def test_a_runs_books_404_for_an_unknown_run(api):
    assert api.get("/api/sync/runs/999/books", headers=AUTH).status_code == 404


# --- review fixes: CORS with *, the catch-all's methods, index.html caching ----------


def _cors_app(origins):
    app = create_app(
        scheduler=FakeScheduler(), state=RunState(), api_token=ApiToken(TOKEN, "stored"), cors_origins=origins
    )
    return TestClient(app)


def test_cors_with_a_wildcard_never_lets_the_session_cookie_through(db, caplog):
    """Starlette echoes any origin for * with credentials: any page on the host could act as the user."""
    client = _cors_app(["*"])

    response = client.get(
        "/api/health", headers={"Origin": "http://evil:9999", "Cookie": "audible_sync_session=stolen"}
    )

    assert response.headers.get("access-control-allow-origin") == "*"
    assert "access-control-allow-credentials" not in response.headers
    assert "AUDIBLE_SYNC_CORS_ORIGINS is *" in caplog.text


def test_cors_with_a_listed_origin_allows_the_session_cookie(db):
    response = _cors_app(["http://ui:5173"]).get("/api/health", headers={"Origin": "http://ui:5173"})

    assert response.headers["access-control-allow-origin"] == "http://ui:5173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_index_html_asked_for_by_name_is_revalidated(web):
    response = web.get("/index.html")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


@pytest.fixture(params=["with a build", "without a build"])
def either(request, db, tmp_path):
    if request.param == "with a build":
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html>")
        app = create_app(scheduler=FakeScheduler(), state=RunState(), api_token=ApiToken(TOKEN, "stored"), web_dir=dist)
    else:
        app = create_app(scheduler=FakeScheduler(), state=RunState(), api_token=ApiToken(TOKEN, "stored"))
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path", "status", "allow"),
    [
        ("POST", "/api/books/1/nope", 404, None),
        ("DELETE", "/api/nope", 404, None),
        ("POST", "/api/health", 405, "GET"),
        ("GET", "/api/sync", 405, "POST"),
        ("PUT", "/api/books/1", 405, "GET, PATCH"),
    ],
)
def test_unknown_paths_and_wrong_methods_answer_the_same_with_or_without_a_build(either, method, path, status, allow):
    response = either.request(method, path, headers=AUTH)

    assert response.status_code == status
    assert response.json()["detail"]
    if allow:
        # Starlette's own 405 names only the first matching route's methods, so check
        # the one that matters is there
        assert allow.split(", ")[0] in response.headers["allow"]


def test_a_post_to_a_page_of_the_app_is_a_404(web):
    response = web.post("/library")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


# --- served under a base path behind a reverse proxy ----------------------------------

from audible_sync.api import web as web_module  # noqa: E402
from audible_sync.api.web import normalise_base_path  # noqa: E402

BASE = "/audible-sync"


@pytest.fixture
def prefixed(db, tmp_path):
    """The app under /audible-sync, with a build whose index.html carries Vite's <base href="/">."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    # Spelled as Vite writes it out of web/index.html
    (dist / "index.html").write_text('<!doctype html><head><base href="/" /></head><title>Audible Sync</title>')
    (dist / "assets" / "app-1a2b.js").write_text("console.log('app')")
    (dist / "manifest.webmanifest").write_text('{"start_url": "./"}')
    (dist / "sw.js").write_text("const BUILD = {};")
    app = create_app(
        scheduler=FakeScheduler(),
        state=RunState(),
        api_token=ApiToken(TOKEN, "stored"),
        web_dir=dist,
        base_path=BASE,
    )
    return TestClient(app)


@pytest.mark.parametrize("prefix", [BASE, ""], ids=["passed through", "stripped"])
def test_the_api_answers_whether_the_proxy_strips_the_prefix_or_not(prefixed, prefix):
    assert prefixed.get(f"{prefix}/api/health").json()["status"] == "ok"
    assert prefixed.get(f"{prefix}/api/status").status_code == 401
    assert prefixed.get(f"{prefix}/api/status", headers=AUTH).status_code == 200


@pytest.mark.parametrize("prefix", [BASE, ""], ids=["passed through", "stripped"])
@pytest.mark.parametrize("page", ["/", "/library?status=failed", "/index.html"])
def test_the_page_tells_the_web_app_its_base(prefixed, prefix, page):
    response = prefixed.get(f"{prefix}{page}")

    assert response.status_code == 200
    assert '<base href="/audible-sync/">' in response.text
    assert '<base href="/"' not in response.text
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("prefix", [BASE, ""], ids=["passed through", "stripped"])
@pytest.mark.parametrize("path", ["/manifest.webmanifest", "/sw.js"])
def test_the_manifest_and_service_worker_are_served_under_the_base(prefixed, prefix, path):
    response = prefixed.get(f"{prefix}{path}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert "<base" not in response.text


@pytest.mark.parametrize("prefix", [BASE, ""], ids=["passed through", "stripped"])
def test_assets_and_unknown_api_paths_behave_as_at_the_root(prefixed, prefix):
    assert prefixed.get(f"{prefix}/assets/app-1a2b.js").text == "console.log('app')"
    assert prefixed.get(f"{prefix}/assets/gone.js").status_code == 404
    assert prefixed.get(f"{prefix}/api/nope", headers=AUTH).json() == {"detail": "Not Found"}
    response = prefixed.post(f"{prefix}/api/health")
    assert response.status_code == 405
    assert "GET" in response.headers["allow"]


def test_the_prefix_without_its_slash_is_redirected_to_it(prefixed):
    response = prefixed.get(f"{BASE}?next=%2Flibrary", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == f"{BASE}/?next=%2Flibrary"


def test_the_docs_find_the_schema_under_the_prefix(prefixed):
    assert prefixed.get(f"{BASE}/openapi.json").status_code == 200
    assert f"{BASE}/openapi.json" in prefixed.get(f"{BASE}/docs").text


def test_the_session_cookie_is_scoped_to_the_prefix(prefixed):
    webauth.ensure_admin()
    webauth.set_password(PASSWORD)

    signed_in = prefixed.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": PASSWORD})
    signed_out = prefixed.post(f"{BASE}/api/auth/logout")

    assert "Path=/audible-sync/" in signed_in.headers["set-cookie"]
    # Cleared at the same path, or the browser would keep the one it was given
    assert "Path=/audible-sync/" in signed_out.headers["set-cookie"]


def test_at_the_root_the_cookie_path_and_the_base_are_slash(web, tmp_path):
    (tmp_path / "dist" / "index.html").write_text('<head><base href="/"></head>')
    app = create_app(
        scheduler=FakeScheduler(), state=RunState(), api_token=ApiToken(TOKEN, "stored"), web_dir=tmp_path / "dist"
    )
    client = TestClient(app)
    webauth.ensure_admin()
    webauth.set_password(PASSWORD)

    assert '<base href="/">' in client.get("/library").text
    assert "Path=/;" in _sign_in(client).headers["set-cookie"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("/", ""),
        ("  ", ""),
        ("/audible-sync", "/audible-sync"),
        ("audible-sync/", "/audible-sync"),
        ("/apps/audible.sync_2~x/", "/apps/audible.sync_2~x"),
    ],
)
def test_the_base_path_is_normalised(value, expected):
    assert normalise_base_path(value) == expected


@pytest.mark.parametrize("value", ["/a b", '/a"><script>', "/a//b", "/..", "/a/./b", "/café", "/a?b", "/a#b"])
def test_a_base_path_that_is_not_plain_url_segments_is_refused(value):
    with pytest.raises(ValueError, match="base path"):
        normalise_base_path(value)


@pytest.mark.parametrize("value", ["/api", "/API/x", "/assets", "/docs", "/openapi.json", "/library", "/settings/sub"])
def test_a_base_path_starting_with_one_of_the_apps_own_paths_is_refused(value):
    # Behind a proxy that strips the prefix, /api/health under a base of /api would be
    # read as already carrying it and routed as /health
    with pytest.raises(ValueError, match="one of the app's own paths"):
        normalise_base_path(value)


@pytest.mark.parametrize("value", ["/apps/api", "/audible-sync/library", "/api-sync", "/my-docs"])
def test_the_apps_own_paths_further_down_a_base_path_are_fine(value):
    assert normalise_base_path(value) == value


def test_a_rebuild_beside_the_running_service_is_served_without_a_restart(prefixed, tmp_path):
    index = tmp_path / "dist" / "index.html"
    assert "app-1a2b.js" not in prefixed.get(f"{BASE}/").text

    index.write_text('<head><base href="/" /></head><script src="assets/app-9z8y.js"></script>')

    page = prefixed.get(f"{BASE}/library").text
    assert "app-9z8y.js" in page
    assert '<base href="/audible-sync/">' in page


def test_a_page_load_mid_rebuild_gets_the_last_good_page(prefixed, tmp_path):
    # Vite empties dist/ before writing the new build
    (tmp_path / "dist" / "index.html").unlink()

    response = prefixed.get(f"{BASE}/library")

    assert response.status_code == 200
    assert '<base href="/audible-sync/">' in response.text


def test_every_page_of_the_web_app_is_a_reserved_base_path():
    # The web app's top-level routes, read from its route table, so a page added there
    # cannot be accepted as a base path it would be confused with
    routes = (REPO_ROOT / "web" / "src" / "app" / "routes.tsx").read_text(encoding="utf-8")
    pages = {path.split("/")[0] for path in re.findall(r'path: "/?([^"*:]+)', routes)}

    assert pages >= {"login", "library", "profile"}
    assert pages <= web_module._RESERVED_SEGMENTS


def test_every_file_the_build_serves_at_its_top_is_a_reserved_base_path():
    # web/public is copied to the top of the build beside index.html and assets/
    public = {entry.name for entry in (REPO_ROOT / "web" / "public").iterdir()}

    assert public >= {"favicon.svg", "manifest.webmanifest", "icons"}
    assert public | {"sw.js"} <= web_module._RESERVED_SEGMENTS


def test_a_symlinked_index_html_still_gets_its_base(db, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.build.html").write_text('<head><base href="/" /></head>')
    (dist / "index.html").symlink_to("index.build.html")
    app = create_app(
        scheduler=FakeScheduler(),
        state=RunState(),
        api_token=ApiToken(TOKEN, "stored"),
        web_dir=dist,
        base_path=BASE,
    )

    response = TestClient(app).get(f"{BASE}/index.html")

    assert '<base href="/audible-sync/">' in response.text
    assert response.headers["cache-control"] == "no-cache"
