"""
The shapes the API speaks: request bodies and responses.

Pydantic models rather than the dataclasses in `src.model`, because these are the
contract the web app codes against and they can differ from the storage shapes - a
book will carry its primary series, a run its account name - without either side
bending to the other. Anything that maps straight from a dataclass uses
`from_attributes`.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.model import BookStatus, SyncOutcome
from src.runstate import RunStage


class Health(BaseModel):
    status: str
    version: str


class Message(BaseModel):
    """A one-line acknowledgement for an action that returns nothing else."""

    status: str


class SchedulerStatus(BaseModel):
    enabled: bool
    interval_minutes: int
    # ISO 8601 UTC, None while the schedule is disabled
    next_run_at: str | None
    running: bool


class BookRef(BaseModel):
    asin: str
    title: str


class Transfer(BaseModel):
    desc: str | None
    bytes: int
    # None when the response carried no Content-Length
    total: int | None


class RunSnapshot(BaseModel):
    """The run in flight, straight from `RunState.snapshot()`."""

    run_id: int
    started_at: str
    stage: RunStage | None
    account: dict[str, Any] | None
    book: BookRef | None
    books_done: int
    books_total: int
    transfer: Transfer | None


class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # None on a run from before there were accounts
    account_id: int | None
    started_at: str
    finished_at: str | None
    outcome: SyncOutcome | None
    books_seen: int
    books_added: int
    books_downloaded: int
    books_failed: int
    error: str | None


class SyncRunList(BaseModel):
    items: list[SyncRunOut]
    total: int


class AccountOut(BaseModel):
    """An account as the API shows it: everything but the credentials."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    country_code: str
    customer_name: str | None
    enabled: bool
    monitor_existing: bool
    # True while the account has no working credentials; the pipeline skips it
    needs_login: bool
    created_at: str | None
    last_synced_at: str | None


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class AccountImport(BaseModel):
    """Import an `audible-cli` style auth file the service can read."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1)
    monitor_existing: bool = True


class MarketplaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    country_code: str
    domain: str
    name: str


class LoginStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country_code: str = Field(min_length=2)


class LoginStarted(BaseModel):
    """Step one: open `url` in a browser, then post the address it lands on to step two."""

    login_id: str
    url: str
    # ISO 8601 UTC
    expires_at: str


class LoginComplete(BaseModel):
    """
    Step two. `response_url` is the "page not found" address the browser lands on after
    signing in; `monitor_existing` is whether the library the account already holds is
    queued for download or inserted unmonitored so the user picks.
    """

    model_config = ConfigDict(extra="forbid")

    response_url: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1)
    monitor_existing: bool = True


class SeriesEntry(BaseModel):
    title: str | None
    sequence: str | None
    series_asin: str | None = None


class BookOut(BaseModel):
    """A book as the API shows it, with the series it files under worked out."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    asin: str
    title: str
    subtitle: str
    authors: list[str]
    narrators: list[str]
    series: list[SeriesEntry]
    # The one series the book is filed and tagged under (see `Book.primary_series`)
    primary_series: SeriesEntry | None
    genres: list[str]
    length: int
    is_finished: bool
    percent_complete: float
    date_added: str | None
    release_date: str | None
    cover_url: str
    has_pdf: bool
    is_consumable: bool
    status: BookStatus | None
    monitored: bool
    attempts: int
    last_error: str | None
    last_attempt_at: str | None
    file_path: str | None
    pdf_path: str | None
    cover_path: str | None
    annotations_path: str | None
    encoding_format: str | None
    downloaded_at: str | None


class BookList(BaseModel):
    items: list[BookOut]
    total: int
    page: int
    page_size: int


class BookUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monitored: bool


class BookAction(BaseModel):
    """What a per-book action did."""

    status: str
    removed: list[str] = []


class LogEntry(BaseModel):
    time: str
    level: str
    logger: str
    message: str


class LogList(BaseModel):
    items: list[LogEntry]


class WebhookTest(BaseModel):
    """Try a webhook; `url` defaults to the configured one."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = None


class BookCounts(BaseModel):
    by_status: dict[str, int]
    monitored: int
    unmonitored: int
    total: int


class AccountStats(BaseModel):
    account: AccountOut
    books: BookCounts
    bytes_on_disk: int
    last_run: SyncRunOut | None


class Stats(BaseModel):
    accounts: list[AccountStats]
    books: BookCounts
    bytes_on_disk: int
    last_run: SyncRunOut | None
    next_run_at: str | None


class Status(BaseModel):
    scheduler: SchedulerStatus
    current_run: RunSnapshot | None
    last_run: SyncRunOut | None
    accounts: list[AccountOut]


class SettingsOut(BaseModel):
    """Every runtime setting (`DB_SETTING_KEYS`), typed; paths as text."""

    model_config = ConfigDict(from_attributes=True)

    debug: bool
    sync_enabled: bool
    sync_interval_minutes: int
    max_download: int | None
    max_attempts: int
    auto_monitor_new: bool
    download_folder: str
    audiobook_folder: str
    folder_template: str
    filename_template: str
    encoding_format: str
    bitrate: int
    webhook_url: str | None

    @field_validator("download_folder", "audiobook_folder", mode="before")
    @classmethod
    def _path_as_text(cls, value: Any) -> Any:
        # `Settings` holds Paths; the wire carries text
        return str(value) if isinstance(value, Path) else value


class SettingsUpdate(BaseModel):
    """
    A partial update: only the fields sent are changed.

    Every field is optional and defaults to "not sent", which is distinct from `null`:
    `{"max_download": null}` clears the limit, omitting it leaves it alone. Unknown
    fields are rejected rather than ignored, so a typo cannot pass silently.
    """

    model_config = ConfigDict(extra="forbid")

    debug: bool | None = None
    sync_enabled: bool | None = None
    sync_interval_minutes: int | None = None
    max_download: int | None = None
    max_attempts: int | None = None
    auto_monitor_new: bool | None = None
    download_folder: str | None = Field(default=None, min_length=1)
    audiobook_folder: str | None = Field(default=None, min_length=1)
    folder_template: str | None = None
    filename_template: str | None = None
    encoding_format: str | None = None
    bitrate: int | None = None
    webhook_url: str | None = None
