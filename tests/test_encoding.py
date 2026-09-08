import base64
import shutil
import struct
from pathlib import Path

import pytest
from mutagen.mp4 import MP4

from src.encoding import (
    DEFAULT_BITRATE,
    DEFAULT_FORMAT,
    chapter_tags,
    image_info,
    output_extension,
    picture_block,
    read_embedded_asin,
    validate_encoding,
    write_m4b_extra_tags,
)


def fake_jpeg(width=200, height=300, sof=0xC0, components=3):
    """Minimal JPEG header: SOI, an APP0 segment, then a start-of-frame with the given size."""
    app0 = b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00"
    sof_payload = struct.pack(">BHHB", 8, height, width, components)
    frame = bytes([0xFF, sof]) + struct.pack(">H", 2 + len(sof_payload)) + sof_payload
    return b"\xff\xd8" + app0 + frame + b"\xff\xd9"


def fake_png(width=640, height=480, bit_depth=8, colour_type=6):
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, colour_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"


def test_output_extension_known_formats():
    assert output_extension("m4b") == ".m4b"
    assert output_extension("oga") == ".oga"
    assert output_extension(DEFAULT_FORMAT) == ".m4b"


def test_output_extension_rejects_unknown_format():
    with pytest.raises(ValueError, match="not supported"):
        output_extension("mp3")


def test_validate_encoding_accepts_defaults():
    validate_encoding(DEFAULT_FORMAT, DEFAULT_BITRATE)
    validate_encoding("oga", 256)


def test_validate_encoding_rejects_unknown_format():
    with pytest.raises(ValueError, match="'mp3' is not supported"):
        validate_encoding("mp3", 64)


@pytest.mark.parametrize("bitrate", [0, -1, 257])
def test_validate_encoding_rejects_bad_bitrate(bitrate):
    with pytest.raises(ValueError, match="bitrate"):
        validate_encoding("oga", bitrate)


def test_image_info_jpeg_baseline():
    assert image_info(fake_jpeg(200, 300)) == ("image/jpeg", 200, 300, 24)


def test_image_info_jpeg_progressive_and_greyscale():
    assert image_info(fake_jpeg(50, 60, sof=0xC2)) == ("image/jpeg", 50, 60, 24)
    assert image_info(fake_jpeg(50, 60, components=1)) == ("image/jpeg", 50, 60, 8)


def test_image_info_jpeg_skips_huffman_table_marker():
    # A DHT segment (FFC4) before the frame must not be mistaken for a start-of-frame
    dht = b"\xff\xc4" + struct.pack(">H", 4) + b"\x00\x00"
    data = b"\xff\xd8" + dht + fake_jpeg(10, 20)[2:]
    assert image_info(data) == ("image/jpeg", 10, 20, 24)


def test_image_info_png():
    assert image_info(fake_png(640, 480, 8, 6)) == ("image/png", 640, 480, 32)
    assert image_info(fake_png(640, 480, 8, 2)) == ("image/png", 640, 480, 24)


def test_image_info_unknown_returns_zeros():
    assert image_info(b"not an image") == ("", 0, 0, 0)
    assert image_info(b"") == ("", 0, 0, 0)
    assert image_info(b"\xff\xd8\xff") == ("image/jpeg", 0, 0, 0)


def test_picture_block_layout(tmp_path):
    cover = tmp_path / "cover.jpg"
    data = fake_jpeg(200, 300)
    cover.write_bytes(data)

    encoded = picture_block(str(cover))
    assert "\n" not in encoded
    block = base64.b64decode(encoded)

    pos = 0

    def read_u32():
        nonlocal pos
        (value,) = struct.unpack(">I", block[pos : pos + 4])
        pos += 4
        return value

    def read_bytes():
        nonlocal pos
        length = read_u32()
        value = block[pos : pos + length]
        pos += length
        return value

    assert read_u32() == 3
    assert read_bytes() == b"image/jpeg"
    assert read_bytes() == b"Cover Artwork"
    assert (read_u32(), read_u32(), read_u32(), read_u32()) == (200, 300, 24, 0)
    assert read_bytes() == data
    assert pos == len(block)


def test_picture_block_falls_back_to_extension_for_unreadable_image(tmp_path):
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"garbage")
    block = base64.b64decode(picture_block(str(cover)))
    assert block[8:17] == b"image/png"
    # width, height, depth, colours all zero
    assert block[34:50] == b"\x00" * 16


