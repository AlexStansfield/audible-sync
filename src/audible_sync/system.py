"""
What the Settings screen's System card shows: FFmpeg, the library's disk, the database.

Every probe here is read-only and injectable, so the tests never run FFmpeg or depend
on the disk they run on. A probe that fails reports nothing rather than raising: the
card is information, and a missing FFmpeg is exactly what it is there to show.
"""

import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Low space is under 5% of the disk or under 50 GB free, whichever is smaller: 5% of a
# 20 TB array is a terabyte, which is no emergency, and 50 GB of a 64 GB card is most of it
LOW_SPACE_FRACTION = 0.05
LOW_SPACE_BYTES = 50 * 10**9

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class Ffmpeg:
    """`version` is the first line of `ffmpeg -version`, None when FFmpeg is missing."""

    version: str | None
    libopus: bool


@dataclass(frozen=True)
class Disk:
    """Space on the disk holding `path`; the sizes are None when it cannot be read."""

    path: Path
    free_bytes: int | None
    total_bytes: int | None
    low_space: bool


def probe_ffmpeg(run: Runner = subprocess.run) -> Ffmpeg:
    """Ask FFmpeg its version and whether it has the libopus encoder the Opus format needs."""
    try:
        version = run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("FFmpeg could not be run: %s", error)
        return Ffmpeg(version=None, libopus=False)
    first_line = next(iter(version.stdout.splitlines()), "").strip() or None
    try:
        encoders = run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("FFmpeg's encoders could not be listed: %s", error)
        return Ffmpeg(version=first_line, libopus=False)
    # A line per encoder: " A....D libopus              libopus Opus"
    libopus = any(line.split()[1:2] == ["libopus"] for line in encoders.stdout.splitlines())
    return Ffmpeg(version=first_line, libopus=libopus)


# How long a failed probe is believed: long enough that loading the Settings screen
# does not run FFmpeg (and log a WARNING) every time, short enough that installing it
# shows without a restart
FAILED_PROBE_TTL = 300.0

_probed: Ffmpeg | None = None
_failed: tuple[float, Ffmpeg] | None = None


def ffmpeg(clock: Callable[[], float] = time.monotonic) -> Ffmpeg:
    """
    `probe_ffmpeg`, kept for good once it has found FFmpeg - a working FFmpeg does not
    change under a running service - and for `FAILED_PROBE_TTL` when it has not.
    """
    global _probed, _failed
    if _probed is not None:
        return _probed
    now = clock()
    if _failed is not None and now - _failed[0] < FAILED_PROBE_TTL:
        return _failed[1]
    found = probe_ffmpeg()
    if found.version is not None:
        _probed, _failed = found, None
    else:
        _failed = (now, found)
    return found


def is_low_space(free: int, total: int) -> bool:
    """
    Whether `free` is under 5% of `total` or under 50 GB, whichever is the smaller line:
    5% of a 20 TB array is no emergency, while 50 GB is most of a small disk.
    """
    return free < min(total * LOW_SPACE_FRACTION, LOW_SPACE_BYTES)


def disk(path: Path, usage: Callable | None = None) -> Disk:
    """
    The space on the disk `path` is on, read from its nearest existing ancestor, so a
    library folder not created yet still reports the disk it will be created on.
    """
    usage = usage or shutil.disk_usage
    probe = Path(path)
    try:
        # Inside the try: exists() raises PermissionError for a parent it cannot enter
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        result = usage(probe)
    except OSError as error:
        logger.warning("Could not read the free space of %s: %s", path, error)
        return Disk(path=Path(path), free_bytes=None, total_bytes=None, low_space=False)
    return Disk(
        path=Path(path),
        free_bytes=result.free,
        total_bytes=result.total,
        low_space=is_low_space(result.free, result.total),
    )


def database_bytes(db_file: str | Path) -> int:
    """The database on disk: the file and its write-ahead log, which WAL mode keeps beside it."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += Path(f"{db_file}{suffix}").stat().st_size
        except OSError:
            continue
    return total
