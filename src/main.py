import logging

from src.audible import Audible
from src.database import init_db
from src.downloader import download_books
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


def run_pipeline(settings: Settings) -> None:
    """
    Sync the library and download everything waiting, in one pass.

    Importable and free of logging side effects, so the Milestone 3 service or
    scheduler can run the same pipeline without reconfiguring its own logger.

    Args:
        settings: Validated settings (see `Settings.from_ini`)
    """
    settings.create_folders()
    init_db()

    audible = Audible(str(settings.auth_file))

    books_synced = sync_library(audible)
    logger.info("%d books synced to database", books_synced)

    download_books(audible, settings)


def main() -> None:
    """CLI entry point: read the config, set up logging, run one sync and download pass."""
    # Settings are read, and so validated, before anything else: a bad template or
    # bitrate now fails before the folders are created rather than after.
    settings = Settings.from_ini()
    configure_logging(settings.debug)
    run_pipeline(settings)


if __name__ == "__main__":
    main()
