import json
import sqlite3
from dataclasses import dataclass, field


def _json_list(value: str | None) -> list:
    """Decode a JSON list column, treating NULL, '' and a stored `null` as an empty list."""
    return (json.loads(value) or []) if value else []


@dataclass
class Book:
    """
    One row of the `library` table, and the object `audible.py` builds from the API.

    The first block of fields comes from the Audible API; the second only ever
    exists in the database, so a freshly synced book leaves it unset.

    Field order deliberately does not mirror the table: `has_pdf` sits between
    `annotations_path` and `encoding_format` in the schema but stays with the API
    fields here. Nothing is positional against a row any more, so the two orders
    are free to differ.
    """

    asin: str
    title: str
    subtitle: str = ""
    authors: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    series: list[dict[str, str | None]] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    length: int = 0
    is_finished: bool = False
    percent_complete: float = 0.0
    # ISO 8601 string exactly as Audible returns it, e.g. "2024-01-01T00:00:00Z".
    # Stored, sorted and compared as text; never parsed into a datetime.
    date_added: str | None = None
    release_date: str | None = None
    cover_url: str = ""
    has_pdf: bool = False

    # Database only, so None on a book that came straight from the Audible API
    status: str | None = None
    pdf_path: str | None = None
    cover_path: str | None = None
    annotations_path: str | None = None
    encoding_format: str | None = None
    downloaded_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Book":
        """
        Build a book from a `library` row.

        Owns the JSON decode and the 0/1 to bool coercion so that no caller repeats
        either. Reads by column name, so `SELECT *` is correct even on a database
        where `_migrate_schema` appended columns in a different order than the DDL,
        and a column the row does not carry falls back to its field default.
        """
        data = dict(row)
        return cls(
            asin=data["asin"],
            title=data.get("title") or "",
            subtitle=data.get("subtitle") or "",
            authors=_json_list(data.get("authors")),
            narrators=_json_list(data.get("narrators")),
            series=_json_list(data.get("series")),
            genres=_json_list(data.get("genres")),
            length=data.get("length") or 0,
            is_finished=bool(data.get("is_finished")),
            percent_complete=data.get("percent_complete") or 0.0,
            date_added=data.get("date_added"),
            release_date=data.get("release_date"),
            cover_url=data.get("cover_url") or "",
            has_pdf=bool(data.get("has_pdf")),
            status=data.get("status"),
            pdf_path=data.get("pdf_path"),
            cover_path=data.get("cover_path"),
            annotations_path=data.get("annotations_path"),
            encoding_format=data.get("encoding_format"),
            downloaded_at=data.get("downloaded_at"),
        )

    def __repr__(self):
        # Kept deliberately: the generated repr would put the cover URL and three
        # file paths into every log line that formats a book.
        return (
            f"Book(asin={self.asin}, title={self.title}, authors={self.authors}, "
            f"release_date={self.release_date}, is_finished={self.is_finished})"
        )
