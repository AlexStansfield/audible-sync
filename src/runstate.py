"""
What the run in flight is doing right now, for anything that wants to watch it.

The pipeline logs as it goes, which is right for a terminal and useless to an API: a
poll of `/api/status` needs a value, not a scroll-back. `RunState` is that value - a
small snapshot the pipeline updates as it moves between stages, books and transfers,
read under a lock from whatever thread is serving the request. It is deliberately a
snapshot rather than an event stream, so a client that polls every second and one that
polls every minute both get the current picture with nothing to replay; a push
transport can be layered on top later without changing what the pipeline reports.

`StateProgress` is the `Progress` implementation that feeds byte progress into it, the
service's counterpart to the CLI's `TqdmProgress`.
"""

import threading
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from src.model import Book


class RunStage(StrEnum):
    """Where a run is, coarse enough to show as one word."""

    SYNCING = "syncing"
    DOWNLOADING = "downloading"
    # Set when a cancel is requested; the run keeps this until it has handed back the
    # book it was on, which is bounded by one download or one ffmpeg pass.
    CANCELLING = "cancelling"


class RunState:
    """
    Thread-safe snapshot of the current run. `snapshot()` is None between runs.

    One instance lives for the life of the service and is reused by every run; the
    pipeline calls `begin` and `end` around each one. Every setter takes the lock, so a
    reader never sees a book from one run against the counters of another.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self._run_id: int | None = None
        self._started_at: str | None = None
        self._stage: RunStage | None = None
        self._account: dict[str, Any] | None = None
        self._book: dict[str, Any] | None = None
        self._books_done = 0
        self._books_total = 0
        self._transfer: dict[str, Any] | None = None

    def begin(self, run_id: int) -> None:
        """Start reporting a run. Clears anything a previous run left behind."""
        with self._lock:
            self._reset()
            self._run_id = run_id
            self._started_at = datetime.now(UTC).replace(microsecond=0).isoformat()

    def end(self) -> None:
        """Stop reporting; `snapshot()` is None again."""
        with self._lock:
            self._reset()

    def set_stage(self, stage: RunStage) -> None:
        with self._lock:
            self._stage = stage

    def set_account(self, account: dict[str, Any] | None) -> None:
        """Which account the run is working through. Unused until there are several."""
        with self._lock:
            self._account = account

    def set_book(self, book: Book | None, *, done: int, total: int) -> None:
        """
        The book being processed and how far through the queue the run is.

        `done` is how many books the run has finished with, successfully or not, and
        `total` how many it took on; `book` is None once the loop is over.
        """
        with self._lock:
            self._book = None if book is None else {"asin": book.asin, "title": book.title}
            self._books_done = done
            self._books_total = total
            # A new book means the previous transfer, if any, is over
            self._transfer = None

    def transfer_started(self, desc: str | None, total: int | None) -> None:
        with self._lock:
            self._transfer = {"desc": desc, "bytes": 0, "total": total}

    def transfer_advanced(self, amount: int) -> None:
        with self._lock:
            if self._transfer is not None:
                self._transfer["bytes"] += amount

    def transfer_finished(self) -> None:
        with self._lock:
            self._transfer = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._run_id is not None

    def snapshot(self) -> dict[str, Any] | None:
        """A copy of the current picture, or None when no run is in flight."""
        with self._lock:
            if self._run_id is None:
                return None
            return {
                "run_id": self._run_id,
                "started_at": self._started_at,
                "stage": self._stage,
                "account": dict(self._account) if self._account else None,
                "book": dict(self._book) if self._book else None,
                "books_done": self._books_done,
                "books_total": self._books_total,
                "transfer": dict(self._transfer) if self._transfer else None,
            }


class StateProgress:
    """
    The service's `Progress`: byte progress goes into a `RunState` instead of a bar.

    Same lifecycle as `TqdmProgress` - one transfer at a time, `start` replacing
    anything left open - so `_stream_to_file` cannot tell the two apart.
    """

    def __init__(self, state: RunState) -> None:
        self._state = state

    def start(self, desc: str | None, total: int | None) -> None:
        self._state.transfer_started(desc, total)

    def advance(self, amount: int) -> None:
        self._state.transfer_advanced(amount)

    def finish(self) -> None:
        self._state.transfer_finished()
