import logging
import threading

from src.audible_client import Audible
from src.database import finish_sync_run, init_db, start_sync_run
from src.downloader import DownloadStats, download_books
from src.model import SyncOutcome
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
    Sync the library and download everything waiting, in one pass.

    Importable and free of logging side effects, so the service or scheduler can run
    the same pipeline without reconfiguring its own logger.

    The whole pass is recorded as one `sync_runs` row, which is both the run history a
    UI reads and the cursor the next sync starts from. The row is opened before Audible
    is touched and closed on every exit, so a run that raises is recorded rather than
    leaving a row that looks in flight forever.

    Args:
        settings: Validated settings (see `Settings.from_db`)
        progress: Where download byte progress is reported (see `src.progress`).
            Defaults to reporting nothing, which is what a headless host wants
        state: Where the stage, current book and queue position are reported (see
            `src.runstate`). Defaults to one nobody reads
        cancel: Event that stops the run once set. Only honoured during the download
            half: the sync is seconds and the downloads are hours, and a run that has
            read the library through still counts as a cursor (see `SyncOutcome`)
    """
    settings.create_folders()
    init_db()

    state = state or RunState()
    run_id = start_sync_run()
    state.begin(run_id)
    # Held outside the try so a run that raises still records what it managed. `synced`
    # is whether the library was read through: a failure after that point must not hold
    # the cursor back, or every later run would re-read from an ever older cursor.
    synced = False
    books_seen = books_added = 0

    try:
        try:
            audible = Audible(str(settings.auth_file))

            state.set_stage(RunStage.SYNCING)
            books_seen, books_added = sync_library(audible)
            synced = True
            logger.info("%d books synced to database", books_added)

            state.set_stage(RunStage.DOWNLOADING)
            stats = download_books(audible, settings, progress=progress, state=state, cancel=cancel)
        except Exception as error:
            finish_sync_run(
                run_id,
                outcome=SyncOutcome.PARTIAL if synced else SyncOutcome.FAILED,
                books_seen=books_seen,
                books_added=books_added,
                error=f"{type(error).__name__}: {error}",
            )
            raise

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


def main() -> None:
    """CLI entry point: read the settings, set up logging, run one sync and download pass."""
    # The settings live in the database, so it is initialised before anything else.
    # `config.ini` is copied in the first time only; after that the table is the truth.
    init_db()
    seed_settings_from_ini()
    # Settings are read, and so validated, before anything else: a bad template or
    # bitrate now fails before the folders are created rather than after.
    settings = Settings.from_db()
    configure_logging(settings.debug)
    # The progress bar is a terminal concern, injected here for the same reason logging
    # is configured here: a host process running the pipeline gets neither by surprise.
    run_pipeline(settings, progress=TqdmProgress())


if __name__ == "__main__":
    main()
