import logging

import pytest
from fastapi.testclient import TestClient

import src.database as database
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
