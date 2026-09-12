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

from src.model import SyncOutcome
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


class Status(BaseModel):
    scheduler: SchedulerStatus
    current_run: RunSnapshot | None
    last_run: SyncRunOut | None


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
