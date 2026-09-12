import logging

import pytest

from src import main as main_module
from src.downloader import DownloadStats
from src.main import configure_logging, main, run_pipeline
from src.model import SyncOutcome
from src.sync import SyncResult
from tests.conftest import make_settings


def test_configure_logging_honours_the_debug_flag(monkeypatch):
    """The debug config flag was read but never applied to the log level."""
    levels = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: levels.append(kw["level"]))

    configure_logging(debug=True)
    configure_logging(debug=False)

    assert levels == [logging.DEBUG, logging.INFO]


# Module-level singletons: ruff forbids constructing these in argument defaults
_SYNCED = SyncResult(books_seen=5, books_added=3)
_STATS = DownloadStats(attempted=2, succeeded=2, failed=0, unavailable=0)


def _patch_pipeline(monkeypatch, calls, *, synced=_SYNCED, stats=_STATS):
    """
    Replace everything run_pipeline calls with recorders, and hand back a fake client.

    `synced` and `stats` may be exceptions, which the recorder raises instead of
    returning, so a test can drive either failure arm.
    """

    def fake_audible(auth_file):
        calls["auth_file"] = auth_file
        return "client"

    def fake_sync(client):
        calls["synced"] = client
        if isinstance(synced, Exception):
            raise synced
        return synced

    def fake_download(client, settings, progress=None):
        calls["downloaded"] = (client, settings)
        calls["progress"] = progress
        if isinstance(stats, Exception):
            raise stats
        return stats

    monkeypatch.setattr(main_module, "Audible", fake_audible)
    monkeypatch.setattr(main_module, "init_db", lambda: calls.__setitem__("init_db", True))
    monkeypatch.setattr(main_module, "sync_library", fake_sync)
    monkeypatch.setattr(main_module, "download_books", fake_download)
    monkeypatch.setattr(main_module, "start_sync_run", lambda: calls.setdefault("run_id", 7))
    monkeypatch.setattr(
        main_module, "finish_sync_run", lambda run_id, **kw: calls.__setitem__("finished", (run_id, kw))
    )


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


def test_run_pipeline_records_a_successful_run(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls)

    run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"))

    run_id, recorded = calls["finished"]
    assert run_id == 7
    assert recorded == {
        "outcome": SyncOutcome.SUCCESS,
        "books_seen": 5,
        "books_added": 3,
        "books_downloaded": 2,
        "books_failed": 0,
    }


def test_run_pipeline_records_partial_when_a_book_failed(tmp_path, monkeypatch):
    """A failed book is the library state machine's business; the sync still completed."""
    calls = {}
    _patch_pipeline(monkeypatch, calls, stats=DownloadStats(3, 2, 1, 0))

    run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"))

    _, recorded = calls["finished"]
    assert recorded["outcome"] == SyncOutcome.PARTIAL
    assert recorded["books_failed"] == 1


def test_run_pipeline_records_failed_when_the_sync_raises(tmp_path, monkeypatch):
    """Nothing was read, so this run must not become the next run's cursor."""
    calls = {}
    _patch_pipeline(monkeypatch, calls, synced=RuntimeError("no library"))

    with pytest.raises(RuntimeError):
        run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"))

    _, recorded = calls["finished"]
    assert recorded["outcome"] == SyncOutcome.FAILED
    assert recorded["error"] == "RuntimeError: no library"
    assert recorded["books_seen"] == 0


def test_run_pipeline_records_partial_when_the_downloads_raise(tmp_path, monkeypatch):
    """The library really was read, so the cursor must still advance - with the counts."""
    calls = {}
    _patch_pipeline(monkeypatch, calls, stats=RuntimeError("disk full"))

    with pytest.raises(RuntimeError):
        run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"))

    _, recorded = calls["finished"]
    assert recorded["outcome"] == SyncOutcome.PARTIAL
    assert recorded["error"] == "RuntimeError: disk full"
    assert (recorded["books_seen"], recorded["books_added"]) == (5, 3)


def test_run_pipeline_reports_no_progress_by_default(tmp_path, monkeypatch):
    """A headless host gets no bar unless it asks for one."""
    calls = {}
    _patch_pipeline(monkeypatch, calls)

    run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"))

    assert calls["progress"] is None


def test_main_reads_settings_before_configuring_logging(monkeypatch):
    """The database holds the settings, so it comes first; the config file is seeded into
    it once; the settings are validated before logging or any folder exists."""
    order = []
    settings = make_settings(debug=True)

    monkeypatch.setattr(main_module, "init_db", lambda: order.append("init_db"))
    monkeypatch.setattr(main_module, "seed_settings_from_ini", lambda: order.append("seed"))
    monkeypatch.setattr(main_module.Settings, "from_db", classmethod(lambda cls: order.append("settings") or settings))
    monkeypatch.setattr(main_module, "configure_logging", lambda debug: order.append(("logging", debug)))
    monkeypatch.setattr(main_module, "run_pipeline", lambda s, progress=None: order.append(("pipeline", s, progress)))

    main()

    assert order[:4] == ["init_db", "seed", "settings", ("logging", True)]
    step, passed_settings, progress = order[4]
    assert (step, passed_settings) == ("pipeline", settings)
    # The bar is a terminal concern the CLI injects, like the logging config above
    assert isinstance(progress, main_module.TqdmProgress)
