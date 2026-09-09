import logging

from src import main as main_module
from src.main import configure_logging, main, run_pipeline
from tests.conftest import make_settings


def test_configure_logging_honours_the_debug_flag(monkeypatch):
    """The debug config flag was read but never applied to the log level."""
    levels = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: levels.append(kw["level"]))

    configure_logging(debug=True)
    configure_logging(debug=False)

    assert levels == [logging.DEBUG, logging.INFO]


def _patch_pipeline(monkeypatch, calls):
    """Replace everything run_pipeline calls with recorders, and hand back a fake client."""

    def fake_audible(auth_file):
        calls["auth_file"] = auth_file
        return "client"

    def fake_sync(client):
        calls["synced"] = client
        return 3

    def fake_download(client, settings):
        calls["downloaded"] = (client, settings)

    monkeypatch.setattr(main_module, "Audible", fake_audible)
    monkeypatch.setattr(main_module, "init_db", lambda: calls.__setitem__("init_db", True))
    monkeypatch.setattr(main_module, "sync_library", fake_sync)
    monkeypatch.setattr(main_module, "download_books", fake_download)


def test_run_pipeline_creates_the_folders_and_wires_the_client(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls)
    settings = make_settings(
        download_folder=tmp_path / "downloads",
        audiobook_folder=tmp_path / "books",
        auth_file=tmp_path / "audible.json",
    )

    run_pipeline(settings)

    assert settings.download_folder.is_dir()
    assert settings.audiobook_folder.is_dir()
    assert calls["auth_file"] == str(tmp_path / "audible.json")
    assert calls["init_db"] is True
    assert calls["synced"] == "client"
    assert calls["downloaded"] == ("client", settings)


def test_run_pipeline_does_not_configure_logging(tmp_path, monkeypatch):
    """A host process runs the pipeline; only the CLI may touch the root logger."""
    calls = {}
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: calls.__setitem__("logging", True))

    run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"))

    assert "logging" not in calls


def test_main_reads_settings_before_configuring_logging(monkeypatch):
    """Settings are validated first, so a bad config fails before any folder is made."""
    order = []
    settings = make_settings(debug=True)

    monkeypatch.setattr(main_module.Settings, "from_ini", classmethod(lambda cls: order.append("settings") or settings))
    monkeypatch.setattr(main_module, "configure_logging", lambda debug: order.append(("logging", debug)))
    monkeypatch.setattr(main_module, "run_pipeline", lambda s: order.append(("pipeline", s)))

    main()

    assert order == ["settings", ("logging", True), ("pipeline", settings)]
