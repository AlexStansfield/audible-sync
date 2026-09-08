import configparser
import logging
from pathlib import Path

from src.audible import Audible
from src.database import init_db
from src.downloader import download_books
from src.encoding import DEFAULT_BITRATE, DEFAULT_FORMAT, validate_encoding
from src.naming import DEFAULT_FILENAME_TEMPLATE, DEFAULT_FOLDER_TEMPLATE, validate_templates
from src.sync import sync_library

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    # Load the Config
    config = configparser.ConfigParser()
    config.read("config/config.ini")

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

    # Get Audible Sync
    if config.has_option("sync", "audible-auth-file"):
        audible_json = config.get("sync", "audible-auth-file")
    else:
        audible_json = f"{Path.home()}/.audible/audible.json"

    audible = Audible(audible_json)

    # Sync the library
    books_synced = sync_library(audible)
    logger.info("%d books synced to database", books_synced)

    # Download Books
    max_download: int = None
    if config.has_option("sync", "max-download"):
        max_download = config.getint("sync", "max-download")
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
