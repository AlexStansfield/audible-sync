import argparse
import logging
import threading

from src import crypto, webauth
from src.accounts import (
    authenticator_for,
    check_marketplace,
    persist_auth_if_changed,
    replace_account_auth,
    store_login,
)
from src.audible_client import Audible
from src.audible_login import MARKETPLACES, LoginFailed, discard_device, login_interactively, login_with_code
from src.crypto import load_secret_key
from src.database import (
    count_unreadable_secrets,
    encrypt_stored_secrets,
    finish_sync_run,
    get_account,
    get_accounts,
    get_sync_run,
    init_db,
    mark_account_synced,
    start_sync_run,
)
from src.downloader import DownloadStats, download_books
from src.model import Account, StoredLogin, SyncOutcome
from src.notify import notify_run_finished
from src.progress import Progress, TqdmProgress
from src.runstate import RunStage, RunState
from src.settings import Settings, seed_settings_from_ini
from src.sync import sync_library

# Run as `python -m`, this module is `__main__`; name the logger after the module anyway
logger = logging.getLogger("src.main" if __name__ == "__main__" else __name__)


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
    for listed in accounts:
        if cancel is not None and cancel.is_set():
            logger.info("Sync cancelled before %s", listed.name)
            break
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
    # The secret key once logging can report where it came from; every later step that
    # stores or reads credentials needs it
    crypto.configure(load_secret_key())
    encrypt_stored_secrets()
    _warn_about_another_key()
    return settings


def _warn_about_another_key() -> None:
    """
    Say so when the stored secrets were written under another key than this process has.

    The usual cause is a CLI on the host next to a service in Docker: the CLI finds no
    `secrets/secret.key` where it looks, generates one, and a login stored with it is
    unreadable to the service. A warning rather than a refusal, because a key that was
    really lost leaves logging in again as the only way back.
    """
    unreadable = count_unreadable_secrets()
    if unreadable:
        logger.warning(
            "%d stored secret(s) were encrypted with another key than this one, so the service and this "
            "command are not using the same key: credentials stored now will be unreadable to the service. "
            "Set AUDIBLE_SYNC_SECRET_KEY or AUDIBLE_SYNC_SECRET_KEY_FILE to the service's key",
            unreadable,
        )


def run_command() -> None:
    """One sync and download pass, then exit."""
    settings = _prepare()
    # The progress bar is a terminal concern, injected here for the same reason logging
    # is configured here: a host process running the pipeline gets neither by surprise.
    run_pipeline(settings, progress=TqdmProgress())


def login_command(
    marketplace: str, *, name: str | None, monitor_existing: bool, account_id: int | None = None, browser: bool = False
) -> None:
    """
    Add an account by signing in to Amazon - a code entered on any device, or with
    `browser` a sign-in in the browser and the address pasted back - or with
    `account_id` give an existing account new credentials and keep its library. A login
    an account already holds gives that account new credentials too.
    """
    _prepare()
    existing = None
    if account_id is not None:
        existing = get_account(account_id)
        if existing is None:
            raise SystemExit(f"No account with id {account_id}")
        try:
            check_marketplace(existing, marketplace)
        except ValueError as error:
            raise SystemExit(str(error)) from None
    try:
        auth = login_interactively(marketplace) if browser else login_with_code(marketplace)
    except LoginFailed as error:
        raise SystemExit(f"Login failed: {error}") from None
    try:
        if existing is not None:
            stored = replace_account_auth(existing, auth)
        else:
            stored = store_login(auth, name=name, monitor_existing=monitor_existing)
    except Exception as error:
        logger.exception("Could not store the login")
        # Otherwise the device Amazon just registered stays on the account with nothing holding it
        discard_device(auth)
        raise SystemExit(f"Logged in, but the account could not be stored: {error}") from None
    if existing is not None:
        print(
            f"Logged in. Account {existing.id} ({existing.name}) has new credentials; its library is kept."
            + _requeued_note(stored)
            + _paused_note(existing)
        )
        return
    if stored.replaced:
        account = get_account(stored.account_id)
        ignored = (
            " The name and download choice given only apply to a new account." if name or not monitor_existing else ""
        )
        print(
            f"Logged in. Account {stored.account_id} ({account.name}) already held this login: "
            f"new credentials, same library.{ignored}{_requeued_note(stored)}{_paused_note(account)}"
        )
        return
    logger.info("Added account %d for Audible %s", stored.account_id, marketplace)
    print(f"Logged in. Account {stored.account_id} added; the next run will sync it.")


def _requeued_note(stored: StoredLogin) -> str:
    if not stored.requeued:
        return ""
    if stored.requeued == 1:
        return " 1 refused book is waiting to download again."
    return f" {stored.requeued} refused books are waiting to download again."


def _paused_note(account: Account) -> str:
    return " It is paused, so runs skip it until it is turned back on." if not account.enabled else ""


def reset_password_command(*, reset_username: bool = False) -> None:
    """
    The way back from a forgotten web app password: a new one, printed here, and every
    browser signed out. With `reset_username` the username goes back to `admin` too.

    The service needs no restart: it reads the password and the sessions from the
    database on every sign-in and request.
    """
    _prepare()
    if not webauth.password_readable():
        # Stored now, the password would be unreadable to the service: the reset would
        # end every session and leave nobody able to sign in
        raise SystemExit(
            "The stored password was written with another key than this command has, so a password set "
            "from here would not work in the service. Run the command where the service runs, e.g. "
            "docker compose exec audible-sync uv run --no-sync python -m src.main reset-password, or set "
            "AUDIBLE_SYNC_SECRET_KEY or AUDIBLE_SYNC_SECRET_KEY_FILE to the service's key"
        )
    password = webauth.reset_password(reset_username=reset_username)
    user = webauth.current_user()
    username = user.username if user else webauth.DEFAULT_USERNAME
    logger.info("Reset the web app password; every session was ended")
    print(f"Password reset. Sign in as {username} with the password: {password}")
    print("Every browser has been signed out. Choose your own password under Your sign-in.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.main", description="Sync an Audible library to DRM-free files")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="sync and download once, then exit (the default)")
    login = commands.add_parser("login", help="add an Audible account by signing in to Amazon")
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
    login.add_argument(
        "--account",
        type=int,
        metavar="ID",
        help="replace the credentials of this existing account instead of adding one; its library is kept",
    )
    login.add_argument(
        "--browser",
        action="store_true",
        help="sign in through a browser and paste the address back, instead of entering a code at Amazon",
    )
    reset = commands.add_parser(
        "reset-password", help="give the web app's user a new password, printed here, and sign out every browser"
    )
    reset.add_argument("--username", action="store_true", help="also set the username back to admin")
    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: `run` (the default), `login` or `reset-password`."""
    args = build_parser().parse_args(argv)
    if args.command == "reset-password":
        reset_password_command(reset_username=args.username)
    elif args.command == "login":
        login_command(
            args.marketplace,
            name=args.name,
            monitor_existing=not args.no_download_existing,
            account_id=args.account,
            browser=args.browser,
        )
    else:
        run_command()


if __name__ == "__main__":
    main()
