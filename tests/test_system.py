import subprocess
from collections import namedtuple
from pathlib import Path

import pytest

from audible_sync import system

VERSION = "ffmpeg version 7.1.1 Copyright (c) 2000-2025 the FFmpeg developers\nbuilt with gcc 14\n"
ENCODERS = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264
 A....D aac                  AAC (Advanced Audio Coding)
 A....D libopus              libopus Opus (codec opus)
"""


def _runner(version=VERSION, encoders=ENCODERS, missing=False):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if missing:
            raise FileNotFoundError("ffmpeg")
        out = version if argv[1] == "-version" else encoders
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    run.calls = calls
    return run


def test_ffmpeg_with_libopus():
    info = system.probe_ffmpeg(_runner())

    assert info == system.Ffmpeg("ffmpeg version 7.1.1 Copyright (c) 2000-2025 the FFmpeg developers", True)


def test_ffmpeg_without_libopus():
    encoders = ENCODERS.replace(" A....D libopus              libopus Opus (codec opus)\n", "")

    assert system.probe_ffmpeg(_runner(encoders=encoders)).libopus is False


def test_a_mention_of_opus_is_not_the_encoder():
    encoders = " A....D opus                 Opus (native, experimental)\n A....D aac  libopus-ish note\n"

    assert system.probe_ffmpeg(_runner(encoders=encoders)).libopus is False


def test_ffmpeg_missing_is_reported_not_raised():
    run = _runner(missing=True)

    assert system.probe_ffmpeg(run) == system.Ffmpeg(None, False)
    assert len(run.calls) == 1


def test_ffmpeg_that_fails_is_reported_not_raised():
    def run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    assert system.probe_ffmpeg(run) == system.Ffmpeg(None, False)


Usage = namedtuple("Usage", "total used free")
TB = 10**12
GB = 10**9


@pytest.mark.parametrize(
    ("free", "total", "low"),
    [
        (40 * GB, 20 * TB, True),  # 5% of 20 TB is a terabyte, but 50 GB is the smaller line
        (51 * GB, 20 * TB, False),
        (3 * GB, 64 * GB, True),  # on a small disk, 5% (3.2 GB) is the smaller line
    ],
)
def test_low_space_is_the_smaller_of_five_percent_and_fifty_gigabytes(free, total, low):
    assert system.is_low_space(free, total) is low


def test_four_gigabytes_of_sixty_four_is_not_low():
    assert system.is_low_space(4 * GB, 64 * GB) is False


def test_disk_reads_the_nearest_existing_folder(tmp_path):
    seen = []

    def usage(path):
        seen.append(path)
        return Usage(100 * GB, 10 * GB, 90 * GB)

    result = system.disk(tmp_path / "audiobooks" / "not-yet", usage)

    assert seen == [tmp_path]
    assert result == system.Disk(tmp_path / "audiobooks" / "not-yet", 90 * GB, 100 * GB, False)


def test_a_disk_that_cannot_be_read_reports_nothing(tmp_path):
    def usage(path):
        raise PermissionError("no")

    assert system.disk(tmp_path, usage) == system.Disk(tmp_path, None, None, False)


def test_database_bytes_counts_the_write_ahead_log(tmp_path):
    db = tmp_path / "a.db"
    db.write_bytes(b"x" * 100)
    Path(f"{db}-wal").write_bytes(b"x" * 20)

    assert system.database_bytes(db) == 120
    assert system.database_bytes(tmp_path / "missing.db") == 0


def test_a_disk_behind_a_folder_it_cannot_enter_reports_nothing(tmp_path):
    """Path.exists raises PermissionError there rather than answering False."""
    locked = tmp_path / "locked"
    (locked / "inner").mkdir(parents=True)
    locked.chmod(0)
    try:
        result = system.disk(locked / "inner" / "books")
    finally:
        locked.chmod(0o755)

    assert result == system.Disk(locked / "inner" / "books", None, None, False)


def test_a_found_ffmpeg_is_kept_and_a_missing_one_is_asked_again_after_a_while(monkeypatch):
    answers = [system.Ffmpeg(None, False), system.Ffmpeg("ffmpeg version 7.1", True)]
    calls = []
    now = [1000.0]
    monkeypatch.setattr(system, "_probed", None)
    monkeypatch.setattr(system, "_failed", None)
    monkeypatch.setattr(system, "probe_ffmpeg", lambda: calls.append(1) or answers[len(calls) - 1])

    def clock():
        return now[0]

    assert system.ffmpeg(clock).version is None
    # A failure is believed for a while, so the Settings screen does not run FFmpeg each load
    now[0] += system.FAILED_PROBE_TTL - 1
    assert system.ffmpeg(clock).version is None
    assert len(calls) == 1
    # ... and then asked again, so FFmpeg installed meanwhile is found
    now[0] += 2
    assert system.ffmpeg(clock).version == "ffmpeg version 7.1"
    # Found is kept for good
    now[0] += 10 * system.FAILED_PROBE_TTL
    assert system.ffmpeg(clock).version == "ffmpeg version 7.1"
    assert len(calls) == 2
