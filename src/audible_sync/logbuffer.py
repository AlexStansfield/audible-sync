"""
The last few hundred log lines, kept in memory for the API.

`docker logs` is the real log; this is the copy a UI can show without it. A ring buffer
on the root logger, so every module's lines land here in the order they happened, and
the oldest fall off rather than the process growing.
"""

import logging
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any


class RingBufferHandler(logging.Handler):
    """Keeps the newest `capacity` records as plain dicts."""

    def __init__(self, capacity: int = 1000) -> None:
        super().__init__()
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                formatter = self.formatter or logging.Formatter()
                message = f"{message}\n{formatter.formatException(record.exc_info)}"
            entry = {
                "time": datetime.fromtimestamp(record.created, UTC).replace(microsecond=0).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            }
        except Exception:
            self.handleError(record)
            return
        with self._lock:
            self._records.append(entry)

    def records(self, *, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        """The newest `limit` records at or above `level`, oldest first."""
        threshold = logging.getLevelNamesMapping().get(level.upper(), logging.NOTSET) if level else logging.NOTSET
        with self._lock:
            entries = list(self._records)
        if threshold:
            entries = [e for e in entries if logging.getLevelNamesMapping().get(e["level"], 0) >= threshold]
        return entries[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
