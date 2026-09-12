import logging

import pytest
from fastapi.testclient import TestClient

import src.database as database
from src import api as api_module
from src.api import create_app, read_version
from src.audible_login import PendingLogins
from src.logbuffer import RingBufferHandler
from src.model import SyncOutcome
from src.runstate import RunStage, RunState
from src.settings import Settings

TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeScheduler:
    """Duck-types the four methods and the status the API touches."""

    def __init__(self, *, running=False):
        self.running = running
        self.triggered = 0
        self.cancelled = 0
        self.woken = 0
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def trigger(self):
        if self.running:
            return False
        self.triggered += 1
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
    logins = PendingLogins()
    log_buffer = RingBufferHandler(capacity=50)
    app = create_app(
        scheduler=scheduler, state=state, api_token=TOKEN, version="test", logins=logins, log_buffer=log_buffer
    )
    # No context manager: the lifespan (and so the scheduler) is exercised on its own
    client = TestClient(app)
    client.scheduler = scheduler
    client.state = state
    client.logins = logins
    client.log_buffer = log_buffer
    return client


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
    api.state.transfer_started("Book", 100)
    api.state.transfer_advanced(40)

    body = api.get("/api/status", headers=AUTH).json()

    assert body["current_run"]["run_id"] == run_id + 1
    assert body["current_run"]["stage"] == "downloading"
    assert body["current_run"]["transfer"] == {"desc": "Book", "bytes": 40, "total": 100}
    assert body["last_run"]["id"] == run_id
    assert body["last_run"]["outcome"] == "partial"
    assert body["last_run"]["books_failed"] == 1


def test_start_sync_triggers_the_scheduler(api):
    response = api.post("/api/sync", headers=AUTH)

    assert response.status_code == 202
    assert response.json() == {"status": "started"}
    assert api.scheduler.triggered == 1


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
    assert "auth_file" not in body
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
    app = create_app(scheduler=scheduler, state=RunState(), api_token=TOKEN)

    with TestClient(app):
        assert (scheduler.started, scheduler.stopped) == (1, 0)

    assert (scheduler.started, scheduler.stopped) == (1, 1)


def test_cors_allows_the_configured_origin(db):
    app = create_app(scheduler=FakeScheduler(), state=RunState(), api_token=TOKEN, cors_origins=["http://ui:5173"])
    client = TestClient(app)

    response = client.options(
        "/api/status",
        headers={"Origin": "http://ui:5173", "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://ui:5173"


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

    monkeypatch.setattr(api_module, "authenticator_for", lambda account: FakeAuthenticator())

    api.delete(f"/api/accounts/{account_id}", headers=AUTH, params={"deregister": "true"})

    assert calls == ["deregistered"]
    assert database.get_account(account_id) is None


def test_delete_account_still_removes_it_when_deregistering_fails(api, monkeypatch, caplog):
    """A login that no longer works cannot deregister itself; the user still wants it gone."""
    account_id = _account()

    class FakeAuthenticator:
        def deregister_device(self):
            raise RuntimeError("token expired")

    monkeypatch.setattr(api_module, "authenticator_for", lambda account: FakeAuthenticator())

    response = api.delete(f"/api/accounts/{account_id}", headers=AUTH, params={"deregister": "true"})

    assert response.status_code == 200
    assert database.get_account(account_id) is None
    assert "Could not deregister" in caplog.text


def test_delete_account_does_not_try_to_deregister_one_without_credentials(api, monkeypatch):
    account_id = database.add_account("Pending", "us", auth=None)
    monkeypatch.setattr(api_module, "authenticator_for", lambda account: pytest.fail("nothing to deregister"))

    assert api.delete(f"/api/accounts/{account_id}", headers=AUTH, params={"deregister": "true"}).status_code == 200


def test_import_account_from_an_auth_file(api, monkeypatch):
    def fake_import(path, *, name, monitor_existing):
        assert path == "/keys/audible.json"
        return _account(name or "Alex (UK)", monitor_existing=monitor_existing)

    monkeypatch.setattr(api_module, "import_auth_file", fake_import)

    response = api.post(
        "/api/accounts/import", headers=AUTH, json={"path": "/keys/audible.json", "monitor_existing": False}
    )

    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["monitor_existing"], body["needs_login"]) == ("Alex (UK)", False, False)


def test_import_account_reports_a_missing_file(api, tmp_path):
    response = api.post("/api/accounts/import", headers=AUTH, json={"path": str(tmp_path / "nope.json")})

    assert response.status_code == 400
    assert "auth file not found" in response.json()["detail"]


def test_import_account_reports_a_file_it_cannot_read(api, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json")

    response = api.post("/api/accounts/import", headers=AUTH, json={"path": str(path)})

    assert response.status_code == 400
    assert "could not read" in response.json()["detail"]


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


def test_login_step_one_returns_the_sign_in_url(api):
    response = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "uk"})

    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("https://www.amazon.co.uk/ap/signin?")
    assert body["expires_at"].endswith("+00:00")
    # The store now holds it for step two
    assert api.logins.pop(body["login_id"]) is not None


def test_login_step_one_rejects_an_unknown_marketplace(api):
    response = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "xx"})

    assert response.status_code == 422
    assert "unknown marketplace" in response.json()["detail"]


def _fake_complete(monkeypatch, *, raises=None):
    from audible import Authenticator

    from tests.test_accounts import AUTH_BLOB

    calls = []

    def fake(pending, response_url):
        calls.append((pending.country_code, response_url))
        if raises is not None:
            raise raises
        return Authenticator.from_dict(dict(AUTH_BLOB))

    monkeypatch.setattr(api_module, "complete_login", fake)
    return calls


