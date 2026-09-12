import argparse
import logging
import threading

from src.accounts import (
    add_account_from_authenticator,
    authenticator_for,
    ensure_account_from_auth_file,
    persist_auth_if_changed,
)
from src.audible_client import Audible
from src.audible_login import MARKETPLACES, login_interactively
from src.database import finish_sync_run, get_accounts, init_db, mark_account_synced, start_sync_run
from src.downloader import DownloadStats, download_books
from src.model import Account, SyncOutcome
from src.progress import Progress, TqdmProgress
from src.runstate import RunStage, RunState
from src.settings import Settings, seed_settings_from_ini
from src.sync import sync_library

logger = logging.getLogger(__name__)


def configure_logging(debug: bool = False) -> None:
    """
    Set up logging for a CLI run.

    Called from the entry point rather than at import time: configuring the root
    logger on import would also reconfigure any host process that imports this
    module, which is exactly what the Milestone 3 service will do.
    """
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _outcome(stats: DownloadStats) -> SyncOutcome:
    """How a run that got through both halves is recorded."""
    if stats.cancelled:
        return SyncOutcome.CANCELLED
    return SyncOutcome.SUCCESS if stats.failed == 0 else SyncOutcome.PARTIAL


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
    credentials is skipped with a warning.

    Args:
        settings: Validated settings (see `Settings.from_db`)
        progress: Where download byte progress is reported (see `src.progress`).
            Defaults to reporting nothing, which is what a headless host wants
        state: Where the stage, current book and queue position are reported (see
            `src.runstate`). Defaults to one nobody reads
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
    for account in accounts:
        if cancel is not None and cancel.is_set():
            logger.info("Sync cancelled before %s", account.name)
            break
        if not account.enabled:
            logger.info("Skipping %s: disabled", account.name)
            continue
        if account.needs_login:
            logger.warning("Skipping %s: it has no credentials, log in to it first", account.name)
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
                audible, settings, progress=progress, account_id=account.id, state=state, cancel=cancel
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
            # Whatever happened, a token the run refreshed is worth keeping
            if audible is not None:
                persist_auth_if_changed(account, audible.auth)

        finish_sync_run(
            run_id,
            outcome=_outcome(stats),
            books_seen=books_seen,
            books_added=books_added,
            books_downloaded=stats.succeeded,
            books_failed=stats.failed,
        )
    finally:
        # After the row is closed, so a poll never sees "nothing running" beside a run
        # the history still shows in flight
        state.end()


def _prepare() -> Settings:
    """What every CLI command does first; see `main` for the order and why."""
    # The settings live in the database, so it is initialised before anything else.
    # `config.ini` is copied in the first time only; after that the table is the truth.
    init_db()
    seed_settings_from_ini()
    # Settings are read, and so validated, before anything else: a bad template or
    # bitrate now fails before the folders are created rather than after.
    settings = Settings.from_db()
    configure_logging(settings.debug)
    # An installation from before there were accounts has its auth file made into one
    ensure_account_from_auth_file(settings.auth_file)
    return settings


def run_command() -> None:
    """One sync and download pass, then exit."""
    settings = _prepare()
    # The progress bar is a terminal concern, injected here for the same reason logging
    # is configured here: a host process running the pipeline gets neither by surprise.
    run_pipeline(settings, progress=TqdmProgress())


def login_command(marketplace: str, *, name: str | None, monitor_existing: bool) -> None:
    """Add an account by signing in through the browser and pasting the address back."""
    _prepare()
    auth = login_interactively(marketplace)
    account_id = add_account_from_authenticator(auth, name=name, monitor_existing=monitor_existing)
    logger.info("Added account %d for Audible %s", account_id, marketplace)
    print(f"Logged in. Account {account_id} added; the next run will sync it.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.main", description="Sync an Audible library to DRM-free files")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="sync and download once, then exit (the default)")
    login = commands.add_parser("login", help="add an Audible account by signing in through your browser")
    login.add_argument(
        "--marketplace",
        required=True,
        choices=[m.country_code for m in MARKETPLACES],
        help="the Audible marketplace to sign in to, e.g. uk or us",
    )
    login.add_argument("--name", help="what to call the account; defaults to your name and the marketplace")
    login.add_argument(
        "--no-download-existing",
        action="store_true",
        help="leave the books the account already owns out of the download queue; only new purchases are fetched",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: `run` (the default) or `login`."""
    args = build_parser().parse_args(argv)
    if args.command == "login":
        login_command(args.marketplace, name=args.name, monitor_existing=not args.no_download_existing)
    else:
        run_command()


if __name__ == "__main__":
    main()
