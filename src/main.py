import configparser
from pathlib import Path
from src.database import init_db
from src.sync import sync_library
from src.audible import Audible
from src.downloader import download_books

if __name__ == "__main__":
    # Load the Config
    config = configparser.ConfigParser()
    config.read('config/config.ini')

    # Initialise the Database if doesn't exist
    init_db()

    # Create folders if not exists
    Path(config['folders']['downloads']).mkdir(parents=True, exist_ok=True)
    Path(config['folders']['audiobooks']).mkdir(parents=True, exist_ok=True)

    # Get Audible Sync
    audible = Audible(config['sync']['audible-auth-file'])

    # Sync the library
    books_synced = sync_library(audible)
    print("{0} books synced to database".format(books_synced))

    # Download Books
    download_books(audible, config['folders']['downloads'], config['folders']['audiobooks'], max=config['sync']['max-download'])