def test_login_step_two_creates_the_account(api, monkeypatch):
    calls = _fake_complete(monkeypatch)
    login_id = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "uk"}).json()["login_id"]

    response = api.post(
        f"/api/accounts/login/{login_id}",
        headers=AUTH,
        json={
            "response_url": "https://www.amazon.co.uk/ap/maplanding?openid.oa2.authorization_code=X",
            "monitor_existing": False,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["country_code"], body["monitor_existing"], body["needs_login"]) == (
        "Alex (UK)",
        "uk",
        False,
        False,
    )
    assert calls == [("uk", "https://www.amazon.co.uk/ap/maplanding?openid.oa2.authorization_code=X")]
    assert database.get_account(body["id"]).auth["access_token"] == "Atna|access"
    # Single use
    assert api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": "x"}).status_code == 404


def test_login_step_two_takes_a_name(api, monkeypatch):
    _fake_complete(monkeypatch)
    login_id = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "uk"}).json()["login_id"]

    body = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": "u", "name": "Main"}).json()

    assert body["name"] == "Main"


def test_login_step_two_for_an_unknown_or_expired_login(api):
    response = api.post("/api/accounts/login/nope", headers=AUTH, json={"response_url": "u"})

    assert response.status_code == 404
    assert "start again" in response.json()["detail"]


def test_login_step_two_with_a_url_that_carries_no_code(api, monkeypatch):
    _fake_complete(monkeypatch, raises=ValueError("that URL carries no authorization code"))
    login_id = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "uk"}).json()["login_id"]

    response = api.post(
        f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": "https://www.amazon.co.uk/"}
    )

    assert response.status_code == 400
    assert "no authorization code" in response.json()["detail"]
    assert database.get_accounts() == []


def test_login_step_two_when_amazon_rejects_the_code(api, monkeypatch, caplog):
    _fake_complete(monkeypatch, raises=Exception({"error": "InvalidValue"}))
    login_id = api.post("/api/accounts/login", headers=AUTH, json={"country_code": "uk"}).json()["login_id"]

    response = api.post(f"/api/accounts/login/{login_id}", headers=AUTH, json={"response_url": "u"})

    assert response.status_code == 502
    assert "Amazon rejected the login" in response.json()["detail"]
    assert "InvalidValue" in caplog.text


# --- books ---------------------------------------------------------------------------

from src.model import BookStatus  # noqa: E402
from tests.conftest import make_book  # noqa: E402


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
    database.mark_book_downloaded(book.id, "m4b", file_path=str(audio), cover_path=str(cover))
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
    database.claim_book_for_download(book.id)
    database.mark_book_failed(book.id, "boom", max_attempts=1)

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


def test_refresh_re_reads_the_book(api, monkeypatch):
    (book,) = _shelve(_account(), make_book("B001", "Old"))
    monkeypatch.setattr(
        api_module.library,
        "refresh",
        lambda b: database.update_books(b.account_id, [make_book("B001", "New")]) or database.get_book(b.id),
    )

    body = api.post(f"/api/books/{book.id}/refresh", headers=AUTH).json()

    assert body["title"] == "New"


def test_refresh_reports_audible_trouble_as_a_502(api, monkeypatch):
    (book,) = _shelve(_account(), make_book("B001"))

    def down(b):
        raise RuntimeError("timeout")

    monkeypatch.setattr(api_module.library, "refresh", down)

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
    app = create_app(scheduler=FakeScheduler(), state=RunState(), api_token=TOKEN)

    assert TestClient(app).get("/api/logs", headers=AUTH).json() == {"items": []}


def test_stats(api, tmp_path, monkeypatch):
    account_id = _account()
    other = _account("Other", "us")
    (book,) = _shelve(account_id, make_book("B001"))
    _shelve(other, make_book("B002"), make_book("B003"), monitor_new=False)
    audio = tmp_path / "a.m4b"
    audio.write_bytes(b"x" * 1234)
    database.mark_book_downloaded(book.id, "m4b", file_path=str(audio))
    database.mark_book_downloaded(
        database.get_book_by_asin("B002", account_id=other).id, "m4b", file_path=str(tmp_path / "gone.m4b")
    )
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


def test_stats_on_an_empty_database(api):
    body = api.get("/api/stats", headers=AUTH).json()

    assert body["accounts"] == []
    assert body["books"]["total"] == 0
    assert body["last_run"] is None


def test_webhook_test_sends_to_the_configured_url(api, monkeypatch):
    sent = []
    monkeypatch.setattr(api_module, "send_webhook", lambda url, payload: sent.append((url, payload["event"])))
    api.put("/api/settings", headers=AUTH, json={"webhook_url": "https://hooks/configured"})

    response = api.post("/api/settings/webhook/test", headers=AUTH, json={})

    assert response.status_code == 200
    assert response.json() == {"status": "sent"}
    assert sent == [("https://hooks/configured", "test")]


def test_webhook_test_can_try_a_url_before_saving_it(api, monkeypatch):
    sent = []
    monkeypatch.setattr(api_module, "send_webhook", lambda url, payload: sent.append(url))

    api.post("/api/settings/webhook/test", headers=AUTH, json={"url": "https://hooks/try"})

    assert sent == ["https://hooks/try"]


def test_webhook_test_without_a_url(api):
    response = api.post("/api/settings/webhook/test", headers=AUTH, json={})

    assert response.status_code == 400
    assert response.json() == {"detail": "No webhook URL configured"}


def test_webhook_test_reports_a_failure(api, monkeypatch):
    def down(url, payload):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(api_module, "send_webhook", down)

    response = api.post("/api/settings/webhook/test", headers=AUTH, json={"url": "https://hooks/x"})

    assert response.status_code == 502
    assert "connection refused" in response.json()["detail"]
