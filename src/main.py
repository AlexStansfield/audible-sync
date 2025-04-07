import configparser
import logging
import sys
from pathlib import Path
from src.database import init_db
from src.sync import sync_library
from src.audible import Audible
from src.downloader import download_books

def setup_logging(level=logging.INFO):
    """Configure root logger and format."""
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

if __name__ == "__main__":
    # Load the Config
    setup_logging() 
    logger = logging.getLogger("main")
    logger.info("Loading config")
    config = configparser.ConfigParser()
    config.read('config/config.ini')

    # Initialise the Database if doesn't exist
    
    init_db()

    # Create folders if not exists
    Path(config['folders']['downloads']).mkdir(parents=True, exist_ok=True)
    Path(config['folders']['audiobooks']).mkdir(parents=True, exist_ok=True)

    # Get Audible Sync
    if config.has_option('sync', 'audible-auth-file'):
        audible_json = config.get('sync', 'audible-auth-file')
    else:
        audible_json = "{0}/.audible/audible.json".format(Path.home())

    audible = Audible(audible_json)

    # Sync the library
    books_synced = sync_library(audible)
    logger.info(f"{books_synced} books synced to database")

    # Download Books
    max_download: int = None
    if config.has_option('sync', 'max-download'):
        max_download = config.getint('sync', 'max-download')
    download_books(audible, config['folders']['downloads'], config['folders']['audiobooks'], max=max_download)