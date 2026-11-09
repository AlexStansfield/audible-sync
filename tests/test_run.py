import logging
import sqlite3
import threading
from datetime import UTC, datetime

import pytest

from audible_sync.audible_api import accounts as accounts_module
from audible_sync.model import Account, SyncOutcome
from audible_sync.pipeline import run as run_module
from audible_sync.pipeline.downloader import DownloadStats
from audible_sync.pipeline.run import run_pipeline
from audible_sync.pipeline.runstate import RunStage, RunState
from audible_sync.pipeline.sync import SyncResult
from tests.conftest import make_account, make_settings

# Module-level singletons: ruff forbids constructing these in argument defaults
_SYNCED = SyncResult(books_seen=5, books_added=3, full_refresh=False)
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

    def fake_sync(client, account, *, auto_monitor_new, full=False):
        calls["synced"] = client
        calls.setdefault("full", []).append(full)
        calls.setdefault("synced_accounts", []).append(account.id)
        calls["auto_monitor_new"] = auto_monitor_new
        if isinstance(synced, Exception):
            raise synced
        return synced

    def fake_download(client, settings, progress=None, *, account_id=None, state=None, cancel=None, run_id=None):
        calls["downloaded"] = (client, settings)
        calls["download_account"] = account_id
        calls["download_run_id"] = run_id
        calls["progress"] = progress
        calls["state"] = state
        calls["cancel"] = cancel
        # What the run looks like to a poll while the downloads are going
        calls["snapshot"] = state.snapshot() if state is not None else None
        if isinstance(stats, Exception):
            raise stats
        return stats

    monkeypatch.setattr(run_module, "get_accounts", lambda: accounts)
    # The loop reads each account again at its turn; `calls["removed"]` plays one deleted meanwhile
    monkeypatch.setattr(
        run_module,
        "get_account",
        lambda account_id: (
            None if account_id in calls.get("removed", ()) else next((a for a in accounts if a.id == account_id), None)
        ),
    )
    monkeypatch.setattr(run_module, "authenticator_for", lambda account: f"auth-{account.id}")
    monkeypatch.setattr(run_module, "Audible", FakeAudible)
    monkeypatch.setattr(
        run_module, "persist_auth_if_changed", lambda account, auth: calls.setdefault("persisted", []).append(auth)
    )
    monkeypatch.setattr(run_module, "mark_account_synced", lambda account_id: calls.__setitem__("marked", account_id))
    monkeypatch.setattr(run_module, "init_db", lambda: calls.__setitem__("init_db", True))
    monkeypatch.setattr(run_module, "sync_library", fake_sync)
    monkeypatch.setattr(run_module, "download_books", fake_download)
    monkeypatch.setattr(
        run_module,
        "start_sync_run",
        lambda account_id, *, full_refresh=False: (
            calls.setdefault("started_full", []).append(full_refresh) or calls.setdefault("run_id", 7)
        ),
    )
    # No full refresh is due unless a test says when the last one was
    monkeypatch.setattr(
        run_module,
        "latest_full_refresh_start",
        lambda account_id: calls.get("last_full", datetime.now(UTC).isoformat()),
    )
    monkeypatch.setattr(run_module, "get_sync_run", lambda run_id: f"run-{run_id}")
    monkeypatch.setattr(
        run_module,
        "notify_run_finished",
        lambda url, run, account: calls.setdefault("notified", []).append((url, run, account.id)),
    )
    monkeypatch.setattr(
        run_module,
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
    # The downloads record their books against the same run
    assert calls["download_run_id"] == 7
    assert recorded == {
        "outcome": SyncOutcome.SUCCESS,
        "books_seen": 5,
        "books_added": 3,
        "books_downloaded": 2,
        "books_failed": 0,
        "error": None,
        "full_refresh": False,
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


def test_run_pipeline_skips_an_account_removed_after_the_run_began(tmp_path, monkeypatch, caplog):
    """The list is read once; an account deleted while an earlier one runs must not be synced."""
    caplog.set_level(logging.INFO, logger="audible_sync.pipeline.run")
    calls = {"removed": {2}}
    _patch_pipeline(monkeypatch, calls, accounts=[make_account(id=1), make_account(id=2, name="Gone")])

    run_pipeline(_settings(tmp_path))

    assert calls["synced_accounts"] == [1]
    assert "Skipping Gone: removed since the run began" in caplog.text


def test_run_pipeline_reads_each_account_again_at_its_turn(tmp_path, monkeypatch):
    """Disabled while an earlier account ran: the fresh row decides, not the stale list."""
    calls = {}
    _patch_pipeline(monkeypatch, calls, accounts=[make_account(id=1), make_account(id=2)])
    monkeypatch.setattr(
        run_module, "get_account", lambda account_id: make_account(id=account_id, enabled=account_id != 2)
    )

    run_pipeline(_settings(tmp_path))

    assert calls["synced_accounts"] == [1]


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


def test_run_account_sends_the_webhook_with_the_closed_run(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls)

    run_pipeline(
        make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b", webhook_url="https://hooks/x")
    )

    assert calls["notified"] == [("https://hooks/x", "run-7", 1)]


def test_run_account_sends_the_webhook_when_the_run_fails_too(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls, synced=RuntimeError("down"))

    with pytest.raises(RuntimeError):
        run_pipeline(
            make_settings(
                download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b", webhook_url="https://hooks/x"
            )
        )

    assert calls["notified"] == [("https://hooks/x", "run-7", 1)]


def test_run_account_sends_no_webhook_without_a_url(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls)

    run_pipeline(_settings(tmp_path))

    assert "notified" not in calls


def test_run_pipeline_records_aborted_with_the_reason_when_the_breaker_trips(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls, stats=DownloadStats(0, 0, 2, 3, aborted=True))

    run_pipeline(
        make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b", max_consecutive_failures=5)
    )

    _, recorded = calls["finished"]
    assert recorded["outcome"] == SyncOutcome.ABORTED
    assert recorded["error"] == "Gave up after 5 books in a row were refused or failed"
    assert recorded["books_failed"] == 2


def test_run_pipeline_records_rejected_credentials_as_an_aborted_run_saying_so(tmp_path, monkeypatch):
    """Not a failed book: the account needs a new login, and the history has to say so."""
    calls = {}
    _patch_pipeline(monkeypatch, calls, stats=DownloadStats(1, 0, 0, 0, credentials_error="Unauthorized: expired"))

    run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"))

    _, recorded = calls["finished"]
    assert recorded["outcome"] == SyncOutcome.ABORTED
    assert recorded["books_failed"] == 0
    assert "log in again" in recorded["error"]
    assert "Unauthorized: expired" in recorded["error"]


def test_a_failed_credentials_write_back_still_closes_the_run(tmp_path, monkeypatch, caplog):
    """A locked database on the token write-back must not leave the row `running` for good."""
    calls = {}
    _patch_pipeline(monkeypatch, calls)

    def locked(account, auth):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(run_module, "persist_auth_if_changed", locked)

    run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"))

    _, recorded = calls["finished"]
    assert recorded["outcome"] == SyncOutcome.SUCCESS
    assert "Could not store the refreshed credentials" in caplog.text


def test_the_device_is_marked_in_use_before_the_account_is_read_and_until_the_run_ends(monkeypatch, tmp_path):
    """
    Read first, a login between the read and the mark would deregister the device the run
    had just read.
    """
    calls = {}
    _patch_pipeline(monkeypatch, calls)
    account = make_account()
    in_use = {}

    def read(account_id):
        in_use["read"] = account_id in accounts_module._devices_in_use
        return account

    def run(account, settings, progress=None, *, state=None, cancel=None, full=False):
        in_use["run"] = account.id in accounts_module._devices_in_use

    monkeypatch.setattr(run_module, "get_account", read)
    monkeypatch.setattr(run_module, "run_account", run)

    run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"))

    assert in_use == {"read": True, "run": True}
    assert accounts_module._devices_in_use == set()


def test_a_failing_run_releases_the_device(monkeypatch, tmp_path):
    calls = {}
    _patch_pipeline(monkeypatch, calls, synced=RuntimeError("no"))

    with pytest.raises(RuntimeError):
        run_pipeline(make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b"))

    assert accounts_module._devices_in_use == set()


# --- full refresh ---------------------------------------------------------------


def _settings(tmp_path, **overrides):
    return make_settings(download_folder=tmp_path / "d", audiobook_folder=tmp_path / "b", **overrides)


def test_an_ordinary_run_is_not_a_full_refresh(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls)

    run_pipeline(_settings(tmp_path))

    assert calls["full"] == [False]
    assert calls["started_full"] == [False]
    assert calls["snapshot"]["full_refresh"] is False


def test_a_full_refresh_asked_for_reaches_every_account(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls, accounts=[make_account(1), make_account(2, "Two (US)", "us")])

    run_pipeline(_settings(tmp_path), full=True)

    assert calls["full"] == [True, True]
    assert calls["started_full"] == [True, True]
    assert calls["snapshot"]["full_refresh"] is True


def test_the_daily_full_refresh_is_due_with_none_yet(tmp_path, monkeypatch):
    calls = {"last_full": None}
    _patch_pipeline(monkeypatch, calls)

    run_pipeline(_settings(tmp_path))

    assert calls["full"] == [True]
    assert calls["started_full"] == [True]


@pytest.mark.parametrize(
    ("last_full", "now", "due"),
    [
        ("2026-11-09T00:05:00+00:00", "2026-11-09T23:55:00+00:00", False),
        # Under 24 hours, but yesterday: the drift of a daily schedule must not skip a day
        ("2026-11-08T06:10:00+00:00", "2026-11-09T06:05:00+00:00", True),
        ("2026-11-08T23:55:00+00:00", "2026-11-09T00:05:00+00:00", True),
        # A timestamp with another offset is compared as UTC
        ("2026-11-09T01:00:00+02:00", "2026-11-09T12:00:00+00:00", True),
    ],
)
def test_the_daily_full_refresh_is_due_once_per_utc_day(monkeypatch, last_full, now, due):
    monkeypatch.setattr(run_module, "latest_full_refresh_start", lambda account_id: last_full)

    assert run_module._full_refresh_due(1, make_settings(), datetime.fromisoformat(now)) is due


def test_the_daily_full_refresh_is_never_due_when_switched_off(tmp_path, monkeypatch):
    calls = {"last_full": None}
    _patch_pipeline(monkeypatch, calls)

    run_pipeline(_settings(tmp_path, daily_full_refresh=False))

    assert calls["full"] == [False]


def test_the_daily_full_refresh_is_worked_out_per_account(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls, accounts=[make_account(1), make_account(2, "Two (US)", "us")])
    # Only the second account has never had one
    monkeypatch.setattr(
        run_module,
        "latest_full_refresh_start",
        lambda account_id: datetime.now(UTC).isoformat() if account_id == 1 else None,
    )

    run_pipeline(_settings(tmp_path))

    assert calls["full"] == [False, True]


def test_the_run_records_whether_the_sync_was_a_full_refresh(tmp_path, monkeypatch):
    """An account's first sync is one without being asked, and the run says so."""
    calls = {}
    _patch_pipeline(monkeypatch, calls, synced=SyncResult(books_seen=1, books_added=0, full_refresh=True))

    run_pipeline(_settings(tmp_path))

    _, recorded = calls["finished"]
    assert recorded["full_refresh"] is True


def test_a_run_failing_before_the_read_leaves_what_it_set_out_to_do(tmp_path, monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls, synced=RuntimeError("no library"))

    with pytest.raises(RuntimeError):
        run_pipeline(_settings(tmp_path), full=True)

    _, recorded = calls["finished"]
    assert recorded["outcome"] is SyncOutcome.FAILED
    assert recorded["full_refresh"] is None
