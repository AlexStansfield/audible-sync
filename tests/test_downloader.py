import base64
import json
import logging
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from audible.exceptions import NotFoundError

import src.downloader as downloader
from src.downloader import (
    DownloadedBook,
    DownloadStats,
    LicenseError,
    _stream_to_file,
    decrypt_aaxc,
    flatten_chapters,
    generate_metadata,
    sanitize_filename,
    temp_book_folder,
    write_ffmpeg_metadata_file,
)
from src.encoding import output_extension
from src.model import BookStatus
from src.runstate import RunState
from tests.conftest import make_book, make_settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Plain Title", "Plain Title"),
        ("Northern Lights: His Dark Materials, Book 1", "Northern Lights - His Dark Materials, Book 1"),
        ("Good Omens / The Nice Bit", "Good Omens - The Nice Bit"),
        ("Back\\Slash", "Back-Slash"),
        ('What "Is"? <Bad>|*Name', "What Is BadName"),
        ("   spaced   out...  ", "spaced out"),
        ("Tabs\tand\nnewlines", "Tabs and newlines"),
    ],
)
def test_sanitize_filename_replaces_unsafe_characters(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_uses_fallback_for_empty_or_none():
    assert sanitize_filename("") == "Unknown"
    assert sanitize_filename(None) == "Unknown"
    assert sanitize_filename("???", fallback="B000ASIN") == "B000ASIN"


def test_sanitize_filename_truncates_long_names():
    result = sanitize_filename("x" * 300)
    assert len(result) == 150


def test_sanitize_filename_accepts_non_strings():
    assert sanitize_filename(3) == "3"


def test_temp_book_folder_is_under_download_folder_and_sanitized():
    folder = temp_book_folder("data/downloads", "B001", "Title: Sub/Title")
    assert folder == Path("data/downloads") / "B001_Title - Sub-Title"


def test_generate_metadata_maps_fields():
    book = make_book(
        title="Title",
        subtitle="Sub",
        authors=("A", "B"),
        narrators=("N",),
        series=[{"title": "Series", "sequence": "2"}],
        genres=("G1", "G2"),
    )
    meta = generate_metadata(book)
    assert meta["title"] == "Title: Sub"
    assert meta["album"] == "Title: Sub"
    assert meta["artist"] == "A; B"
    assert meta["composer"] == "N"
    assert meta["series"] == "Series"
    assert meta["series-part"] == "2"
    assert meta["genre"] == "G1; G2"
    assert meta["date"] == "2020"
    assert meta["comment"] == "ASIN: B001"


def test_generate_metadata_tags_the_primary_series_not_the_first_one():
    book = make_book(
        series=[
            {"title": "The Dune Sequence", "sequence": "12"},
            {"title": "Dune", "sequence": "1"},
        ]
    )
    meta = generate_metadata(book)

    assert meta["series"] == "Dune"
    assert meta["series-part"] == "1"


def test_generate_metadata_leaves_a_null_sequence_empty_rather_than_none():
    """
    `_prepare_book` always creates the `sequence` key, so a `.get(..., "")` default
    never fired and a null sequence reached the metadata as `None`.
    """
    meta = generate_metadata(make_book(series=[{"title": "Companion", "sequence": None}]))

    assert meta["series"] == "Companion"
    assert meta["series-part"] == ""


def test_a_null_sequence_does_not_reach_the_ffmetadata_file_as_the_word_none(tmp_path):
    """
    Where the bug was actually visible: `_escape_ffmetadata` does `str(value)`, so a
    `None` was written as a literal `series-part=None` line and FFmpeg copied it into
    the output, showing "None" as the series number in a player.
    """
    path = tmp_path / "meta.ffmetadata"
    write_ffmpeg_metadata_file(generate_metadata(make_book(series=[{"title": "S", "sequence": None}])), path)

    contents = path.read_text()
    assert "series-part=\n" in contents
    assert "None" not in contents


def test_generate_metadata_handles_missing_optional_fields():
    book = make_book(authors=(), narrators=(), genres=(), release_date=None)
    meta = generate_metadata(book)
    assert meta["title"] == "Title"
    assert "artist" not in meta
    assert "composer" not in meta
    assert "genre" not in meta
    assert "date" not in meta


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """Replace subprocess.run with a recorder that captures argv and the FFMETADATA file before it is deleted."""
    calls = SimpleNamespace(cmd=None, metadata=None, returncode=0, extra_tags=None)

    def run(cmd, **kwargs):
        calls.cmd = list(cmd)
        for arg in cmd:
            if str(arg).endswith(".ffmetadata"):
                calls.metadata = Path(arg).read_text(encoding="utf-8")
        return SimpleNamespace(returncode=calls.returncode, stderr="boom")

    def write_m4b_extra_tags(path, metadata):
        # The fake ffmpeg writes no file, so record what would have been tagged instead of opening it
        calls.extra_tags = (str(path), dict(metadata))
        return ["series", "series-part"]

    monkeypatch.setattr(downloader.subprocess, "run", run)
    monkeypatch.setattr(downloader, "write_m4b_extra_tags", write_m4b_extra_tags)
    return calls


@pytest.fixture
def aaxc(tmp_path):
    """A fake AAXC, voucher and cover in a temp folder."""
    book = tmp_path / "book.aaxc"
    book.write_bytes(b"aaxc")
    voucher = tmp_path / "book.json"
    voucher.write_text(json.dumps({"key": "KEY", "iv": "IV"}))
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8jpeg")
    return SimpleNamespace(book=str(book), voucher=str(voucher), cover=str(cover))


CHAPTERS = [
    {"start_offset_ms": 0, "length_ms": 1000, "title": "Intro"},
    {"start_offset_ms": 1000, "length_ms": 500, "title": "Part 1"},
]


def test_decrypt_aaxc_m4b_command_keeps_all_metadata_tags(fake_ffmpeg, aaxc):
    book = make_book(series=[{"title": "Series", "sequence": "2"}])
    out = decrypt_aaxc(aaxc.book, aaxc.voucher, book_data=book, cover_path=aaxc.cover, chapters=CHAPTERS)

    assert out == f"{aaxc.book}.m4b"
    metadata_file = f"{aaxc.book}.ffmetadata"
    assert fake_ffmpeg.cmd == [
        "ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error",
        "-audible_key", "KEY", "-audible_iv", "IV", "-i", aaxc.book,
        "-i", aaxc.cover,
        "-i", metadata_file,
        "-map", "0:a",
        "-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic",
        "-map_metadata", "2", "-map_chapters", "2",
        "-c:a", "copy", "-dn",
        out,
    ]  # fmt: skip
    assert "series=Series\nseries-part=2\n" in fake_ffmpeg.metadata
    tagged_path, tagged = fake_ffmpeg.extra_tags
    assert tagged_path == out
    assert tagged["series"] == "Series"
    assert tagged["series-part"] == "2"
    assert fake_ffmpeg.metadata.count("[CHAPTER]") == 2
    assert "CHAPTER000" not in fake_ffmpeg.metadata
    assert "METADATA_BLOCK_PICTURE" not in fake_ffmpeg.metadata
    assert not Path(metadata_file).exists()


def test_decrypt_aaxc_oga_command_and_metadata(fake_ffmpeg, aaxc):
    out = decrypt_aaxc(
        aaxc.book,
        aaxc.voucher,
        book_data=make_book(),
        cover_path=aaxc.cover,
        chapters=CHAPTERS,
        encoding_format="oga",
        bitrate=96,
    )

    assert out == f"{aaxc.book}.oga"
    metadata_file = f"{aaxc.book}.ffmetadata"
    assert fake_ffmpeg.cmd == [
        "ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error",
        "-audible_key", "KEY", "-audible_iv", "IV", "-i", aaxc.book,
        "-i", metadata_file,
        "-map", "0:a",
        "-map_metadata", "1",
        "-map_chapters", "-1",
        "-c:a", "libopus", "-b:a", "96k", "-vbr", "on", "-dn",
        out,
    ]  # fmt: skip
    assert aaxc.cover not in fake_ffmpeg.cmd

    meta = fake_ffmpeg.metadata
    assert "[CHAPTER]" not in meta
    assert "title=Title\n" in meta
    assert "CHAPTER000=00:00:00.000\n" in meta
    assert "CHAPTER000NAME=Intro\n" in meta
    assert "CHAPTER001=00:00:01.000\n" in meta
    assert "CHAPTER001NAME=Part 1\n" in meta

    picture_line = next(line for line in meta.splitlines() if line.startswith("METADATA_BLOCK_PICTURE="))
    encoded = picture_line.split("=", 1)[1].replace("\\=", "=")
    block = base64.b64decode(encoded)
    assert block.startswith(b"\x00\x00\x00\x03\x00\x00\x00\x0aimage/jpeg")
    assert block.endswith(b"\xff\xd8jpeg")
    assert not Path(metadata_file).exists()


def test_decrypt_aaxc_oga_without_cover_or_chapters(fake_ffmpeg, aaxc):
    decrypt_aaxc(aaxc.book, aaxc.voucher, book_data=make_book(), encoding_format="oga")

    assert "METADATA_BLOCK_PICTURE" not in fake_ffmpeg.metadata
    assert "CHAPTER000" not in fake_ffmpeg.metadata
    assert "title=Title\n" in fake_ffmpeg.metadata
    assert "-map_metadata" in fake_ffmpeg.cmd
    assert fake_ffmpeg.cmd[-1].endswith(".oga")


def test_decrypt_aaxc_oga_without_any_metadata_skips_metadata_input(fake_ffmpeg, aaxc):
    decrypt_aaxc(aaxc.book, aaxc.voucher, encoding_format="oga")

    assert fake_ffmpeg.metadata is None
    assert "-map_metadata" not in fake_ffmpeg.cmd
    assert fake_ffmpeg.cmd[-1] == f"{aaxc.book}.oga"


def test_decrypt_aaxc_uses_default_bitrate(fake_ffmpeg, aaxc):
    decrypt_aaxc(aaxc.book, aaxc.voucher, encoding_format="oga")
    assert fake_ffmpeg.cmd[fake_ffmpeg.cmd.index("-b:a") + 1] == "64k"


def test_decrypt_aaxc_raises_on_ffmpeg_failure(fake_ffmpeg, aaxc):
    fake_ffmpeg.returncode = 1
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        decrypt_aaxc(aaxc.book, aaxc.voucher, book_data=make_book())
    assert excinfo.value.stderr == "boom"
    assert fake_ffmpeg.extra_tags is None


def test_decrypt_aaxc_oga_does_not_write_mp4_tags(fake_ffmpeg, aaxc):
    decrypt_aaxc(aaxc.book, aaxc.voucher, book_data=make_book(), encoding_format="oga")
    assert fake_ffmpeg.extra_tags is None


def test_decrypt_aaxc_m4b_without_book_data_does_not_write_mp4_tags(fake_ffmpeg, aaxc):
    decrypt_aaxc(aaxc.book, aaxc.voucher)
    assert fake_ffmpeg.extra_tags is None


def test_decrypt_aaxc_rejects_unknown_format(fake_ffmpeg, aaxc):
    with pytest.raises(ValueError, match="not supported"):
        decrypt_aaxc(aaxc.book, aaxc.voucher, encoding_format="mp3")
    assert fake_ffmpeg.cmd is None


def _library_books():
    return [
        make_book("BAD1", "Broken: Book", series=[{"title": "S/eries", "sequence": "1"}]),
        make_book(
            "OK2",
            "Northern Lights: Book 1",
            authors=("Philip Pullman",),
            series=[{"title": "His Dark Materials", "sequence": "1"}],
        ),
        make_book("OK3", "No Series / No Author", authors=()),
    ]


def _asins(books):
    """
    Row id to ASIN, for fakes standing in for the database.

    The state machine is keyed by row id, but a test reads better asserting on ASINs,
    so the fakes translate back on the way in.
    """
    return {book.id: book.asin for book in books}


def _patch_pipeline(monkeypatch, books, marked, accessories, decrypt_calls, *, claimed=None, failures=None):
    """
    Stub everything that would hit the network, ffmpeg or the database.

    The database functions are patched as attributes of `downloader`, which is where
    `download_books` looks them up; miss one and the test writes to the real library.
    """
    asins = _asins(books)
    monkeypatch.setattr(downloader, "get_books_to_download", lambda **kw: books)

    def fake_claim(book_id, **kwargs):
        if claimed is not None:
            claimed.append(asins[book_id])
        return True

    def fake_fail(book_id, error, *, max_attempts, terminal=False):
        if failures is not None:
            failures.append((asins[book_id], error, terminal, max_attempts))
        return BookStatus.FAILED if terminal else BookStatus.WAITING_DOWNLOAD

    monkeypatch.setattr(downloader, "claim_book_for_download", fake_claim)
    monkeypatch.setattr(downloader, "mark_book_failed", fake_fail)
    monkeypatch.setattr(downloader, "release_book", lambda book_id: None)
    monkeypatch.setattr(downloader, "mark_book_unavailable", lambda book_id, error: None)

    def fake_mark(book_id, **kw):
        marked.append((asins[book_id], kw["encoding_format"]))
        accessories.append({k: v for k, v in kw.items() if k.endswith("_path") and k != "file_path"})

    monkeypatch.setattr(downloader, "mark_book_downloaded", fake_mark)

    def fake_download_book(self, book, temp_dir):
        temp_dir.mkdir(parents=True, exist_ok=True)
        if book.asin == "BAD1":
            (temp_dir / "partial.aaxc").write_bytes(b"junk")
            raise RuntimeError("simulated network failure")
        aaxc = temp_dir / "book.aaxc"
        aaxc.write_bytes(b"aaxc")
        voucher = aaxc.with_suffix(".json")
        voucher.write_text("{}")
        return DownloadedBook(aaxc=aaxc, voucher=voucher, chapters=None)

    def fake_decrypt(book, voucher, **kwargs):
        decrypt_calls.append(kwargs)
        out = f"{book}{output_extension(kwargs.get('encoding_format', 'm4b'))}"
        Path(out).write_bytes(b"audio")
        return out

    monkeypatch.setattr(downloader.Downloader, "download_book", fake_download_book)
    monkeypatch.setattr(downloader.Downloader, "download_annotations", lambda self, asin, out: False)
    monkeypatch.setattr(downloader, "decrypt_aaxc", fake_decrypt)


def test_download_books_skips_failed_book_and_cleans_temp(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    library = tmp_path / "audiobooks"
    downloads.mkdir()
    marked, accessories, decrypt_calls = [], [], []
    _patch_pipeline(monkeypatch, _library_books(), marked, accessories, decrypt_calls)

    downloader.download_books(object(), make_settings(download_folder=downloads, audiobook_folder=library))

    assert marked == [("OK2", "m4b"), ("OK3", "m4b")]
    assert accessories == [{}, {}]
    assert list(downloads.iterdir()) == []

    final = sorted(str(p.relative_to(library)) for p in library.rglob("*.m4b"))
    assert final == [
        "Philip Pullman/His Dark Materials/1 - Northern Lights - Book 1/Northern Lights - Book 1.m4b",
        "Unknown Author/No Series - No Author/No Series - No Author.m4b",
    ]


def test_download_books_oga_files_with_oga_extension(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    library = tmp_path / "audiobooks"
    downloads.mkdir()
    marked, accessories, decrypt_calls = [], [], []
    _patch_pipeline(monkeypatch, _library_books(), marked, accessories, decrypt_calls)

    settings = make_settings(download_folder=downloads, audiobook_folder=library, encoding_format="oga", bitrate=48)
    downloader.download_books(object(), settings)

    assert marked == [("OK2", "oga"), ("OK3", "oga")]
    assert all(call["encoding_format"] == "oga" and call["bitrate"] == 48 for call in decrypt_calls)
    assert list(library.rglob("*.m4b")) == []
    final = sorted(str(p.relative_to(library)) for p in library.rglob("*.oga"))
    assert final == [
        "Philip Pullman/His Dark Materials/1 - Northern Lights - Book 1/Northern Lights - Book 1.oga",
        "Unknown Author/No Series - No Author/No Series - No Author.oga",
    ]


def test_download_books_reports_license_failure_and_keeps_going(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    books = [make_book("NOLIC", "Unlicensed")]
    parked = []
    monkeypatch.setattr(downloader, "get_books_to_download", lambda **kw: books)
    monkeypatch.setattr(downloader, "mark_book_downloaded", lambda asin, **kw: pytest.fail("should not be marked"))
    monkeypatch.setattr(downloader, "claim_book_for_download", lambda asin, **kw: True)
    monkeypatch.setattr(
        downloader, "mark_book_failed", lambda *a, **kw: pytest.fail("a withdrawn title is not a failure")
    )
    asins = _asins(books)
    monkeypatch.setattr(
        downloader, "mark_book_unavailable", lambda book_id, error: parked.append((asins[book_id], error))
    )

    def refuse(self, book, temp_dir):
        raise LicenseError("Audible did not grant a license for NOLIC (status Denied): not in catalogue")

    monkeypatch.setattr(downloader.Downloader, "download_book", refuse)

    downloader.download_books(
        object(), make_settings(download_folder=tmp_path / "dl", audiobook_folder=tmp_path / "lib")
    )

    assert "will not license" in caplog.text
    assert "not in catalogue" in caplog.text
    # Parked, not failed: Audible offers withdrawn Plus titles again, and the run is clean
    assert "0 succeeded, 0 failed" in caplog.text
    assert "1 books are not currently available" in caplog.text
    asin, error = parked[0]
    assert asin == "NOLIC"
    assert "not in catalogue" in error


def test_write_ffmpeg_metadata_file_escapes_newlines_the_way_ffmpeg_reads_them(tmp_path):
    out = tmp_path / "meta.ffmetadata"
    write_ffmpeg_metadata_file(
        {"title": "Part One\nThe Beginning"},
        str(out),
        chapters=[{"start_offset_ms": 0, "length_ms": 10, "title": "One\r\nTwo"}],
    )
    text = out.read_text(encoding="utf-8")

    # FFmpeg escapes a newline as a backslash followed by the real newline; the two
    # characters "\\" and "n" would be read back as a literal "n".
    assert "title=Part One\\\nThe Beginning\n" in text
    assert "title=One\\\nTwo\n" in text
    assert "\\n" not in text.replace("\\\n", "")
    assert "\r" not in text


def test_flatten_chapters_descends_into_parts():
    tree = [
        {"title": "Part One", "start_offset_ms": 0, "chapters": [{"title": "Ch 1"}, {"title": "Ch 2"}]},
        {"title": "Part Two", "start_offset_ms": 100, "chapters": [{"title": "Ch 3"}]},
    ]
    assert [c["title"] for c in flatten_chapters(tree)] == ["Ch 1", "Ch 2", "Ch 3"]


def test_flatten_chapters_leaves_a_flat_list_alone():
    flat = [{"title": "Ch 1"}, {"title": "Ch 2"}]
    assert flatten_chapters(flat) == flat
    assert flatten_chapters(None) == []


def test_m4b_without_chapters_lets_ffmpeg_keep_the_aaxc_chapter_track(fake_ffmpeg, aaxc):
    decrypt_aaxc(aaxc.book, aaxc.voucher, book_data=make_book())

    # Pointing -map_chapters at a chapterless metadata file would throw away the
    # chapters the AAXC itself carries.
    assert "-map_metadata" in fake_ffmpeg.cmd
    assert "-map_chapters" not in fake_ffmpeg.cmd


def test_m4b_with_chapters_maps_them_from_the_metadata_file(fake_ffmpeg, aaxc):
    decrypt_aaxc(aaxc.book, aaxc.voucher, book_data=make_book(), chapters=CHAPTERS)

    idx = fake_ffmpeg.cmd.index("-map_chapters") + 1
    assert fake_ffmpeg.cmd[idx] == "1"
    assert fake_ffmpeg.metadata.count("[CHAPTER]") == 2


def test_license_response_rejects_a_denied_license():
    class FakeClient:
        def post(self, path, body):
            return {"content_license": {"status_code": "Denied", "message": "Not in catalogue"}}

    downloader_ = downloader.Downloader(SimpleNamespace(client=FakeClient()))

    with pytest.raises(LicenseError, match="Not in catalogue"):
        downloader_.get_license_response("B001", quality="High")


def test_license_response_returns_a_granted_license():
    granted = {"content_license": {"status_code": "Granted", "content_metadata": {}}}

    class FakeClient:
        def post(self, path, body):
            return granted

    downloader_ = downloader.Downloader(SimpleNamespace(client=FakeClient()))
    assert downloader_.get_license_response("B001", quality="High") == granted


def _annotations_downloader(response):
    """A Downloader whose sidecar call returns `response`, or raises it if it is an exception."""

    class FakeClient:
        def get(self, url, params=None):
            if isinstance(response, Exception):
                raise response
            return response

    return downloader.Downloader(SimpleNamespace(client=FakeClient()))


def test_download_annotations_treats_a_404_as_no_annotations(tmp_path):
    """
    The sidecar 404s for a book that was never opened. Letting that propagate left
    the book waiting_download forever, re-downloading the whole AAXC every run.
    """
    out = tmp_path / "a.json"
    downloader_ = _annotations_downloader(NotFoundError(httpx.Response(404), {"message": "Not Found"}))

    assert downloader_.download_annotations("B001", str(out)) is False
    assert not out.exists()


def test_download_annotations_still_raises_other_errors(tmp_path):
    """Anything but a 404 must raise, so the book is retried rather than filed without them."""
    out = tmp_path / "a.json"
    downloader_ = _annotations_downloader(httpx.ReadTimeout("boom"))

    with pytest.raises(httpx.ReadTimeout):
        downloader_.download_annotations("B001", str(out))
    assert not out.exists()


def test_download_annotations_returns_false_when_there_are_none(tmp_path):
    out = tmp_path / "a.json"
    downloader_ = _annotations_downloader({"clips": [], "bookmarks": []})

    assert downloader_.download_annotations("B001", str(out)) is False
    assert not out.exists()


def test_download_annotations_writes_the_file_when_clips_exist(tmp_path):
    out = tmp_path / "a.json"
    downloader_ = _annotations_downloader({"clips": [{"start": 1}], "bookmarks": []})

    assert downloader_.download_annotations("B001", str(out)) is True
    assert json.loads(out.read_text())["clips"] == [{"start": 1}]


def test_resolve_output_path_keeps_a_different_book_from_being_overwritten(tmp_path, monkeypatch):
    existing = tmp_path / "Red Rising.m4b"
    existing.write_bytes(b"first book")
    monkeypatch.setattr(downloader, "read_embedded_asin", lambda path: "OTHER")

    path, stem = downloader._resolve_output_path(tmp_path, "Red Rising", ".m4b", "MINE")

    assert path == tmp_path / "Red Rising [MINE].m4b"
    assert stem == "Red Rising [MINE]"
    assert existing.read_bytes() == b"first book"


def test_resolve_output_path_reuses_the_name_when_the_file_is_this_book(tmp_path, monkeypatch):
    (tmp_path / "Red Rising.m4b").write_bytes(b"mine")
    monkeypatch.setattr(downloader, "read_embedded_asin", lambda path: "MINE")

    path, stem = downloader._resolve_output_path(tmp_path, "Red Rising", ".m4b", "MINE")

    assert path == tmp_path / "Red Rising.m4b"
    assert stem == "Red Rising"


def test_resolve_output_path_uses_the_plain_name_when_nothing_is_there(tmp_path):
    path, stem = downloader._resolve_output_path(tmp_path, "Red Rising", ".m4b", "MINE")
    assert path == tmp_path / "Red Rising.m4b"
    assert stem == "Red Rising"


def test_download_books_files_colliding_books_side_by_side(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    library = tmp_path / "audiobooks"
    downloads.mkdir()
    marked, accessories, decrypt_calls = [], [], []
    books = [
        make_book("ASIN1", "Red Rising", authors=("Pierce Brown",)),
        make_book("ASIN2", "Red Rising", authors=("Pierce Brown",)),
    ]
    _patch_pipeline(monkeypatch, books, marked, accessories, decrypt_calls)
    # The stub decrypt writes no tags, so neither file can claim an ASIN
    monkeypatch.setattr(downloader, "read_embedded_asin", lambda path: None)

    downloader.download_books(object(), make_settings(download_folder=downloads, audiobook_folder=library))

    final = sorted(p.name for p in library.rglob("*.m4b"))
    assert final == ["Red Rising [ASIN2].m4b", "Red Rising.m4b"]
    assert marked == [("ASIN1", "m4b"), ("ASIN2", "m4b")]


def test_download_books_stops_on_an_authentication_failure(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    books = [make_book("A1", "First"), make_book("A2", "Second")]
    released = []
    monkeypatch.setattr(downloader, "get_books_to_download", lambda **kw: books)
    monkeypatch.setattr(downloader, "mark_book_downloaded", lambda asin, **kw: pytest.fail("should not be marked"))
    monkeypatch.setattr(downloader, "claim_book_for_download", lambda asin, **kw: True)
    monkeypatch.setattr(
        downloader, "mark_book_failed", lambda *a, **kw: pytest.fail("credentials are not the book's fault")
    )
    asins = _asins(books)
    monkeypatch.setattr(downloader, "release_book", lambda book_id: released.append(asins[book_id]))

    attempts = []

    def reject(self, book, temp_dir):
        attempts.append(book.asin)
        raise downloader.Unauthorized(httpx.Response(401), {"message": "token expired"})

    monkeypatch.setattr(downloader.Downloader, "download_book", reject)

    downloader.download_books(
        object(), make_settings(download_folder=tmp_path / "dl", audiobook_folder=tmp_path / "lib")
    )

    # Every remaining book would fail the same way, so the run stops after the first
    assert attempts == ["A1"]
    assert "rejected our credentials" in caplog.text
    # The claim goes back untouched: an expiring token must not fail a good book
    assert released == ["A1"]


def test_download_books_removes_the_encrypted_source_after_decrypting(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    library = tmp_path / "audiobooks"
    downloads.mkdir()
    marked, accessories, decrypt_calls = [], [], []
    leftovers = []
    _patch_pipeline(monkeypatch, [make_book("OK1", "Book")], marked, accessories, decrypt_calls)

    real_move = downloader.shutil.move

    def spy_move(src, dst):
        leftovers.append(sorted(p.name for p in Path(src).parent.iterdir()))
        return real_move(src, dst)

    monkeypatch.setattr(downloader.shutil, "move", spy_move)
    downloader.download_books(object(), make_settings(download_folder=downloads, audiobook_folder=library))

    # By the time the finished book is filed, the AAXC and voucher are already gone
    assert leftovers[0] == ["book.aaxc.m4b"]


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"\x00\x00\x00\x10\x00\x00\x00\x10\x08\x06"
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF"


class StubAccessoryDownloader:
    """A Downloader whose accessory fetches write fixed bytes instead of hitting the network."""

    def __init__(self, cover_bytes=JPEG_BYTES, has_annotations=False):
        self.cover_bytes = cover_bytes
        self.has_annotations = has_annotations

    def download_pdf(self, asin, output_path):
        Path(output_path).write_bytes(b"%PDF-1.4")
        return True

    def download_cover(self, cover_url, output_path):
        Path(output_path).write_bytes(self.cover_bytes)
        return True

    def download_annotations(self, asin, output_path):
        if not self.has_annotations:
            return False
        Path(output_path).write_text("{}")
        return True


@pytest.mark.parametrize(
    ("cover_bytes", "expected_suffix"),
    [(JPEG_BYTES, ".jpg"), (PNG_BYTES, ".png")],
)
def test_download_accessories_names_the_cover_from_its_bytes(tmp_path, cover_bytes, expected_suffix):
    """The URL is not reliable: plenty of cover URLs carry no extension at all."""
    book = make_book("B001", "Book", cover_url="https://img/cover-with-no-extension")
    stub = StubAccessoryDownloader(cover_bytes=cover_bytes)

    accessories = downloader._download_accessories(stub, book, tmp_path, "Book")

    assert accessories["cover_path"].suffix == expected_suffix
    assert accessories["cover_path"].read_bytes() == cover_bytes


def test_download_accessories_skips_a_pdf_the_book_does_not_have(tmp_path):
    book = make_book("B001", "Book", cover_url="")
    accessories = downloader._download_accessories(StubAccessoryDownloader(), book, tmp_path, "Book")

    assert accessories == {}


def test_download_accessories_collects_everything_available(tmp_path):
    book = make_book("B001", "Book", cover_url="https://img/c.jpg", has_pdf=True)
    stub = StubAccessoryDownloader(has_annotations=True)

    accessories = downloader._download_accessories(stub, book, tmp_path, "Book")

    assert set(accessories) == {"pdf_path", "cover_path", "annotations_path"}


def test_download_books_claims_each_book_before_downloading_it(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    claimed, marked, accessories, decrypt_calls = [], [], [], []
    _patch_pipeline(monkeypatch, _library_books(), marked, accessories, decrypt_calls, claimed=claimed)

    downloader.download_books(
        object(), make_settings(download_folder=downloads, audiobook_folder=tmp_path / "audiobooks")
    )

    assert claimed == ["BAD1", "OK2", "OK3"]


def test_download_books_skips_a_book_another_run_is_already_downloading(tmp_path, monkeypatch, caplog):
    """The claim is what stops a scheduler tick picking up an in-flight book."""
    caplog.set_level(logging.INFO)
    downloads = tmp_path / "downloads"
    library = tmp_path / "audiobooks"
    downloads.mkdir()
    marked, accessories, decrypt_calls, failures = [], [], [], []
    books = [make_book("BUSY", "Taken"), make_book("OK2", "Mine")]
    asins = _asins(books)
    _patch_pipeline(monkeypatch, books, marked, accessories, decrypt_calls, failures=failures)
    monkeypatch.setattr(downloader, "claim_book_for_download", lambda book_id, **kw: asins[book_id] != "BUSY")

    downloader.download_books(object(), make_settings(download_folder=downloads, audiobook_folder=library))

    # Not downloaded, but not counted as a failure either: somebody else has it
    assert marked == [("OK2", "m4b")]
    assert failures == []
    assert "another run is already downloading it" in caplog.text
    assert "1 succeeded, 0 failed" in caplog.text


def test_download_books_records_a_retryable_failure_against_the_book(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    marked, accessories, decrypt_calls, failures = [], [], [], []
    _patch_pipeline(monkeypatch, _library_books(), marked, accessories, decrypt_calls, failures=failures)

    settings = make_settings(download_folder=downloads, audiobook_folder=tmp_path / "audiobooks", max_attempts=5)
    downloader.download_books(object(), settings)

    assert len(failures) == 1
    asin, error, terminal, max_attempts = failures[0]
    assert asin == "BAD1"
    assert terminal is False
    # The cap comes from the settings, and the message says what actually went wrong
    assert max_attempts == 5
    assert error == "RuntimeError: simulated network failure"


def test_download_books_logs_when_it_gives_up_on_a_book(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    marked, accessories, decrypt_calls = [], [], []
    _patch_pipeline(monkeypatch, _library_books(), marked, accessories, decrypt_calls)
    monkeypatch.setattr(downloader, "mark_book_failed", lambda book_id, error, **kw: downloader.BookStatus.FAILED)

    settings = make_settings(download_folder=downloads, audiobook_folder=tmp_path / "audiobooks", max_attempts=3)
    downloader.download_books(object(), settings)

    assert "Giving up on Broken: Book (BAD1) after 3 attempts" in caplog.text


def test_download_books_does_not_log_giving_up_while_a_book_still_has_attempts_left(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    marked, accessories, decrypt_calls = [], [], []
    _patch_pipeline(monkeypatch, _library_books(), marked, accessories, decrypt_calls)

    downloader.download_books(
        object(), make_settings(download_folder=downloads, audiobook_folder=tmp_path / "audiobooks")
    )

    assert "Giving up on" not in caplog.text


def test_download_books_parks_an_unlicensable_book_without_burning_attempts(tmp_path, monkeypatch):
    """A withdrawn Plus title is not a failure: it must not count towards max_attempts."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    marked, accessories, decrypt_calls, parked = [], [], [], []
    books = [make_book("GONE", "Withdrawn"), make_book("OK2", "Fine")]
    asins = _asins(books)
    _patch_pipeline(monkeypatch, books, marked, accessories, decrypt_calls)
    monkeypatch.setattr(downloader, "mark_book_unavailable", lambda book_id, error: parked.append(asins[book_id]))
    monkeypatch.setattr(
        downloader, "mark_book_failed", lambda *a, **kw: pytest.fail("a withdrawn title is not a failure")
    )

    def refuse(self, book, temp_dir):
        if book.asin == "GONE":
            raise LicenseError("Audible did not grant a license for GONE (status Denied)")
        temp_dir.mkdir(parents=True, exist_ok=True)
        aaxc = temp_dir / "book.aaxc"
        aaxc.write_bytes(b"aaxc")
        voucher = aaxc.with_suffix(".json")
        voucher.write_text("{}")
        return DownloadedBook(aaxc=aaxc, voucher=voucher, chapters=None)

    monkeypatch.setattr(downloader.Downloader, "download_book", refuse)

    downloader.download_books(
        object(), make_settings(download_folder=downloads, audiobook_folder=tmp_path / "audiobooks")
    )

    assert parked == ["GONE"]
    # The run carries on and is not counted as a failure
    assert marked == [("OK2", "m4b")]


# --- progress reporting ----------------------------------------------------------


class RecordingProgress:
    """A `Progress` that records the lifecycle instead of drawing anything."""

    def __init__(self):
        self.started = None
        self.advances = []
        self.finished = 0

    def start(self, desc, total):
        self.started = (desc, total)

    def advance(self, amount):
        self.advances.append(amount)

    def finish(self):
        self.finished += 1


class FakeStream:
    """
    Stands in for a streaming httpx response.

    `num_bytes_downloaded` is the counter `_stream_to_file` takes its deltas from, so
    it has to advance as the chunks are yielded, exactly as httpx advances it.
    """

    def __init__(self, chunks, content_length=None, fail_after=None, status_code=200, content_type=None):
        self._chunks = chunks
        self._fail_after = fail_after
        self.headers = {} if content_length is None else {"Content-Length": str(content_length)}
        if content_type is not None:
            self.headers["content-type"] = content_type
        self.status_code = status_code
        self.num_bytes_downloaded = 0

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://example.invalid"),
                response=httpx.Response(self.status_code),
            )

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def iter_bytes(self):
        for index, chunk in enumerate(self._chunks):
            if self._fail_after is not None and index == self._fail_after:
                raise httpx.ReadError("connection dropped")
            self.num_bytes_downloaded += len(chunk)
            yield chunk


def test_stream_to_file_reports_progress_against_the_content_length(tmp_path):
    progress = RecordingProgress()
    response = FakeStream([b"abcd", b"ef"], content_length=6)

    _stream_to_file(response, tmp_path / "book.aaxc", desc="Book", progress=progress)

    assert progress.started == ("Book", 6)
    assert progress.advances == [4, 2]
    assert progress.finished == 1
    assert (tmp_path / "book.aaxc").read_bytes() == b"abcdef"


def test_stream_to_file_reports_an_unknown_total_when_there_is_no_content_length(tmp_path):
    """Plenty of responses carry no length; the transfer still has to be reported."""
    progress = RecordingProgress()

    _stream_to_file(FakeStream([b"abc"]), tmp_path / "book.aaxc", progress=progress)

    assert progress.started == (None, None)
    assert progress.advances == [3]


def test_stream_to_file_finishes_the_progress_even_when_the_stream_fails(tmp_path):
    """A bar left open would draw over the next book's."""
    progress = RecordingProgress()
    response = FakeStream([b"abcd", b"ef"], content_length=6, fail_after=1)

    with pytest.raises(httpx.ReadError):
        _stream_to_file(response, tmp_path / "book.aaxc", progress=progress)

    assert progress.finished == 1
    # The partial file is never renamed into place, so nothing looks complete
    assert not (tmp_path / "book.aaxc").exists()
    assert (tmp_path / "book.aaxc.part").exists()


def test_stream_to_file_works_without_a_progress_object(tmp_path):
    """The default: a headless run reports nothing and must not need a guard."""
    _stream_to_file(FakeStream([b"abc"]), tmp_path / "book.aaxc")

    assert (tmp_path / "book.aaxc").read_bytes() == b"abc"


def test_download_file_names_the_transfer_and_uses_the_configured_progress(tmp_path, monkeypatch):
    """
    The AAXC is the multi-gigabyte transfer of the run and was the one download the
    bar could not name, because `download_file` was static and took no description.
    """
    progress = RecordingProgress()
    streamed = {}

    def fake_stream_to_file(response, path, desc=None, progress=None, cancel=None):
        streamed["desc"] = desc
        streamed["progress"] = progress
        streamed["cancel"] = cancel

    monkeypatch.setattr(downloader, "_stream_to_file", fake_stream_to_file)
    monkeypatch.setattr(
        downloader,
        "get_http_client",
        lambda: SimpleNamespace(stream=lambda *a, **kw: _null_stream()),
    )

    cancel = threading.Event()
    downloader.Downloader(object(), progress=progress, cancel=cancel).download_file(
        "https://cdn/x", tmp_path / "b.aaxc", desc="Book"
    )

    assert streamed == {"desc": "Book", "progress": progress, "cancel": cancel}


def _null_stream():
    """A context manager standing in for httpx's streaming response."""

    class _Ctx:
        def __enter__(self):
            return SimpleNamespace(raise_for_status=lambda: None)

        def __exit__(self, *exc):
            return False

    return _Ctx()


def test_download_books_returns_the_counts_the_run_record_is_written_from(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    marked, accessories, decrypt_calls = [], [], []
    _patch_pipeline(monkeypatch, _library_books(), marked, accessories, decrypt_calls)

    stats = downloader.download_books(
        object(), make_settings(download_folder=downloads, audiobook_folder=tmp_path / "audiobooks")
    )

    # Three books, one of which (BAD1) raises inside the fake download
    assert stats == DownloadStats(attempted=3, succeeded=2, failed=1, unavailable=0)


def test_download_books_counts_a_parked_book_separately_from_a_failure(tmp_path, monkeypatch):
    """A licence Audible refuses is not a failure, so the run is not partial for it."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    marked, accessories, decrypt_calls = [], [], []
    _patch_pipeline(monkeypatch, [make_book("NOLIC", "Unlicensed")], marked, accessories, decrypt_calls)

    def refuse(self, book, temp_dir):
        raise LicenseError("not consumable")

    monkeypatch.setattr(downloader.Downloader, "download_book", refuse)

    stats = downloader.download_books(
        object(), make_settings(download_folder=downloads, audiobook_folder=tmp_path / "audiobooks")
    )

    assert stats == DownloadStats(attempted=1, succeeded=0, failed=0, unavailable=1)


def test_download_books_respects_max_download_in_the_attempted_count(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    marked, accessories, decrypt_calls = [], [], []
    _patch_pipeline(monkeypatch, _library_books(), marked, accessories, decrypt_calls)

    stats = downloader.download_books(
        object(),
        make_settings(download_folder=downloads, audiobook_folder=tmp_path / "audiobooks", max_download=1),
    )

    assert stats.attempted == 1


class FakeStreamingClient:
    """
    Stands in for a client with a `.stream()` context manager.

    Used for all three transfers: `httpx.Client` for the AAXC and the cover, and the
    authenticated `audible.client.session` for the PDF. Records what it was called
    with, which is the only way to assert on headers the real code never returns.
    """

    def __init__(self, response):
        self._response = response
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._response


@pytest.fixture
def fresh_http_client(monkeypatch):
    """`get_http_client` memoises into a module global; give each test a clean one."""
    monkeypatch.setattr(downloader, "_http_client", None)
    yield
    downloader._http_client = None


def _downloader_with(client=None, auth=None, progress=None):
    """A Downloader over a fake `Audible`, with only the pieces a given method touches."""
    return downloader.Downloader(SimpleNamespace(client=client, auth=auth), progress=progress)


# --- get_http_client ---------------------------------------------------------


def test_get_http_client_reuses_one_client(fresh_http_client):
    assert downloader.get_http_client() is downloader.get_http_client()


def test_get_http_client_is_configured_for_a_multi_gigabyte_download(fresh_http_client):
    """
    httpx defaults to 5s, which aborted a part-finished AAXC on any brief CDN stall,
    and the download link redirects.
    """
    client = downloader.get_http_client()

    assert client.follow_redirects is True
    assert client.timeout.connect == 30.0
    assert client.timeout.read == 120.0


# --- get_download_link -------------------------------------------------------


def test_get_download_link_pulls_the_offline_url_out_of_the_license():
    license_response = {
        "content_license": {"content_metadata": {"content_url": {"offline_url": "https://cdn/book.aaxc"}}}
    }

    assert downloader.Downloader.get_download_link(license_response) == "https://cdn/book.aaxc"


# --- get_license_response ----------------------------------------------------


def test_license_response_asks_for_a_downloadable_drm_copy():
    """`quality="High"` and `Adrm`/`Download` are what make the response an AAXC."""
    calls = []

    class FakeClient:
        def post(self, path, body):
            calls.append({"path": path, "body": body})
            return {"content_license": {"status_code": "Granted"}}

    _downloader_with(client=FakeClient()).get_license_response("B001", quality="High")

    assert calls == [
        {
            "path": "content/B001/licenserequest",
            "body": {"drm_type": "Adrm", "consumption_type": "Download", "quality": "High"},
        }
    ]


def test_license_response_reports_a_denial_that_carries_no_message():
    class FakeClient:
        def post(self, path, body):
            return {"content_license": {"status_code": "Denied"}}

    with pytest.raises(LicenseError, match="no reason given"):
        _downloader_with(client=FakeClient()).get_license_response("B001", quality="High")


# --- get_chapter_info --------------------------------------------------------


def test_get_chapter_info_returns_the_chapter_info_block():
    chapter_info = {"chapters": [{"title": "One", "start_offset_ms": 0, "length_ms": 10}]}

    class FakeClient:
        def get(self, url, params=None):
            assert url == "content/B001/metadata"
            assert params == {"response_groups": "chapter_info"}
            return {"content_metadata": {"chapter_info": chapter_info}}

    assert _downloader_with(client=FakeClient()).get_chapter_info("B001") == chapter_info


@pytest.mark.parametrize("response", [{}, {"content_metadata": {}}])
def test_get_chapter_info_returns_none_when_the_response_carries_none(response):
    class FakeClient:
        def get(self, url, params=None):
            return response

    assert _downloader_with(client=FakeClient()).get_chapter_info("B001") is None


def test_get_chapter_info_swallows_a_transport_error():
    """Chapters are a nice-to-have; losing them must not cost the whole book."""

    class FakeClient:
        def get(self, url, params=None):
            raise httpx.ReadTimeout("boom")

    assert _downloader_with(client=FakeClient()).get_chapter_info("B001") is None


# --- download_file -----------------------------------------------------------


def test_download_file_sends_the_user_agent_the_cdn_expects(tmp_path, monkeypatch):
    client = FakeStreamingClient(FakeStream([b"abc"], content_length=3))
    monkeypatch.setattr(downloader, "get_http_client", lambda: client)
    out = tmp_path / "book.aaxc"

    _downloader_with().download_file("https://cdn/book.aaxc", out)

    assert out.read_bytes() == b"abc"
    assert client.calls[0]["headers"]["User-Agent"].startswith("Audible/")


def test_download_file_raises_on_an_http_error(tmp_path, monkeypatch):
    """The book must go back to the queue rather than be filed from a half file."""
    monkeypatch.setattr(downloader, "get_http_client", lambda: FakeStreamingClient(FakeStream([], status_code=500)))
    out = tmp_path / "book.aaxc"

    with pytest.raises(httpx.HTTPStatusError):
        _downloader_with().download_file("https://cdn/book.aaxc", out)
    assert not out.exists()


# --- download_pdf ------------------------------------------------------------


def _pdf_downloader(response):
    auth = SimpleNamespace(locale=SimpleNamespace(domain="co.uk"))
    session = FakeStreamingClient(response)
    audible = SimpleNamespace(client=SimpleNamespace(session=session), auth=auth)
    return downloader.Downloader(audible), session


def test_download_pdf_writes_the_companion_file(tmp_path):
    out = tmp_path / "book.pdf"
    downloader_, session = _pdf_downloader(FakeStream([b"%PDF-1.4"], content_length=8, content_type="application/pdf"))

    assert downloader_.download_pdf("B001", str(out)) is True
    assert out.read_bytes() == b"%PDF-1.4"
    assert session.calls[0]["url"] == "https://www.audible.co.uk/companion-file/B001"


def test_download_pdf_treats_a_404_as_no_pdf(tmp_path):
    """Genuinely absent, so it returns False rather than sending the book back to the queue."""
    out = tmp_path / "book.pdf"
    downloader_, _ = _pdf_downloader(FakeStream([], status_code=404))

    assert downloader_.download_pdf("B001", str(out)) is False
    assert not out.exists()


def test_download_pdf_rejects_an_html_login_page(tmp_path):
    """A redirect to a sign-in page is a 200; only the content type gives it away."""
    out = tmp_path / "book.pdf"
    downloader_, _ = _pdf_downloader(FakeStream([b"<html>"], content_type="text/html; charset=utf-8"))

    assert downloader_.download_pdf("B001", str(out)) is False
    assert not out.exists()


def test_download_pdf_accepts_an_octet_stream(tmp_path):
    out = tmp_path / "book.pdf"
    downloader_, _ = _pdf_downloader(FakeStream([b"%PDF"], content_type="application/octet-stream"))

    assert downloader_.download_pdf("B001", str(out)) is True


def test_download_pdf_raises_on_a_server_error(tmp_path):
    out = tmp_path / "book.pdf"
    downloader_, _ = _pdf_downloader(FakeStream([], status_code=503))

    with pytest.raises(httpx.HTTPStatusError):
        downloader_.download_pdf("B001", str(out))


# --- download_cover ----------------------------------------------------------


def test_download_cover_uses_the_unauthenticated_client(tmp_path, monkeypatch):
    """
    The audible session signs every request, which would hand the account's ADP
    token to the image CDN.
    """
    client = FakeStreamingClient(FakeStream([JPEG_BYTES], content_length=len(JPEG_BYTES)))
    monkeypatch.setattr(downloader, "get_http_client", lambda: client)
    out = tmp_path / "cover.jpg"

    assert _downloader_with().download_cover("https://img/c.jpg", str(out)) is True
    assert out.read_bytes() == JPEG_BYTES
    assert "headers" not in client.calls[0]


def test_download_cover_treats_a_404_as_no_cover(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "get_http_client", lambda: FakeStreamingClient(FakeStream([], status_code=404)))
    out = tmp_path / "cover.jpg"

    assert _downloader_with().download_cover("https://img/c.jpg", str(out)) is False
    assert not out.exists()


def test_download_cover_raises_on_a_server_error(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "get_http_client", lambda: FakeStreamingClient(FakeStream([], status_code=500)))

    with pytest.raises(httpx.HTTPStatusError):
        _downloader_with().download_cover("https://img/c.jpg", str(tmp_path / "cover.jpg"))


# --- download_book -----------------------------------------------------------


def _book_downloader(monkeypatch, chapter_info=None, events=None):
    """A Downloader whose license, voucher and transfer are all recorded, not performed."""
    license_response = {
        "content_license": {
            "status_code": "Granted",
            "content_metadata": {"content_url": {"offline_url": "https://cdn/book.aaxc"}},
        }
    }

    class FakeClient:
        def post(self, path, body):
            return license_response

        def get(self, url, params=None):
            return {"content_metadata": {"chapter_info": chapter_info}}

    monkeypatch.setattr(downloader, "decrypt_voucher_from_licenserequest", lambda auth, response: {"key": "K"})
    downloader_ = _downloader_with(client=FakeClient(), auth=object())

    def fake_download_file(self, url, filename, desc=None):
        if events is not None:
            events.append(("download_file", desc, Path(filename).with_suffix(".json").exists()))
        Path(filename).write_bytes(b"aaxc")

    monkeypatch.setattr(downloader.Downloader, "download_file", fake_download_file)
    return downloader_


def test_download_book_writes_the_voucher_before_fetching_the_audio(tmp_path, monkeypatch):
    """
    Documented ordering: a key problem should surface in a second, not after several
    hundred megabytes have been pulled down.
    """
    events = []
    downloader_ = _book_downloader(monkeypatch, events=events)

    downloaded = downloader_.download_book(make_book(asin="B001", title="A Title"), tmp_path / "work")

    assert events == [("download_file", "A Title", True)]
    assert downloaded.voucher.read_text().strip().startswith("{")
    assert downloaded.aaxc.name == "A Title.aaxc"


def test_download_book_flattens_the_chapters_of_a_multi_part_book(tmp_path, monkeypatch):
    """Audible nests the real chapters under a "Part One" marker for a split book."""
    chapter_info = {
        "chapters": [
            {
                "title": "Part One",
                "start_offset_ms": 0,
                "length_ms": 20,
                "chapters": [{"title": "Chapter 1", "start_offset_ms": 0, "length_ms": 10}],
            }
        ]
    }
    downloader_ = _book_downloader(monkeypatch, chapter_info=chapter_info)

    downloaded = downloader_.download_book(make_book(asin="B001"), tmp_path / "work")

    # The "Part One" wrapper is replaced by its children, not kept alongside them.
    assert [c["title"] for c in downloaded.chapters] == ["Chapter 1"]


def test_download_book_returns_no_chapters_when_there_are_none(tmp_path, monkeypatch):
    downloader_ = _book_downloader(monkeypatch, chapter_info=None)

    assert downloader_.download_book(make_book(asin="B001"), tmp_path / "work").chapters is None


# --- cancellation ---------------------------------------------------------------


def test_stream_to_file_stops_between_chunks_once_cancelled(tmp_path):
    """A cancel lands within a chunk of a multi-gigabyte download, not at the end of it."""
    progress = RecordingProgress()
    cancel = threading.Event()

    def chunks():
        yield b"abcd"
        cancel.set()
        yield b"ef"

    with pytest.raises(downloader.SyncCancelled, match="Book"):
        _stream_to_file(
            FakeStream(chunks(), content_length=6),
            tmp_path / "book.aaxc",
            desc="Book",
            progress=progress,
            cancel=cancel,
        )

    # The bar is closed and nothing that looks complete is left behind
    assert progress.finished == 1
    assert not (tmp_path / "book.aaxc").exists()
    assert (tmp_path / "book.aaxc.part").read_bytes() == b"abcd"


def test_stream_to_file_ignores_a_cancel_event_that_is_not_set(tmp_path):
    _stream_to_file(FakeStream([b"ab"], content_length=2), tmp_path / "x", cancel=threading.Event())

    assert (tmp_path / "x").read_bytes() == b"ab"


def _cancel_patches(monkeypatch, books, claimed, released):
    asins = _asins(books)
    monkeypatch.setattr(downloader, "get_books_to_download", lambda **kw: books)
    monkeypatch.setattr(
        downloader, "claim_book_for_download", lambda book_id, **kw: claimed.append(asins[book_id]) or True
    )
    monkeypatch.setattr(downloader, "release_book", lambda book_id: released.append(asins[book_id]))
    monkeypatch.setattr(downloader, "mark_book_downloaded", lambda asin, **kw: pytest.fail("should not be marked"))
    monkeypatch.setattr(downloader, "mark_book_failed", lambda *a, **kw: pytest.fail("a cancel is not a failure"))


def test_download_books_hands_the_book_back_when_cancelled_mid_download(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    claimed, released = [], []
    _cancel_patches(monkeypatch, [make_book("A1", "First"), make_book("A2", "Second")], claimed, released)
    cancel = threading.Event()

    def stop(self, book, temp_dir):
        cancel.set()
        raise downloader.SyncCancelled("stopped")

    monkeypatch.setattr(downloader.Downloader, "download_book", stop)

    stats = downloader.download_books(
        object(), make_settings(download_folder=tmp_path / "dl", audiobook_folder=tmp_path / "lib"), cancel=cancel
    )

    # The book in hand goes back with its attempt untouched; the next is never claimed
    assert claimed == ["A1"]
    assert released == ["A1"]
    assert stats == DownloadStats(attempted=2, succeeded=0, failed=0, unavailable=0, cancelled=True)
    assert "handing it back to the queue" in caplog.text


def test_download_books_claims_nothing_once_cancelled(tmp_path, monkeypatch):
    """Checked before the claim, so a book the run never reached is left as it was."""
    claimed, released = [], []
    _cancel_patches(monkeypatch, [make_book("A1", "First")], claimed, released)
    cancel = threading.Event()
    cancel.set()

    stats = downloader.download_books(
        object(), make_settings(download_folder=tmp_path / "dl", audiobook_folder=tmp_path / "lib"), cancel=cancel
    )

    assert claimed == []
    assert released == []
    assert stats.cancelled is True


def test_download_books_hands_the_downloader_the_cancel_event(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "get_books_to_download", lambda **kw: [])
    seen = {}

    class SpyDownloader(downloader.Downloader):
        def __init__(self, audible, progress=None, cancel=None):
            seen["cancel"] = cancel

    monkeypatch.setattr(downloader, "Downloader", SpyDownloader)
    cancel = threading.Event()

    downloader.download_books(object(), make_settings(download_folder=tmp_path / "dl"), cancel=cancel)

    assert seen["cancel"] is cancel


def test_download_books_reports_the_book_in_hand_to_the_run_state(tmp_path, monkeypatch):
    marked, accessories, decrypt_calls = [], [], []
    _patch_pipeline(
        monkeypatch, [make_book("OK2", "Two"), make_book("OK3", "Three")], marked, accessories, decrypt_calls
    )
    state = RunState()
    state.begin(1)
    snapshots = []
    original = downloader.Downloader.download_book

    def spy(self, book, temp_dir):
        snapshots.append(state.snapshot())
        return original(self, book, temp_dir)

    monkeypatch.setattr(downloader.Downloader, "download_book", spy)

    downloader.download_books(
        object(), make_settings(download_folder=tmp_path / "dl", audiobook_folder=tmp_path / "lib"), state=state
    )

    assert [(s["book"]["asin"], s["books_done"], s["books_total"]) for s in snapshots] == [("OK2", 0, 2), ("OK3", 1, 2)]
    # Once the loop is over there is no book in hand, and the queue reads as done
    assert state.snapshot()["book"] is None
    assert state.snapshot()["books_done"] == 2


def test_download_books_asks_the_queue_for_one_accounts_books(tmp_path, monkeypatch):
    """The client is one marketplace's, so the queue must be that account's."""
    seen = {}
    monkeypatch.setattr(downloader, "get_books_to_download", lambda **kw: seen.update(kw) or [])

    downloader.download_books(object(), make_settings(download_folder=tmp_path / "dl"), account_id=7)

    assert seen == {"account_id": 7}


def test_download_books_records_where_the_audio_was_filed(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    library = tmp_path / "audiobooks"
    downloads.mkdir()
    marked, accessories, decrypt_calls = [], [], []
    books = [make_book("OK2", "Two")]
    _patch_pipeline(monkeypatch, books, marked, accessories, decrypt_calls)
    filed = {}
    monkeypatch.setattr(downloader, "mark_book_downloaded", lambda book_id, **kw: filed.update(kw))

    downloader.download_books(object(), make_settings(download_folder=downloads, audiobook_folder=library))

    assert filed["file_path"] == str(library / "Author One" / "Two" / "Two.m4b")
    assert Path(filed["file_path"]).exists()
