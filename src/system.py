"""
What the Settings screen's System card shows: FFmpeg, the library's disk, the database.

Every probe here is read-only and injectable, so the tests never run FFmpeg or depend
on the disk they run on. A probe that fails reports nothing rather than raising: the
card is information, and a missing FFmpeg is exactly what it is there to show.
"""

import functools
import logging
import shutil
import subprocess
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


@functools.cache
def ffmpeg() -> Ffmpeg:
    """`probe_ffmpeg`, once per process: FFmpeg does not change under a running service."""
    return probe_ffmpeg()


def is_low_space(free: int, total: int) -> bool:
    return free < min(total * LOW_SPACE_FRACTION, LOW_SPACE_BYTES)


def disk(path: Path, usage: Callable | None = None) -> Disk:
    """
    The space on the disk `path` is on, read from its nearest existing ancestor, so a
    library folder not created yet still reports the disk it will be created on.
    """
    usage = usage or shutil.disk_usage
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
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
