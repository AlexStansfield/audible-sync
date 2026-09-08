import base64
import struct

import pytest

from src.encoding import (
    DEFAULT_BITRATE,
    DEFAULT_FORMAT,
    chapter_tags,
    image_info,
    output_extension,
    picture_block,
    validate_encoding,
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
