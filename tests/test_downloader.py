import base64
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.downloader as downloader
from src.downloader import (
    decrypt_aaxc,
    generate_metadata,
    sanitize_filename,
    temp_book_folder,
    write_ffmpeg_metadata_file,
)
from src.encoding import output_extension


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


def make_row(
    asin="B001",
    title="Title",
    subtitle="",
    authors=("Author One",),
    narrators=("Narrator",),
    series=None,
    genres=("Fiction",),
    release_date="2020-05-01",
    cover_url="",
    has_pdf=0,
):
    """Build a database row tuple matching the library table column order."""
    row = [None] * 20
    row[0] = asin
    row[1] = title
    row[2] = subtitle
    row[3] = json.dumps(list(authors))
    row[4] = json.dumps(list(narrators))
    row[5] = json.dumps(series or [])
    row[6] = json.dumps(list(genres))
    row[11] = release_date
    row[12] = cover_url
    row[13] = "waiting_download"
    row[17] = has_pdf
    return tuple(row)


def test_generate_metadata_maps_fields():
    row = make_row(
        title="Title",
        subtitle="Sub",
        authors=("A", "B"),
        narrators=("N",),
        series=[{"title": "Series", "sequence": "2"}],
        genres=("G1", "G2"),
    )
    meta = generate_metadata(row)
    assert meta["title"] == "Title: Sub"
    assert meta["album"] == "Title: Sub"
    assert meta["artist"] == "A; B"
    assert meta["composer"] == "N"
    assert meta["series"] == "Series"
    assert meta["series-part"] == "2"
    assert meta["genre"] == "G1; G2"
    assert meta["date"] == "2020"
    assert meta["comment"] == "ASIN: B001"


def test_generate_metadata_handles_missing_optional_fields():
    row = make_row(authors=(), narrators=(), genres=(), release_date=None)
    meta = generate_metadata(row)
    assert meta["title"] == "Title"
    assert "artist" not in meta
    assert "composer" not in meta
    assert "genre" not in meta
    assert "date" not in meta


def test_write_ffmpeg_metadata_file_escapes_and_writes_chapters(tmp_path):
    out = tmp_path / "meta.txt"
    chapters = [
        {"start_offset_ms": 0, "length_ms": 1000, "title": "Intro"},
        {"start_offset_ms": 1000, "length_ms": 500, "title": "Part 1; a=b"},
    ]
    write_ffmpeg_metadata_file({"title": "A=B", "comment": "x#y"}, str(out), chapters=chapters)
    text = out.read_text(encoding="utf-8")

    assert text.startswith(";FFMETADATA1\n")
    assert "title=A\\=B\n" in text
    assert "comment=x\\#y\n" in text
    assert text.count("[CHAPTER]") == 2
    assert "START=1000\nEND=1500\ntitle=Part 1\\; a\\=b\n" in text


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
    row = make_row(series=[{"title": "Series", "sequence": "2"}])
    out = decrypt_aaxc(aaxc.book, aaxc.voucher, book_data=row, cover_path=aaxc.cover, chapters=CHAPTERS)

    assert out == f"{aaxc.book}.m4b"
    metadata_file = f"{aaxc.book}.ffmetadata"
    assert fake_ffmpeg.cmd == [
        "ffmpeg", "-y",
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
        book_data=make_row(),
        cover_path=aaxc.cover,
        chapters=CHAPTERS,
        encoding_format="oga",
        bitrate=96,
    )

    assert out == f"{aaxc.book}.oga"
    metadata_file = f"{aaxc.book}.ffmetadata"
    assert fake_ffmpeg.cmd == [
        "ffmpeg", "-y",
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
    decrypt_aaxc(aaxc.book, aaxc.voucher, book_data=make_row(), encoding_format="oga")

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
    with pytest.raises(Exception, match="FFmpeg conversion failed: boom"):
        decrypt_aaxc(aaxc.book, aaxc.voucher, book_data=make_row())
    assert fake_ffmpeg.extra_tags is None


def test_decrypt_aaxc_oga_does_not_write_mp4_tags(fake_ffmpeg, aaxc):
    decrypt_aaxc(aaxc.book, aaxc.voucher, book_data=make_row(), encoding_format="oga")
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
        make_row("BAD1", "Broken: Book", series=[{"title": "S/eries", "sequence": "1"}]),
        make_row(
            "OK2",
            "Northern Lights: Book 1",
            authors=("Philip Pullman",),
            series=[{"title": "His Dark Materials", "sequence": "1"}],
        ),
        make_row("OK3", "No Series / No Author", authors=()),
    ]


def _patch_pipeline(monkeypatch, books, marked, accessories, decrypt_calls):
    """Stub everything that would hit the network, ffmpeg or the database."""
    monkeypatch.setattr(downloader, "get_books_to_download", lambda: books)
    monkeypatch.setattr(
        downloader, "mark_book_downloaded", lambda asin, **kw: marked.append((asin, kw["encoding_format"]))
    )
    monkeypatch.setattr(downloader, "update_book_accessories", lambda asin, **kw: accessories.append(asin))

    def fake_download_book(self, book, folder):
        work = temp_book_folder(folder, book[0], book[1])
        work.mkdir(parents=True, exist_ok=True)
        if book[0] == "BAD1":
            (work / "partial.aaxc").write_bytes(b"junk")
            raise RuntimeError("simulated network failure")
        aaxc = work / "book.aaxc"
        aaxc.write_bytes(b"aaxc")
        voucher = aaxc.with_suffix(".json")
        voucher.write_text("{}")
        return {"book": aaxc, "voucher": voucher, "chapters": None}

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

    downloader.download_books(object(), str(downloads), str(library))

    assert marked == [("OK2", "m4b"), ("OK3", "m4b")]
    assert accessories == ["OK2", "OK3"]
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

    downloader.download_books(object(), str(downloads), str(library), encoding_format="oga", bitrate=48)

    assert marked == [("OK2", "oga"), ("OK3", "oga")]
    assert all(call["encoding_format"] == "oga" and call["bitrate"] == 48 for call in decrypt_calls)
    assert list(library.rglob("*.m4b")) == []
    final = sorted(str(p.relative_to(library)) for p in library.rglob("*.oga"))
    assert final == [
        "Philip Pullman/His Dark Materials/1 - Northern Lights - Book 1/Northern Lights - Book 1.oga",
        "Unknown Author/No Series - No Author/No Series - No Author.oga",
    ]


def test_download_books_raises_clear_error_when_no_license(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    books = [make_row("NOLIC", "Unlicensed")]
    monkeypatch.setattr(downloader, "get_books_to_download", lambda: books)
    monkeypatch.setattr(downloader, "mark_book_downloaded", lambda asin, **kw: pytest.fail("should not be marked"))
    monkeypatch.setattr(downloader.Downloader, "download_book", lambda self, book, folder: None)

    downloader.download_books(object(), str(tmp_path / "dl"), str(tmp_path / "lib"))

    assert "Could not obtain a download license" in caplog.text
    assert "1 failed" in caplog.text
