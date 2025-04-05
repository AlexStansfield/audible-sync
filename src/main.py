from src.database import init_db, get_book_by_asin
from src.sync import sync_library
from src.audible import get_download_link
from src.downloader import Downloader

if __name__ == "__main__":
    init_db()
    books_synced = sync_library()
    print(books_synced)
    book = get_book_by_asin("1838773193")
    print(book)
    downloader = Downloader()
    downloader.download_book(book)
    # print(url)