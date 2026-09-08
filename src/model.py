from datetime import datetime


class Book:
    def __init__(
        self,
        asin: str,
        title: str,
        subtitle: str = "",
        authors: list[str] | None = None,
        narrators: list[str] | None = None,
        series: list[dict[str, str]] | None = None,
        genres: list[str] | None = None,
        length: int = 0,
        is_finished: bool = False,
        percent_complete: float = 0.0,
        date_added: datetime | None = None,
        release_date: str | None = None,
        cover_url: str = "",
        has_pdf: bool = False,
    ):
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
        self.has_pdf = has_pdf

    def __repr__(self):
        return (
            f"Book(asin={self.asin}, title={self.title}, authors={self.authors}, "
            f"release_date={self.release_date}, is_finished={self.is_finished})"
        )
