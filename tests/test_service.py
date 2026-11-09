import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.database as database
from src import crypto, service, webauth
from src.apitoken import resolve_api_token
from src.logbuffer import RingBufferHandler
from src.service import ServiceConfig


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


def test_the_duplicate_account_warning_reaches_the_logs_endpoint(db, monkeypatch):
    """It is the only advice about duplicates, and most read their logs in the web app."""
    monkeypatch.setattr(service, "configure_logging", lambda debug: None)
    monkeypatch.setattr(service, "seed_settings_from_ini", lambda: False)
    key = crypto.generate_key()
    crypto.configure(key)
    monkeypatch.setattr(service, "load_secret_key", lambda: key)
    blob = {"locale_code": "uk", "customer_info": {"user_id": "u1"}}
    database.add_account("First", "uk", auth=blob)
    database.add_account("Second", "uk", auth=blob)
    root = logging.getLogger()
    before = list(root.handlers)

    app = service.build(ServiceConfig(api_token="tok"))
    try:
        items = TestClient(app).get("/api/logs", headers={"Authorization": "Bearer tok"}).json()["items"]
        assert any("hold the same Audible login" in e["message"] for e in items)
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
    token = resolve_api_token(None).value
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


def test_the_web_app_build_defaults_to_web_dist_and_can_be_moved():
    assert ServiceConfig.from_env({}).web_dir == service.REPO_ROOT / "web" / "dist"
    assert ServiceConfig.from_env({"AUDIBLE_SYNC_WEB_DIR": "/srv/ui"}).web_dir == Path("/srv/ui")
    # A relative path is anchored to the repository, like every other path
    assert ServiceConfig.from_env({"AUDIBLE_SYNC_WEB_DIR": "ui"}).web_dir == service.REPO_ROOT / "ui"


def test_build_serves_the_web_app_it_is_pointed_at(db, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "configure_logging", lambda debug: None)
    monkeypatch.setattr(service, "seed_settings_from_ini", lambda: False)
    (tmp_path / "index.html").write_text("<p>app</p>")

    client = TestClient(service.build(ServiceConfig(api_token="tok", web_dir=tmp_path)))

    assert client.get("/library").text == "<p>app</p>"


@pytest.mark.parametrize(
    ("value", "expected"), [("", False), ("false", False), ("0", False), ("off", False), ("YES", True), ("on", True)]
)
def test_a_flag_reads_the_spellings_config_ini_does(value, expected):
    assert ServiceConfig.from_env({"AUDIBLE_SYNC_SECURE_COOKIE": value}).secure_cookie is expected


def test_a_flag_that_is_not_true_or_false_is_an_error_naming_it():
    with pytest.raises(ValueError, match="AUDIBLE_SYNC_DEBUG must be true or false"):
        ServiceConfig.from_env({"AUDIBLE_SYNC_DEBUG": "maybe"})


def test_the_base_path_comes_from_the_environment_normalised():
    assert ServiceConfig.from_env({}).base_path == ""
    assert ServiceConfig.from_env({"AUDIBLE_SYNC_BASE_PATH": "audible-sync/"}).base_path == "/audible-sync"


def test_a_base_path_that_is_not_a_path_is_an_error_naming_the_variable():
    with pytest.raises(ValueError, match="AUDIBLE_SYNC_BASE_PATH"):
        ServiceConfig.from_env({"AUDIBLE_SYNC_BASE_PATH": "/a b"})


def test_build_serves_under_the_base_path(db, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "configure_logging", lambda debug: None)
    monkeypatch.setattr(service, "seed_settings_from_ini", lambda: False)
    (tmp_path / "index.html").write_text('<base href="/">')

    client = TestClient(service.build(ServiceConfig(api_token="tok", web_dir=tmp_path, base_path="/sync")))

    assert client.get("/sync/library").text == '<base href="/sync/">'
    assert client.get("/sync/api/health").status_code == 200