def test_chapter_tags_formats_times_and_names():
    chapters = [
        {"start_offset_ms": 0, "length_ms": 61500, "title": "Opening Credits"},
        {"start_offset_ms": 61500, "length_ms": 100, "title": "Chapter 1"},
        {"start_offset_ms": 3_600_000, "length_ms": 1, "title": "Chapter 2"},
        {"start_offset_ms": 360_000_123, "length_ms": 1, "title": "Late"},
    ]
    assert chapter_tags(chapters) == {
        "CHAPTER000": "00:00:00.000",
        "CHAPTER000NAME": "Opening Credits",
        "CHAPTER001": "00:01:01.500",
        "CHAPTER001NAME": "Chapter 1",
        "CHAPTER002": "01:00:00.000",
        "CHAPTER002NAME": "Chapter 2",
        "CHAPTER003": "100:00:00.123",
        "CHAPTER003NAME": "Late",
    }


def test_chapter_tags_empty_or_none_returns_empty_dict():
    assert chapter_tags(None) == {}
    assert chapter_tags([]) == {}


def test_chapter_tags_default_title():
    assert chapter_tags([{"start_offset_ms": 5}]) == {"CHAPTER000": "00:00:00.005", "CHAPTER000NAME": "Chapter"}


FIXTURE_M4B = Path(__file__).parent / "fixtures" / "silence.m4b"


@pytest.fixture
def m4b(tmp_path):
    """A copy of the tiny silent M4B fixture (AAC, title=Silence, artist=Nobody)."""
    target = tmp_path / "book.m4b"
    shutil.copy(FIXTURE_M4B, target)
    return target


def test_write_m4b_extra_tags_adds_freeform_atoms_and_keeps_native_tags(m4b):
    metadata = {
        "title": "Ignored, FFmpeg wrote this",
        "artist": "Ignored too",
        "author": "Philip Pullman",
        "series": "His Dark Materials",
        "series-part": "1",
        "media_type": "audiobook",
    }

    written = write_m4b_extra_tags(m4b, metadata)

    assert written == ["author", "series", "series-part", "media_type"]
    tags = MP4(m4b).tags
    assert tags["\xa9nam"] == ["Silence"]
    assert tags["\xa9ART"] == ["Nobody"]
    assert bytes(tags["----:com.apple.iTunes:series"][0]) == b"His Dark Materials"
    assert bytes(tags["----:com.apple.iTunes:series-part"][0]) == b"1"
    assert bytes(tags["----:com.apple.iTunes:author"][0]) == b"Philip Pullman"
    assert tags["stik"] == [2]
    assert "----:com.apple.iTunes:title" not in tags


def test_write_m4b_extra_tags_skips_empty_values_and_non_audiobooks(m4b):
    written = write_m4b_extra_tags(m4b, {"series": "", "series-part": None, "title": "x", "media_type": "music"})

    assert written == ["media_type"]
    tags = MP4(m4b).tags
    assert "----:com.apple.iTunes:series" not in tags
    assert "stik" not in tags


def test_write_m4b_extra_tags_with_nothing_to_add_leaves_file_unchanged(m4b):
    before = m4b.read_bytes()

    assert write_m4b_extra_tags(m4b, {"title": "x", "artist": "y"}) == []
    assert m4b.read_bytes() == before


def test_write_m4b_extra_tags_accepts_non_string_values(m4b):
    assert write_m4b_extra_tags(m4b, {"series-part": 3}) == ["series-part"]
    assert bytes(MP4(m4b).tags["----:com.apple.iTunes:series-part"][0]) == b"3"


def test_read_embedded_asin_reads_back_what_the_m4b_writer_stored(tmp_path):
    target = tmp_path / "book.m4b"
    shutil.copy(FIXTURE_M4B, target)
    write_m4b_extra_tags(target, {"series": "Red Rising"})
    audio = MP4(target)
    audio.tags["\xa9cmt"] = ["ASIN: B00LNGMCE2"]
    audio.save()

    assert read_embedded_asin(target) == "B00LNGMCE2"


def test_read_embedded_asin_returns_none_without_an_asin_tag(tmp_path):
    target = tmp_path / "book.m4b"
    shutil.copy(FIXTURE_M4B, target)

    assert read_embedded_asin(target) is None


def test_read_embedded_asin_returns_none_for_an_unreadable_file(tmp_path):
    junk = tmp_path / "not-audio.m4b"
    junk.write_bytes(b"not an audio file")

    assert read_embedded_asin(junk) is None


def test_read_embedded_asin_returns_none_for_a_missing_file(tmp_path):
    assert read_embedded_asin(tmp_path / "gone.m4b") is None


def test_validate_encoding_still_rejects_an_unknown_format():
    with pytest.raises(ValueError, match="not supported"):
        validate_encoding("mp3", 64)
