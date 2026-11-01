import logging
import threading

import pytest

from src import main as main_module
from src.downloader import DownloadStats
from src.main import configure_logging, main, run_pipeline
from src.model import Account, SyncOutcome
from src.runstate import RunStage, RunState
from src.sync import SyncResult
from tests.conftest import make_account, make_settings


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


class FakeAudible:
    def __init__(self, auth):
        self.auth = auth


def _patch_pipeline(monkeypatch, calls, *, synced=_SYNCED, stats=_STATS, accounts=None):
    """
    Replace everything run_pipeline calls with recorders, and hand back a fake client.

    `synced` and `stats` may be exceptions, which the recorder raises instead of
    returning, so a test can drive either failure arm. `accounts` defaults to one
    account with credentials.
    """
    accounts = [make_account()] if accounts is None else accounts

    def fake_sync(client, account, *, auto_monitor_new):
        calls["synced"] = client
        calls.setdefault("synced_accounts", []).append(account.id)
        calls["auto_monitor_new"] = auto_monitor_new
        if isinstance(synced, Exception):
            raise synced
        return synced

    def fake_download(client, settings, progress=None, *, account_id=None, state=None, cancel=None):
        calls["downloaded"] = (client, settings)
        calls["download_account"] = account_id
        calls["progress"] = progress
        calls["state"] = state
        calls["cancel"] = cancel
        # What the run looks like to a poll while the downloads are going
        calls["snapshot"] = state.snapshot() if state is not None else None
        if isinstance(stats, Exception):
            raise stats
        return stats

    monkeypatch.setattr(main_module, "get_accounts", lambda: accounts)
    monkeypatch.setattr(main_module, "authenticator_for", lambda account: f"auth-{account.id}")
    monkeypatch.setattr(main_module, "Audible", FakeAudible)
    monkeypatch.setattr(
        main_module, "persist_auth_if_changed", lambda account, auth: calls.setdefault("persisted", []).append(auth)
    )
    monkeypatch.setattr(main_module, "mark_account_synced", lambda account_id: calls.__setitem__("marked", account_id))
    monkeypatch.setattr(main_module, "init_db", lambda: calls.__setitem__("init_db", True))
    monkeypatch.setattr(main_module, "sync_library", fake_sync)
    monkeypatch.setattr(main_module, "download_books", fake_download)
    monkeypatch.setattr(main_module, "start_sync_run", lambda account_id: calls.setdefault("run_id", 7))
    monkeypatch.setattr(
        main_module,
        "finish_sync_run",
        lambda run_id, **kw: (
            calls.setdefault("finished_all", []).append((run_id, kw)) or calls.__setitem__("finished", (run_id, kw))
        ),
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
    assert calls["init_db"] is True
    # The client is built from the account's credentials and used for both halves
    client = calls["synced"]
    assert client.auth == "auth-1"
    assert calls["downloaded"] == (client, settings)
    assert calls["download_account"] == 1
    assert calls["auto_monitor_new"] is True
    assert calls["marked"] == 1
    # Whatever the run refreshed is written back
    assert calls["persisted"] == ["auth-1"]


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
    it once; the settings are validated before logging or any folder exists; then the
    auth file of an installation from before accounts is brought across."""
    order = []
    settings = make_settings(debug=True)

    monkeypatch.setattr(main_module, "init_db", lambda: order.append("init_db"))
    monkeypatch.setattr(main_module, "seed_settings_from_ini", lambda: order.append("seed"))
    monkeypatch.setattr(main_module.Settings, "from_db", classmethod(lambda cls: order.append("settings") or settings))
    monkeypatch.setattr(main_module, "configure_logging", lambda debug: order.append(("logging", debug)))
    monkeypatch.setattr(main_module, "ensure_account_from_auth_file", lambda path: order.append(("import", path)))
    monkeypatch.setattr(main_module, "run_pipeline", lambda s, progress=None: order.append(("pipeline", s, progress)))

    main([])

    assert order[:5] == ["init_db", "seed", "settings", ("logging", True), ("import", settings.auth_file)]
    step, passed_settings, progress = order[5]
    assert (step, passed_settings) == ("pipeline", settings)
    # The bar is a terminal concern the CLI injects, like the logging config above
    assert isinstance(progress, main_module.TqdmProgress)


def test_run_pipeline_records_cancelled_when_the_downloads_were_stopped(tmp_path, monkeypatch):
    """A cancelled run still read the library through, so it is recorded with its counts
    and counts as a cursor - the outcome just says why it stopped early."""
    calls = {}
    _patch_pipeline(monkeypatch, calls, stats=DownloadStats(5, 2, 0, 0, cancelled=True))

    run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"))

    _, recorded = calls["finished"]
    assert recorded["outcome"] == SyncOutcome.CANCELLED
    assert recorded["books_downloaded"] == 2


def test_run_pipeline_reports_its_stages_to_the_run_state(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls)
    state = RunState()

    run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"), state=state)

    # Seen from inside the download half: the run row's id and the downloading stage
    assert calls["snapshot"]["run_id"] == 7
    assert calls["snapshot"]["stage"] == RunStage.DOWNLOADING
    # And nothing once the run is over
    assert state.snapshot() is None


def test_run_pipeline_clears_the_run_state_when_the_run_raises(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls, synced=RuntimeError("no library"))
    state = RunState()

    with pytest.raises(RuntimeError):
        run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"), state=state)

    assert state.snapshot() is None


def test_run_pipeline_hands_the_cancel_event_to_the_downloads(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls)
    cancel = threading.Event()

    run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"), cancel=cancel)

    assert calls["cancel"] is cancel


# --- accounts ----------------------------------------------------------------------


def _settings(tmp_path):
    return make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b")


def test_run_pipeline_does_nothing_without_an_account(tmp_path, monkeypatch, caplog):
    calls = {}
    _patch_pipeline(monkeypatch, calls, accounts=[])

    run_pipeline(_settings(tmp_path))

    assert "synced" not in calls
    assert "No Audible accounts" in caplog.text


def test_run_pipeline_skips_a_disabled_account_and_one_without_credentials(tmp_path, monkeypatch, caplog):
    calls = {}
    accounts = [
        make_account(id=1, enabled=False),
        make_account(id=2, auth={"locale_code": "us"}),
        Account(id=3, name="Pending", country_code="de", auth=None),
    ]
    _patch_pipeline(monkeypatch, calls, accounts=accounts)

    run_pipeline(_settings(tmp_path))

    assert calls["synced_accounts"] == [2]
    assert "Skipping Pending: it has no credentials" in caplog.text


def test_run_pipeline_gives_every_account_its_turn_then_raises_the_first_error(tmp_path, monkeypatch):
    """One account failing must not cost the others their run, but a one-shot run still
    has to exit non-zero."""
    calls = {}
    accounts = [make_account(id=1), make_account(id=2)]
    _patch_pipeline(monkeypatch, calls, accounts=accounts, synced=RuntimeError("marketplace down"))

    with pytest.raises(RuntimeError, match="marketplace down"):
        run_pipeline(_settings(tmp_path))

    assert calls["synced_accounts"] == [1, 2]
    assert [kw["outcome"] for _, kw in calls["finished_all"]] == [SyncOutcome.FAILED, SyncOutcome.FAILED]


def test_run_pipeline_stops_between_accounts_when_cancelled(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls, accounts=[make_account(id=1), make_account(id=2)])
    cancel = threading.Event()
    cancel.set()

    run_pipeline(_settings(tmp_path), cancel=cancel)

    assert "synced" not in calls


def test_run_account_reports_the_account_to_the_run_state(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls, accounts=[make_account(id=4, name="Alex (US)", country_code="us")])

    run_pipeline(_settings(tmp_path), state=RunState())

    assert calls["snapshot"]["account"] == {"id": 4, "name": "Alex (US)", "country_code": "us"}


def test_run_account_writes_refreshed_credentials_back_even_when_the_run_fails(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls, stats=RuntimeError("disk full"))

    with pytest.raises(RuntimeError):
        run_pipeline(_settings(tmp_path))

    assert calls["persisted"] == ["auth-1"]


# --- the command line ----------------------------------------------------------------


def _patch_prepare(monkeypatch, order):
    settings = make_settings()
    monkeypatch.setattr(main_module, "init_db", lambda: order.append("init_db"))
    monkeypatch.setattr(main_module, "seed_settings_from_ini", lambda: order.append("seed"))
    monkeypatch.setattr(main_module.Settings, "from_db", classmethod(lambda cls: settings))
    monkeypatch.setattr(main_module, "configure_logging", lambda debug: order.append("logging"))
    monkeypatch.setattr(main_module, "ensure_account_from_auth_file", lambda path: order.append("import"))
    return settings


def test_run_is_the_default_command(monkeypatch):
    order = []
    settings = _patch_prepare(monkeypatch, order)
    monkeypatch.setattr(main_module, "run_pipeline", lambda s, progress=None: order.append(("pipeline", s)))

    main(["run"])
    main([])

    assert order.count(("pipeline", settings)) == 2


def test_login_command_adds_an_account_from_the_browser_login(monkeypatch, capsys):
    order = []
    _patch_prepare(monkeypatch, order)
    monkeypatch.setattr(
        main_module, "login_interactively", lambda marketplace: order.append(("login", marketplace)) or "auth"
    )
    added = []

    def fake_add(auth, *, name, monitor_existing):
        added.append((auth, name, monitor_existing))
        return 3

    monkeypatch.setattr(main_module, "add_account_from_authenticator", fake_add)
    monkeypatch.setattr(main_module, "run_pipeline", lambda *a, **kw: pytest.fail("login must not sync"))

    main(["login", "--marketplace", "uk", "--name", "Main", "--no-download-existing"])

    # The database and settings come first, like every command, then the login
    assert order[:4] == ["init_db", "seed", "logging", "import"]
    assert order[4] == ("login", "uk")
    assert added == [("auth", "Main", False)]
    assert "Account 3 added" in capsys.readouterr().out


def test_login_command_defaults(monkeypatch):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "login_interactively", lambda marketplace: "auth")
    added = []
    monkeypatch.setattr(
        main_module,
        "add_account_from_authenticator",
        lambda auth, *, name, monitor_existing: added.append((name, monitor_existing)) or 1,
    )

    main(["login", "--marketplace", "us"])

    assert added == [(None, True)]


def test_login_command_refuses_an_unknown_marketplace(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        main(["login", "--marketplace", "xx"])

    assert "invalid choice" in capsys.readouterr().err
