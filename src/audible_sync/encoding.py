"""
Output encoding for converted audiobooks.

Two formats are supported. ``m4b`` keeps the original AAC audio from Audible in an
MP4 container (a stream copy, no quality loss). ``oga`` re-encodes to Opus in an Ogg
container, which is far smaller at the same perceived quality for speech.

Ogg cannot carry a cover as an attached picture stream or chapters as a chapter
track the way MP4 does, and FFmpeg does not translate either. Instead the cover is
embedded as a base64 ``METADATA_BLOCK_PICTURE`` vorbis comment and the chapters as
``CHAPTERxxx`` / ``CHAPTERxxxNAME`` comments. The helpers here build those values so
they can be written into the FFMETADATA file that FFmpeg already reads.
"""

import base64
import logging
import struct
from pathlib import Path

import mutagen
from mutagen.mp4 import MP4, MP4FreeForm

logger = logging.getLogger(__name__)

# Output format name -> file extension
FORMATS: dict[str, str] = {"m4b": ".m4b", "oga": ".oga"}
DEFAULT_FORMAT = "m4b"

# Opus bitrate in kbps. Only used for the oga format. libopus rejects anything over 256 kbps per channel.
DEFAULT_BITRATE = 64
MAX_BITRATE = 256

# Metadata keys FFmpeg's MP4 muxer maps to iTunes atoms itself. Any other key in the
# FFMETADATA file is silently dropped, so those are added afterwards as freeform atoms.
MP4_NATIVE_KEYS: frozenset[str] = frozenset(
    {
        "title",
        "album",
        "artist",
        "album_artist",
        "composer",
        "genre",
        "date",
        "comment",
        "description",
        "synopsis",
        "copyright",
        "grouping",
        "lyrics",
        "track",
        "disc",
        "encoder",
    }
)
_MP4_FREEFORM_PREFIX = "----:com.apple.iTunes:"
# generate_metadata writes the ASIN into the comment tag of every output file, which
# makes a finished file self-identifying. FFmpeg renames `comment` to DESCRIPTION in Ogg.
_ASIN_COMMENT_PREFIX = "ASIN: "
# iTunes "stik" media kind atom: 2 is Audiobook
_MP4_STIK_AUDIOBOOK = 2

# Picture type 3 is "Cover (front)" in the FLAC picture block specification
_PICTURE_TYPE_FRONT_COVER = 3
_PICTURE_DESCRIPTION = b"Cover Artwork"

# PNG colour type -> number of channels
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# JPEG start-of-frame markers carry the image dimensions. C4 (DHT), C8 (JPG) and CC (DAC) are not frames.
_JPEG_SOF_MARKERS = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def validate_encoding(encoding_format: str, bitrate: int) -> None:
    """
    Check the encoding settings up front so a typo fails at startup rather than
    after a book has been downloaded.

    Raises:
        ValueError: on an unknown format or a bitrate outside 1..MAX_BITRATE
    """
    output_extension(encoding_format)
    if not 1 <= bitrate <= MAX_BITRATE:
        raise ValueError(f"encoding bitrate {bitrate} must be between 1 and {MAX_BITRATE} kbps")


def output_extension(encoding_format: str) -> str:
    """Return the file extension (with leading dot) for an output format."""
    try:
        return FORMATS[encoding_format]
    except KeyError:
        valid = ", ".join(sorted(FORMATS))
        raise ValueError(f"encoding format '{encoding_format}' is not supported, use one of: {valid}") from None


def image_info(data: bytes) -> tuple[str, int, int, int]:
    """
    Read the MIME type, width, height and colour depth of a JPEG or PNG image.

    Only the headers are parsed, no image library is needed. Anything that is not
    recognised returns ("", 0, 0, 0); zeros are permitted in the picture block and
    universally tolerated by players.
    """
    try:
        if data.startswith(_PNG_SIGNATURE):
            width, height, bit_depth, colour_type = struct.unpack(">IIBB", data[16:26])
            return "image/png", width, height, bit_depth * _PNG_CHANNELS.get(colour_type, 0)
        if data.startswith(b"\xff\xd8"):
            return ("image/jpeg", *_jpeg_dimensions(data))
    except (struct.error, IndexError):
        pass
    return "", 0, 0, 0


