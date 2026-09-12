import logging

import pytest
from fastapi.testclient import TestClient

import src.database as database
from src import service
from src.logbuffer import RingBufferHandler
from src.service import ServiceConfig, resolve_api_token
from src.settings import Settings


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()


def test_config_defaults():
    assert ServiceConfig.from_env({}) == ServiceConfig(
        host="0.0.0.0", port=8080, api_token=None, cors_origins=(), debug=False
    )


def test_config_reads_every_variable():
    config = ServiceConfig.from_env(
        {
            "AUDIBLE_SYNC_HOST": "127.0.0.1",
            "AUDIBLE_SYNC_PORT": "9000",
            "AUDIBLE_SYNC_API_TOKEN": "tok",
            "AUDIBLE_SYNC_CORS_ORIGINS": "http://a:1, http://b:2,",
            "AUDIBLE_SYNC_DEBUG": "true",
        }
    )

    assert config == ServiceConfig(
        host="127.0.0.1", port=9000, api_token="tok", cors_origins=("http://a:1", "http://b:2"), debug=True
    )


def test_config_treats_an_empty_token_as_unset():
    assert ServiceConfig.from_env({"AUDIBLE_SYNC_API_TOKEN": ""}).api_token is None


def test_configured_token_wins(db):
    assert resolve_api_token("from-env") == "from-env"
    assert database.get_settings() == {}


def test_a_generated_token_is_kept_for_the_next_start(db):
    """The token copied from the log must keep working across restarts."""
    first = resolve_api_token(None)

    assert len(first) >= 32
    assert resolve_api_token(None) == first
    assert database.get_settings() == {"api_token": first}


def test_the_stored_token_never_shows_as_a_setting(db):
    resolve_api_token(None)

    assert Settings.from_db() == Settings()


def test_build_wires_a_working_app(db, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "configure_logging", lambda debug: None)
    monkeypatch.setattr(service, "seed_settings_from_ini", lambda: False)

    app = service.build(ServiceConfig(api_token="tok"))
    client = TestClient(app)

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer tok"}).status_code == 200


def test_build_installs_the_log_buffer_on_the_root_logger(db, monkeypatch):
    monkeypatch.setattr(service, "configure_logging", lambda debug: None)
    monkeypatch.setattr(service, "seed_settings_from_ini", lambda: False)
    root = logging.getLogger()
    before = list(root.handlers)

    app = service.build(ServiceConfig(api_token="tok"))
    try:
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 1
        assert isinstance(added[0], RingBufferHandler)
        probe = logging.getLogger("tests.service")
        probe.setLevel(logging.INFO)
        probe.info("through the root")
        client = TestClient(app)
        body = client.get("/api/logs", headers={"Authorization": "Bearer tok"}).json()
        assert any(e["message"] == "through the root" for e in body["items"])
    finally:
        for handler in root.handlers:
            if handler not in before:
                root.removeHandler(handler)
