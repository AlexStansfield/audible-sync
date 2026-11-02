"""
The shapes the API speaks: request bodies and responses.

Pydantic models rather than the dataclasses in `src.model`, because these are the
contract the web app codes against and they can differ from the storage shapes - a
book will carry its primary series, a run its account name - without either side
bending to the other. Anything that maps straight from a dataclass uses
`from_attributes`.
"""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.library import BulkAction, SkipReason
from src.model import BookStatus, SyncOutcome
from src.runstate import RunStage
from src.webauth import MAX_PASSWORD_LENGTH, MAX_USERNAME_LENGTH


class Health(BaseModel):
    status: str
    version: str


class Message(BaseModel):
    """A one-line acknowledgement for an action that returns nothing else."""

    status: str


# Capped here as well as in `src.webauth`, so an endpoint that needs no sign-in never
# hashes a password of any length a client cares to send. The username cap
# is loose: `webauth` enforces the real one when it is set.
Password = Annotated[str, Field(max_length=MAX_PASSWORD_LENGTH)]
Username = Annotated[str, Field(max_length=4 * MAX_USERNAME_LENGTH)]


class SignIn(BaseModel):
    """`POST /api/auth/login`. `remember` makes the session cookie outlive the browser."""

    username: Username
    password: Password
    remember: bool = False


class Me(BaseModel):
    """The web app's user. `generated_password` asks the UI to suggest choosing one."""

    username: str
    generated_password: bool


class UsernameChange(BaseModel):
    username: Username
    current_password: Password


class PasswordChange(BaseModel):
    current_password: Password
    # webauth's validator gives the message for one that is too long or too short
    new_password: str


class SchedulerStatus(BaseModel):
    enabled: bool
    interval_minutes: int
    # ISO 8601 UTC, None while the schedule is disabled
    next_run_at: str | None
    running: bool


class BookRef(BaseModel):
    # The library row: what /api/books/{id} and the cover take. None only for a book
    # that never came from the database, which the pipeline does not report
    id: int | None
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


class MarketplaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    country_code: str
    domain: str
    name: str


class LoginStart(BaseModel):
    """
    Start signing in to a marketplace. The account the login will produce is described
    here, since with the default `code` method nothing else is posted: `name` and
    `monitor_existing` (whether the library the account already holds is queued for
    download or inserted unmonitored so the user picks) for a new account, or
    `account_id` to give an existing account the new credentials instead, keeping its
    library - the way back when a registration stops working - with
    `deregister_previous` removing its old device from Amazon first, best effort.

    `method` is `code` (show a code, the user enters it at Amazon on any device, the
    service polls until confirmed) or `browser` (the user signs in at `url` in a browser
    and posts the "page not found" address it lands on to step two).
    """

    model_config = ConfigDict(extra="forbid")

    country_code: str = Field(min_length=2)
    method: Literal["code", "browser"] = "code"
    name: str | None = Field(default=None, min_length=1)
    monitor_existing: bool = True
    account_id: int | None = None
    deregister_previous: bool = True


class LoginOut(BaseModel):
    """
    A login in flight or finished. `status` is `pending` until the user has done their
    part, then `confirmed` with the `account`, or `expired` (start again) or `failed`
    with the `error`. The `code` method carries `code` and `verification_url`; the
    `browser` method carries `url`.
    """

    login_id: str
    method: Literal["code", "browser"]
    status: Literal["pending", "confirmed", "expired", "failed"]
    # ISO 8601 UTC; after this the login cannot succeed
    expires_at: str
    code: str | None = None
    verification_url: str | None = None
    url: str | None = None
    account: AccountOut | None = None
    error: str | None = None


class LoginComplete(BaseModel):
    """
    Step two of a browser login: `response_url` is the "page not found" address the
    browser lands on after signing in.
    """

    model_config = ConfigDict(extra="forbid")

    response_url: str = Field(min_length=1)


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


class RetryUnavailable(BaseModel):
    """Queue the parked books again: one account's, or everyone's."""

    model_config = ConfigDict(extra="forbid")

    account_id: int | None = None


class Requeued(BaseModel):
    """How many books a bulk retry put back in the queue."""

    requeued: int


class BookFilter(BaseModel):
    """The library listing's filters, for a bulk action on everything they match."""

    model_config = ConfigDict(extra="forbid")

    account_id: int | None = None
    status: BookStatus | None = None
    monitored: bool | None = None
    q: Annotated[str | None, Field(max_length=200)] = None


MAX_BULK_IDS = 10_000
# SQLite's INTEGER range: a larger id is a 422 here rather than an OverflowError there
BookId = Annotated[int, Field(ge=1, le=2**63 - 1)]


class BulkRequest(BaseModel):
    """
    One action on many books: the `ids` selected, or every book `filter` matches.
    Exactly one of the two.
    """

    model_config = ConfigDict(extra="forbid")

    action: BulkAction
    ids: Annotated[list[BookId] | None, Field(max_length=MAX_BULK_IDS)] = None
    filter: BookFilter | None = None

    @model_validator(mode="after")
    def _one_selection(self) -> "BulkRequest":
        if (self.ids is None) == (self.filter is None):
            raise ValueError("give exactly one of ids or filter")
        return self


class BulkSkip(BaseModel):
    id: int
    reason: SkipReason


class BulkResultOut(BaseModel):
    """`updated` books changed, `queued` of them now wait for a download, the rest skipped."""

    updated: int
    queued: int
    skipped: list[BulkSkip]


class ApiTokenInfo(BaseModel):
    """The token as the Settings screen shows it: masked to its last four characters."""

    last4: str
    source: Literal["env", "stored"]


class NewApiToken(BaseModel):
    """A regenerated token, returned this once."""

    token: str


class DiskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    free_bytes: int | None
    total_bytes: int | None
    low_space: bool

    @field_validator("path", mode="before")
    @classmethod
    def _path_as_text(cls, value: Any) -> Any:
        return str(value) if isinstance(value, Path) else value


class SystemInfo(BaseModel):
    version: str
    ffmpeg_version: str | None
    libopus: bool
    audiobook_folder: DiskOut
    key_source: Literal["env", "file"]
    key_file: str | None
    database_bytes: int


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
    max_consecutive_failures: int | None
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

    `null` is only accepted for the three settings that can be unset (`max_download`,
    `max_consecutive_failures`, `webhook_url`). Anywhere else it would be stored as
    an empty value no later read could parse, locking the API out of its own settings.
    """

    model_config = ConfigDict(extra="forbid")

    debug: bool | None = None
    sync_enabled: bool | None = None
    sync_interval_minutes: int | None = None
    max_download: int | None = None
    max_attempts: int | None = None
    max_consecutive_failures: int | None = None
    auto_monitor_new: bool | None = None
    download_folder: str | None = Field(default=None, min_length=1)
    audiobook_folder: str | None = Field(default=None, min_length=1)
    folder_template: str | None = None
    filename_template: str | None = None
    encoding_format: str | None = None
    bitrate: int | None = None
    webhook_url: str | None = None

    @field_validator(
        "debug",
        "sync_enabled",
        "sync_interval_minutes",
        "max_attempts",
        "auto_monitor_new",
        "download_folder",
        "audiobook_folder",
        "folder_template",
        "filename_template",
        "encoding_format",
        "bitrate",
    )
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        # Runs only for a field that was sent: an omitted one keeps its default unvalidated
        if value is None:
            raise ValueError("cannot be null; omit the field to leave it unchanged")
        return value
