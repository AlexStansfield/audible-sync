import logging

import pytest
from fastapi.testclient import TestClient

import src.database as database
from src import crypto, service, webauth
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
    # Encrypted at rest, like the credentials: a copy of the database is not API access
    (stored,) = database.get_settings().values()
    assert stored.startswith("enc:") and first not in stored


def test_the_token_is_logged_in_full_only_on_the_start_that_generates_it(db, caplog):
    """`docker logs` attached to a bug report must not hand over the API on every start."""
    caplog.set_level(logging.INFO, logger="src.service")
    token = resolve_api_token(None)
    assert token in caplog.text
    assert "not logged again" in caplog.text
    caplog.clear()

    assert resolve_api_token(None) == token

    assert token not in caplog.text
    assert f"the stored one, ending ...{token[-4:]}" in caplog.text


def test_a_configured_token_is_never_logged(db, caplog):
    caplog.set_level(logging.INFO, logger="src.service")

    resolve_api_token("chosen-by-the-user")

    assert "chosen-by-the-user" not in caplog.text
    assert "from AUDIBLE_SYNC_API_TOKEN" in caplog.text


def test_a_token_stored_in_plain_text_by_an_older_version_still_works(db):
    database.save_settings({"api_token": "old-token"})

    assert resolve_api_token(None) == "old-token"


def test_a_token_under_another_key_is_replaced(db, caplog):
    first = resolve_api_token(None)
    crypto.configure(crypto.generate_key())

    second = resolve_api_token(None)

    assert second != first
    assert "cannot be read" in caplog.text
    assert resolve_api_token(None) == second


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


def test_the_first_start_creates_the_web_user_and_logs_its_password_once(db, caplog):
    caplog.set_level(logging.INFO, logger="src.service")

    service.ensure_web_user()
    (password,) = [r.args[1] for r in caplog.records if "Web app sign-in" in r.getMessage()]
    caplog.clear()
    service.ensure_web_user()

    assert webauth.verify("admin", password)
    assert password not in caplog.text
    assert "reset-password" not in caplog.text


def test_build_creates_the_web_user(db, monkeypatch):
    monkeypatch.setattr(service, "configure_logging", lambda debug: None)
    monkeypatch.setattr(service, "seed_settings_from_ini", lambda: False)

    service.build(ServiceConfig(api_token="tok"))

    assert webauth.current_user() == webauth.User("admin", generated_password=True)


def test_secure_cookie_comes_from_the_environment():
    assert ServiceConfig.from_env({}).secure_cookie is False
    assert ServiceConfig.from_env({"AUDIBLE_SYNC_SECURE_COOKIE": "true"}).secure_cookie is True


def test_a_generated_token_and_password_never_reach_the_logs_endpoint(db, monkeypatch, caplog):
    """The Logs screen can be copied into a bug report; the console is the only place they go."""
    monkeypatch.setattr(service, "configure_logging", lambda debug: None)
    monkeypatch.setattr(service, "seed_settings_from_ini", lambda: False)
    caplog.set_level(logging.INFO)

    app = service.build(ServiceConfig())
    client = TestClient(app)
    token = resolve_api_token(None)
    logged = " ".join(
        r["message"]
        for r in client.get("/api/logs?limit=1000", headers={"Authorization": f"Bearer {token}"}).json()["items"]
    )

    assert "Generated a new API token" in caplog.text
    assert "Web app sign-in" in caplog.text
    assert token not in logged
    assert "Web app sign-in" not in logged
    for handler in list(logging.getLogger().handlers):
        if isinstance(handler, RingBufferHandler):
            logging.getLogger().removeHandler(handler)
