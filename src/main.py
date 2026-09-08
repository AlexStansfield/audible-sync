import configparser
import logging
from pathlib import Path

from src.audible import Audible
from src.database import init_db
from src.downloader import download_books
from src.encoding import DEFAULT_BITRATE, DEFAULT_FORMAT, validate_encoding
from src.naming import DEFAULT_FILENAME_TEMPLATE, DEFAULT_FOLDER_TEMPLATE, validate_templates
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


def validate_max_download(max_download: int | None) -> None:
    """
    Check the download limit up front, like the naming and encoding settings.

    Raises:
        ValueError: if the limit is set but not a positive number
    """
    if max_download is not None and max_download < 1:
        raise ValueError(f"sync max-download must be 1 or more, got {max_download}")


if __name__ == "__main__":
    # Load the Config
    config = configparser.ConfigParser()
    config.read("config/config.ini")

    configure_logging(config.getboolean("general", "debug", fallback=False))

    # Initialise the Database if doesn't exist
    init_db()

    # Create folders if not exists
    Path(config["folders"]["downloads"]).mkdir(parents=True, exist_ok=True)
    Path(config["folders"]["audiobooks"]).mkdir(parents=True, exist_ok=True)

    # Naming templates for converted books, validated up front so a typo fails before any download
    folder_template = config.get("naming", "folder", fallback=DEFAULT_FOLDER_TEMPLATE)
    filename_template = config.get("naming", "filename", fallback=DEFAULT_FILENAME_TEMPLATE)
    validate_templates(folder_template, filename_template)

    # Output format and Opus bitrate, validated up front for the same reason
    encoding_format = config.get("encoding", "format", fallback=DEFAULT_FORMAT)
    bitrate = config.getint("encoding", "bitrate", fallback=DEFAULT_BITRATE)
    validate_encoding(encoding_format, bitrate)

    # How many books to process this run, same again
    max_download: int | None = config.getint("sync", "max-download", fallback=None)
    validate_max_download(max_download)

    # Get Audible Sync
    audible_json = config.get("sync", "audible-auth-file", fallback=None) or str(
        Path.home() / ".audible" / "audible.json"
    )

    audible = Audible(audible_json)

    # Sync the library
    books_synced = sync_library(audible)
    logger.info("%d books synced to database", books_synced)

    # Download Books
    download_books(
        audible,
        config["folders"]["downloads"],
        config["folders"]["audiobooks"],
        max=max_download,
        folder_template=folder_template,
        filename_template=filename_template,
        encoding_format=encoding_format,
        bitrate=bitrate,
    )
