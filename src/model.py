from datetime import datetime, date
from typing import List, Dict

class Book:
    def __init__(self, asin: str, title: str, subtitle: str = "", 
                 authors: List[str] = None, narrators: List[str] = None, series: List[Dict[str, str]] = None,
                 genres: List[str] = None, length: int = 0, is_finished: bool = False, 
                 percent_complete: float = 0.0, date_added: datetime = None, 
                 release_date: str = None, cover_url: str = ""):
        self.asin = asin
        self.title = title
        self.subtitle = subtitle
        self.authors = authors if authors is not None else []
        self.narrators = narrators if narrators is not None else []
        self.series = series if series is not None else []
        self.genres = genres if genres is not None else []
        self.length = length
        self.is_finished = is_finished
        self.percent_complete = percent_complete
        self.date_added = date_added
        self.release_date = release_date
        self.cover_url = cover_url
    
    def __repr__(self):
        return f"Book(asin={self.asin}, title={self.title}, authors={self.authors}, release_date={self.release_date}, is_finished={self.is_finished})"