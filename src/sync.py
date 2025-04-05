from src.database import update_books
from src.audible import Audible

def sync_library(audible: Audible):
    # Check Books Count
    library = audible.get_library()
    books_synced = update_books(library)
    
    return books_synced