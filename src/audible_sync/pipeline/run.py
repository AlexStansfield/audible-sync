"""
One run: every account's library synced, then what it has waiting downloaded.

The service's scheduler and the `audible-sync` command both start runs here; neither
configures anything on import, so a host process is not surprised.
"""

import logging
import threading

from audible_sync.audible_api.accounts import (
    authenticator_for,
    device_in_use,
    persist_auth_if_changed,
)
from audible_sync.audible_api.client import Audible
from audible_sync.database import (
    finish_sync_run,
    get_account,
    get_accounts,
    get_sync_run,
    init_db,
    mark_account_synced,
    start_sync_run,
)
from audible_sync.model import Account, SyncOutcome
from audible_sync.pipeline.downloader import DownloadStats, download_books
from audible_sync.pipeline.notify import notify_run_finished
from audible_sync.pipeline.progress import Progress
from audible_sync.pipeline.runstate import RunStage, RunState
from audible_sync.pipeline.sync import sync_library
from audible_sync.settings import Settings

logger = logging.getLogger(__name__)


def _outcome(stats: DownloadStats) -> SyncOutcome:
    """How a run that got through both halves is recorded."""
    if stats.cancelled:
        return SyncOutcome.CANCELLED
    if stats.aborted or stats.credentials_error:
        return SyncOutcome.ABORTED
    return SyncOutcome.SUCCESS if stats.failed == 0 else SyncOutcome.PARTIAL


def _run_error(stats: DownloadStats, settings: Settings) -> str | None:
    """The text a run that got through both halves records in `error`, if any."""
    if stats.credentials_error:
        return f"Audible rejected the account's credentials; log in again. {stats.credentials_error}"
    if stats.aborted:
        return f"Gave up after {settings.max_consecutive_failures} books in a row were refused or failed"
    return None


def run_pipeline(
    settings: Settings,
    progress: Progress | None = None,
    *,
    state: RunState | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """
    Sync every account's library and download everything waiting, in one pass.

    Importable and free of logging side effects, so the service or scheduler can run
    the same pipeline without reconfiguring its own logger.

    Accounts are taken in turn, each as its own `sync_runs` row (see `run_account`).
    One account failing does not stop the others: its run is recorded and the loop
    moves on, and the first error is raised again once every account has had its turn,
    so a one-shot CLI run still exits non-zero. An account that is disabled or has no
    credentials is skipped with a warning. Each account is read again at its turn, so
    one removed, disabled or logged out while an earlier one was running is skipped.

    Args:
        settings: Validated settings (see `Settings.from_db`)
        progress: Where download byte progress is reported (see `audible_sync.pipeline.progress`).
            Defaults to reporting nothing, which is what a headless host wants
        state: Where the stage, current book and queue position are reported (see
            `audible_sync.pipeline.runstate`). Defaults to one nobody reads
        cancel: Event that stops the run once set; checked between accounts and,
            within an account, only during the download half (see `run_account`)
    """
    settings.create_folders()
    init_db()

    state = state or RunState()
    accounts = get_accounts()
    if not accounts:
        logger.warning("No Audible accounts: nothing to sync until one is added")
        return

    first_error: Exception | None = None
    for listed in accounts:
        if cancel is not None and cancel.is_set():
            logger.info("Sync cancelled before %s", listed.name)
            break
        # A login during the run must leave this account's device working until the run ends
        with device_in_use(listed.id):
            # Read again at its turn: the list is hours old by the last account, which may
            # have been deleted, disabled or logged out since (the API refuses to delete an
            # account only while the run is on it)
            account = get_account(listed.id)
            if account is None:
                logger.info("Skipping %s: removed since the run began", listed.name)
                continue
            if not account.runnable:
                if account.needs_login:
                    logger.warning("Skipping %s: it has no credentials, log in to it first", account.name)
                else:
                    logger.info("Skipping %s: disabled", account.name)
                continue

            try:
                run_account(account, settings, progress=progress, state=state, cancel=cancel)
            except Exception as error:
                logger.exception("Run failed for %s", account.name)
                first_error = first_error or error

    if first_error is not None:
        raise first_error


def run_account(
    account: Account,
    settings: Settings,
    progress: Progress | None = None,
    *,
    state: RunState | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """
    One account's pass: sync its library, then download what it has waiting.

    The pass is recorded as one `sync_runs` row, which is both the run history a UI
    reads and the cursor the account's next sync starts from. The row is opened before
    Audible is touched and closed on every exit, so a run that raises is recorded
    rather than leaving a row that looks in flight forever. Credentials the run
    refreshed are written back afterwards.

    `cancel` is only honoured during the download half: the sync is seconds and the
    downloads are hours, and a run that has read the library through still counts as
    a cursor (see `SyncOutcome`).
    """
    state = state or RunState()
    run_id = start_sync_run(account.id)
    state.begin(run_id)
    state.set_account({"id": account.id, "name": account.name, "country_code": account.country_code})
    # Held outside the try so a run that raises still records what it managed. `synced`
    # is whether the library was read through: a failure after that point must not hold
    # the cursor back, or every later run would re-read from an ever older cursor.
    synced = False
    books_seen = books_added = 0
    audible: Audible | None = None

    try:
        try:
            audible = Audible(authenticator_for(account))

            state.set_stage(RunStage.SYNCING)
            books_seen, books_added = sync_library(audible, account, auto_monitor_new=settings.auto_monitor_new)
            synced = True
            mark_account_synced(account.id)
            logger.info("%d books synced to database for %s", books_added, account.name)

            state.set_stage(RunStage.DOWNLOADING)
            stats = download_books(
                audible, settings, progress=progress, account_id=account.id, state=state, cancel=cancel, run_id=run_id
            )
        except Exception as error:
            finish_sync_run(
                run_id,
                outcome=SyncOutcome.PARTIAL if synced else SyncOutcome.FAILED,
                books_seen=books_seen,
                books_added=books_added,
                error=f"{type(error).__name__}: {error}",
            )
            raise
        finally:
            # Whatever happened, a token the run refreshed is worth keeping - but not at
            # the price of the run record: raising here would skip finish_sync_run below
            # and leave the row `running` for good
            if audible is not None:
                try:
                    persist_auth_if_changed(account, audible.auth)
                except Exception:
                    logger.exception("Could not store the refreshed credentials of %r", account)

        finish_sync_run(
            run_id,
            outcome=_outcome(stats),
            books_seen=books_seen,
            books_added=books_added,
            books_downloaded=stats.succeeded,
            books_failed=stats.failed,
            error=_run_error(stats, settings),
        )
    finally:
        # The row is closed on every path above, so what is sent is the final record
        if settings.webhook_url:
            run = get_sync_run(run_id)
            if run is not None:
                notify_run_finished(settings.webhook_url, run, account)
        # After the row is closed, so a poll never sees "nothing running" beside a run
        # the history still shows in flight
        state.end()
