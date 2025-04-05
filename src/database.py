import sqlite3
from typing import List
from src.model import Book
import json

DB_FILE = "audible_sync.db"

def _get_connection():
    return sqlite3.connect(DB_FILE)

def init_db():
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS library (
            asin TEXT PRIMARY KEY,
            title TEXT,
            subtitle TEXT,
            authors JSON,
            narrators JSON,
            series JSON,
            genres JSON,
            length INTEGER,
            is_finished BOOLEAN,
            percent_complete REAL,
            date_added TEXT,
            release_date TEXT,
            cover_url TEXT
        )
    """)
    conn.commit()
    conn.close()

def update_books(books: List[Book]):
    conn = _get_connection()
    cursor = conn.cursor()
    
    books_synced = 0
    for book in books:
        # Check if the book already exists in the database
        existing_book = get_book_by_asin(book.asin)
        
        # If the book doesn't exist, insert it into the database
        if existing_book is None:
            cursor.execute("""
            INSERT INTO library (asin, title, subtitle, authors, narrators, series, genres, 
                               length, is_finished, percent_complete, date_added, release_date, cover_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (book.asin, book.title, book.subtitle, json.dumps(book.authors), json.dumps(book.narrators), 
                  json.dumps(book.series), json.dumps(book.genres), book.length, book.is_finished, book.percent_complete, 
                  book.date_added, book.release_date, book.cover_url))
            books_synced += 1
    
    conn.commit()
    conn.close()

    return books_synced

def get_book_by_asin(asin):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM library WHERE asin=?", (asin,))
    return cursor.fetchone()