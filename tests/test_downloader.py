import json
import logging
from pathlib import Path

import pytest

import src.downloader as downloader
from src.downloader import (
    generate_metadata,
    sanitize_filename,
    temp_book_folder,
    write_ffmpeg_metadata_file,
)


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
    row = [None] * 18
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


def test_download_books_skips_failed_book_and_cleans_temp(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    library = tmp_path / "audiobooks"
    downloads.mkdir()

    books = [
        make_row("BAD1", "Broken: Book", series=[{"title": "S/eries", "sequence": "1"}]),
        make_row(
            "OK2",
            "Northern Lights: Book 1",
            authors=("Philip Pullman",),
            series=[{"title": "His Dark Materials", "sequence": "1"}],
        ),
        make_row("OK3", "No Series / No Author", authors=()),
    ]
    marked = []
    accessories = []

    monkeypatch.setattr(downloader, "get_books_to_download", lambda: books)
    monkeypatch.setattr(downloader, "mark_book_downloaded", marked.append)
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
        out = f"{book}.m4b"
        Path(out).write_bytes(b"m4b")
        return out

    monkeypatch.setattr(downloader.Downloader, "download_book", fake_download_book)
    monkeypatch.setattr(downloader.Downloader, "download_annotations", lambda self, asin, out: False)
    monkeypatch.setattr(downloader, "decrypt_aaxc", fake_decrypt)

    downloader.download_books(object(), str(downloads), str(library))

    assert marked == ["OK2", "OK3"]
    assert accessories == ["OK2", "OK3"]
    assert list(downloads.iterdir()) == []

    final = sorted(str(p.relative_to(library)) for p in library.rglob("*.m4b"))
    assert final == [
        "Philip Pullman/His Dark Materials/1 - Northern Lights - Book 1/Northern Lights - Book 1.m4b",
        "Unknown Author/No Series - No Author/No Series - No Author.m4b",
    ]


def test_download_books_raises_clear_error_when_no_license(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    books = [make_row("NOLIC", "Unlicensed")]
    monkeypatch.setattr(downloader, "get_books_to_download", lambda: books)
    monkeypatch.setattr(downloader, "mark_book_downloaded", lambda asin: pytest.fail("should not be marked"))
    monkeypatch.setattr(downloader.Downloader, "download_book", lambda self, book, folder: None)

    downloader.download_books(object(), str(tmp_path / "dl"), str(tmp_path / "lib"))

    assert "Could not obtain a download license" in caplog.text
    assert "1 failed" in caplog.text
