import json
import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum


class BookStatus(StrEnum):
    """
    Where a book is in the download pipeline.

    A `StrEnum` rather than a plain `Enum` so a member *is* the text stored in the
    `status` column: the database keeps the values it already holds, no migration
    has to rewrite a row, and a comparison against a plain string still works.

    `DOWNLOADING` exists so a scheduler tick starting while a download is running
    cannot pick the same row, and `FAILED` is terminal - without it a book that
    could never succeed was re-licensed, re-downloaded and re-failed on every run.
    """

    WAITING_DOWNLOAD = "waiting_download"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    FAILED = "failed"


def _json_list(value: str | None) -> list:
    """Decode a JSON list column, treating NULL, '' and a stored `null` as an empty list."""
    return (json.loads(value) or []) if value else []


def _book_status(value: str | None) -> BookStatus | None:
    """
    Map the stored `status` text onto the enum, tolerating anything unrecognised.

    A database written by an older version, or edited by hand, can hold a value this
    build has never heard of. Raising here would make the whole row unreadable, so an
    unknown status reads back as None - the same "we do not know" the field already
    carries for a book that came straight from the API. Nothing selects on the Python
    value (the queue is a SQL predicate), so such a row is simply never picked up.
    """
    if value is None:
        return None
    try:
        return BookStatus(value)
    except ValueError:
        return None


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
    status: BookStatus | None = None
    # `attempts` counts claims, not failures, so it survives a success as a record of
    # what the book cost. Its default must stay in step with the column default.
    attempts: int = 0
    last_error: str | None = None
    last_attempt_at: str | None = None
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
            status=_book_status(data.get("status")),
            attempts=data.get("attempts") or 0,
            last_error=data.get("last_error"),
            last_attempt_at=data.get("last_attempt_at"),
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
