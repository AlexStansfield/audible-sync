import logging

from src.audible_client import Audible
from src.database import finish_sync_run, init_db, start_sync_run
from src.downloader import download_books
from src.model import SyncOutcome
from src.progress import Progress, TqdmProgress
from src.settings import Settings
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


def run_pipeline(settings: Settings, progress: Progress | None = None) -> None:
    """
    Sync the library and download everything waiting, in one pass.

    Importable and free of logging side effects, so the Milestone 3 service or
    scheduler can run the same pipeline without reconfiguring its own logger.

    The whole pass is recorded as one `sync_runs` row, which is both the run history a
    UI reads and the cursor the next sync starts from. The row is opened before Audible
    is touched and closed on every exit, so a run that raises is recorded rather than
    leaving a row that looks in flight forever.

    Args:
        settings: Validated settings (see `Settings.from_ini`)
        progress: Where download byte progress is reported (see `src.progress`).
            Defaults to reporting nothing, which is what a headless host wants
    """
    settings.create_folders()
    init_db()

    run_id = start_sync_run()
    # Held outside the try so a run that raises still records what it managed. `synced`
    # is whether the library was read through: a failure after that point must not hold
    # the cursor back, or every later run would re-read from an ever older cursor.
    synced = False
    books_seen = books_added = 0

    try:
        audible = Audible(str(settings.auth_file))

        books_seen, books_added = sync_library(audible)
        synced = True
        logger.info("%d books synced to database", books_added)

        stats = download_books(audible, settings, progress=progress)
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
        outcome=SyncOutcome.SUCCESS if stats.failed == 0 else SyncOutcome.PARTIAL,
        books_seen=books_seen,
        books_added=books_added,
        books_downloaded=stats.succeeded,
        books_failed=stats.failed,
    )


def main() -> None:
    """CLI entry point: read the config, set up logging, run one sync and download pass."""
    # Settings are read, and so validated, before anything else: a bad template or
    # bitrate now fails before the folders are created rather than after.
    settings = Settings.from_ini()
    configure_logging(settings.debug)
    # The progress bar is a terminal concern, injected here for the same reason logging
    # is configured here: a host process running the pipeline gets neither by surprise.
    run_pipeline(settings, progress=TqdmProgress())


if __name__ == "__main__":
    main()
