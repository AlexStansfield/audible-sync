import logging

import pytest
from fastapi.testclient import TestClient

import src.database as database
from src import api as api_module
from src.api import create_app, read_version
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
    app = create_app(scheduler=scheduler, state=state, api_token=TOKEN, version="test")
    # No context manager: the lifespan (and so the scheduler) is exercised on its own
    client = TestClient(app)
    client.scheduler = scheduler
    client.state = state
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
