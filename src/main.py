import configparser
from src.database import init_db, get_book_by_asin
from src.sync import sync_library
from src.audible import Audible
from src.downloader import Downloader

if __name__ == "__main__":
    # Load the Config
    config = configparser.ConfigParser()
    config.read('config/config.ini')

    # Initialise the Database if doesn't exist
    init_db()

    # Get Audible Sync
    audible = Audible(config['audible-sync']['audible-auth-file'])

    books_synced = sync_library(audible)
    print(books_synced)
    book = get_book_by_asin("1838773193")
    print(book)
    book2 = audible.get_book("1838773193")
    print(book2)

    # downloader = Downloader()
    # downloader.download_book(book)
    # print(url)