def _jpeg_dimensions(data: bytes) -> tuple[int, int, int]:
    """Walk JPEG segments until the first start-of-frame and return (width, height, depth)."""
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker in _JPEG_SOF_MARKERS:
            precision, height, width, components = struct.unpack(">BHHB", data[pos + 4 : pos + 10])
            return width, height, precision * components
        (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        pos += 2 + length
    return 0, 0, 0


def picture_block(cover_path: str) -> str:
    """
    Build a base64 METADATA_BLOCK_PICTURE value for an Ogg file from a cover image.

    The layout is the FLAC picture block: big-endian 32-bit picture type, MIME type
    and description (each length-prefixed), width, height, colour depth, number of
    colours (0 unless indexed), then the length-prefixed image bytes.
    """
    data = Path(cover_path).read_bytes()
    mime, width, height, depth = image_info(data)
    if not mime:
        mime = "image/png" if Path(cover_path).suffix.lower() == ".png" else "image/jpeg"
        logger.warning("Could not read image header of %s, embedding as %s without dimensions", cover_path, mime)

    mime_bytes = mime.encode("ascii")
    block = b"".join(
        [
            struct.pack(">I", _PICTURE_TYPE_FRONT_COVER),
            struct.pack(">I", len(mime_bytes)),
            mime_bytes,
            struct.pack(">I", len(_PICTURE_DESCRIPTION)),
            _PICTURE_DESCRIPTION,
            struct.pack(">IIII", width, height, depth, 0),
            struct.pack(">I", len(data)),
            data,
        ]
    )
    return base64.b64encode(block).decode("ascii")


def chapter_tags(chapters: list[dict] | None) -> dict[str, str]:
    """
    Convert Audible chapters to CHAPTERxxx / CHAPTERxxxNAME vorbis comments.

    Times are HH:MM:SS.mmm from ``start_offset_ms``; the title falls back to
    "Chapter" like the [CHAPTER] writer. Values are not escaped here, the
    FFMETADATA writer does that.
    """
    tags: dict[str, str] = {}
    for index, chapter in enumerate(chapters or []):
        total_ms = int(chapter.get("start_offset_ms", 0))
        hours, rest = divmod(total_ms, 3_600_000)
        minutes, rest = divmod(rest, 60_000)
        seconds, millis = divmod(rest, 1000)
        tags[f"CHAPTER{index:03d}"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
        tags[f"CHAPTER{index:03d}NAME"] = str(chapter.get("title", "Chapter"))
    return tags


def write_m4b_extra_tags(path: str | Path, metadata: dict) -> list[str]:
    """
    Add the metadata keys FFmpeg's MP4 muxer cannot write to a finished M4B.

    FFmpeg writes only the keys in ``MP4_NATIVE_KEYS`` into an MP4 (its
    ``use_metadata_tags`` alternative keeps every key but drops the embedded
    cover). Every other non-empty key is stored as an iTunes freeform atom
    (``----:com.apple.iTunes:<key>``), which FFmpeg, Audiobookshelf and most
    taggers read back under the plain key name. A ``media_type`` of
    ``audiobook`` also sets the iTunes ``stik`` media-kind atom.

    Args:
        path: The M4B file, modified in place
        metadata: The same dictionary that was written to the FFMETADATA file

    Returns:
        The keys that were added, in order
    """
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()

    written = []
    for key, value in metadata.items():
        if key in MP4_NATIVE_KEYS or value is None or str(value) == "":
            continue
        audio.tags[f"{_MP4_FREEFORM_PREFIX}{key}"] = [MP4FreeForm(str(value).encode("utf-8"))]
        written.append(key)

    if metadata.get("media_type") == "audiobook":
        audio.tags["stik"] = [_MP4_STIK_AUDIOBOOK]

    if written or "stik" in audio.tags:
        audio.save()
    logger.debug("Added freeform MP4 tags to %s: %s", path, written)
    return written


def read_embedded_asin(path: str | Path) -> str | None:
    """
    Read the ASIN back out of a finished audiobook, or None if it is not there.

    Every file this app writes carries ``comment=ASIN: <asin>``, so an existing
    file in the library can say which book it belongs to. Used to tell a retry of
    the same book (safe to overwrite) from a different book that renders to the
    same name (must not be overwritten).
    """
    try:
        audio = mutagen.File(path)
    except (mutagen.MutagenError, OSError):
        # A file we cannot parse simply has no ASIN as far as the caller is concerned.
        logger.debug("Could not read tags from %s", path, exc_info=True)
        return None

    if audio is None or not audio.tags:
        return None

    for value in _tag_values(audio.tags):
        if value.startswith(_ASIN_COMMENT_PREFIX):
            return value[len(_ASIN_COMMENT_PREFIX) :].strip()
    return None


def _tag_values(tags) -> list[str]:
    """Every tag value in a mutagen tag object as a flat list of strings."""
    values = []
    for _, value in tags.items():
        items = value if isinstance(value, list) else [value]
        values.extend(str(item) for item in items)
    return values
