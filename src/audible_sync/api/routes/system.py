"""
What the service and its library look like: the System card, the log and the dashboard's numbers.
"""

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from audible_sync import database, system
from audible_sync.api.schemas import (
    AccountStats,
    DiskOut,
    LogList,
    Stats,
    SystemInfo,
)
from audible_sync.database import (
    downloaded_file_paths,
    get_accounts,
    get_sync_runs,
    library_stats,
)
from audible_sync.logbuffer import RingBufferHandler
from audible_sync.pipeline.scheduler import Scheduler
from audible_sync.security import crypto
from audible_sync.settings import Settings


def router(*, scheduler: Scheduler, log_buffer: RingBufferHandler | None, version: str) -> APIRouter:
    router = APIRouter()

    @router.get("/system", response_model=SystemInfo)
    def get_system() -> SystemInfo:
        """FFmpeg, the library's disk, the key and the database, for the System card."""
        ffmpeg = system.ffmpeg()
        library_disk = system.disk(Settings.from_db().audiobook_folder)
        key = crypto.key_source()
        return SystemInfo(
            version=version,
            ffmpeg_version=ffmpeg.version,
            libopus=ffmpeg.libopus,
            audiobook_folder=DiskOut.model_validate(library_disk),
            key_source=key.kind,
            key_file=str(key.path) if key.path else None,
            database_bytes=system.database_bytes(database.DB_FILE),
        )

    @router.get("/logs", response_model=LogList)
    def get_logs(
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
        level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None = None,
    ) -> LogList:
        """The newest log lines, oldest first, at or above `level`."""
        return LogList(items=log_buffer.records(limit=limit, level=level) if log_buffer else [])

    @router.get("/stats", response_model=Stats)
    def get_stats() -> Stats:
        """The numbers a dashboard opens with: per account and in total."""
        settings = Settings.from_db()
        per_account = []
        for account in get_accounts():
            last = get_sync_runs(limit=1, account_id=account.id)
            per_account.append(
                AccountStats(
                    account=account,
                    books=library_stats(account_id=account.id),
                    bytes_on_disk=_bytes_on_disk(downloaded_file_paths(account_id=account.id)),
                    last_run=last[0] if last else None,
                )
            )
        last = get_sync_runs(limit=1)
        return Stats(
            accounts=per_account,
            books=library_stats(),
            bytes_on_disk=sum(a.bytes_on_disk for a in per_account),
            last_run=last[0] if last else None,
            next_run_at=scheduler.status(settings)["next_run_at"],
        )

    return router


def _bytes_on_disk(paths: list[str]) -> int:
    """Sum of the file sizes, ignoring any file that has gone."""
    total = 0
    for path in paths:
        try:
            total += Path(path).stat().st_size
        except OSError:
            continue
    return total
