from src.audible import get_library
from src.database import update_books

def sync_library():
    library = get_library()
    books_synced = update_books(library)
    
    return books_synced