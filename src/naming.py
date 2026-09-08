"""
File and folder naming for converted audiobooks.

Output paths are built from templates configured in `config.ini` under the
`[naming]` section. A template is a path-like string containing `{placeholder}`
tokens and optional `[...]` groups: the text inside a group is only emitted when
every placeholder inside it has a value, so `[{series}/]` disappears for books
that are not part of a series.
"""

import json
import re
from pathlib import Path

# Characters that are invalid in file names on Windows/SMB shares (plus control chars).
# '/' and '\\' are handled separately so they can be replaced rather than dropped.
_INVALID_PATH_CHARS = re.compile(r'[<>"|?*\x00-\x1f]')
_MAX_NAME_LENGTH = 150
# ext4, NTFS and SMB limit a single path component to 255 bytes, not characters. The
# budget is lower than that to leave room for the suffixes callers append to a stem
# ("_annotations.json", "{asin}_", ".aaxc.ffmetadata").
_MAX_NAME_BYTES = 200
# Device names Windows and Windows SMB clients cannot use as a file or folder name,
# with or without an extension. Compared case-insensitively against the stem.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{n}" for n in range(1, 10)} | {f"LPT{n}" for n in range(1, 10)}
)

DEFAULT_FOLDER_TEMPLATE = "{author}/[{series}/][{sequence} - ]{title}"
DEFAULT_FILENAME_TEMPLATE = "{title}"

PLACEHOLDERS = frozenset(
    {"asin", "author", "authors", "narrator", "narrators", "title", "subtitle", "series", "sequence", "year"}
)

_OPTIONAL_GROUP = re.compile(r"\[([^\[\]]*)\]")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def sanitize_filename(name, fallback: str = "Unknown") -> str:
    """
    Make a string safe to use as a single file or folder name on Linux, macOS,
    Windows and SMB shares.

    - ':' becomes ' -'  (so "Title: Subtitle" -> "Title - Subtitle")
    - '/' and '\\' become '-'
    - Other characters that are invalid on Windows/SMB are removed
    - Whitespace is collapsed, leading/trailing spaces and dots are stripped
    - Names reserved by Windows (CON, NUL, COM1, ...) get a trailing underscore
    - Result is truncated to a safe length in both characters and UTF-8 bytes

    Args:
        name: The raw name (title, author, series, ...). May be None.
        fallback: Returned when the sanitized result would be empty.

    Returns:
        A path-safe name.
    """
    if name is None:
        return fallback

    result = str(name)
    result = result.replace(":", " -")
    result = re.sub(r"[/\\]", "-", result)
    # Collapse first so tabs and newlines become spaces, then again after dropping the
    # invalid characters so removing one does not leave a double space behind.
    result = re.sub(r"\s+", " ", result)
    result = _INVALID_PATH_CHARS.sub("", result)
    result = re.sub(r"\s+", " ", result).strip(" .")
    result = _truncate_to_bytes(result[:_MAX_NAME_LENGTH]).rstrip(" .")

    if result.split(".")[0].upper() in _RESERVED_NAMES:
        result = f"{result}_"

    return result or fallback


def _truncate_to_bytes(value: str, limit: int = _MAX_NAME_BYTES) -> str:
    """
    Trim a name to at most `limit` bytes of UTF-8 without splitting a character.

    File systems cap a path component in bytes, so a title in a script that encodes to
    three bytes per character overflows long before the character cap is reached.
    """
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def temp_book_folder(download_folder: str, asin: str, title: str) -> Path:
    """Temporary working folder for a book while it is downloaded and decrypted"""
    return Path(download_folder) / f"{asin}_{sanitize_filename(title, fallback=asin)}"


def book_template_values(book: tuple) -> dict[str, str]:
    """
    Build the placeholder values for a book from its database row.

    Every value is already path-safe (see `sanitize_filename`), so a title
    containing '/' cannot introduce an extra folder level. Placeholders with no
    data are empty strings, except `title` (falls back to the ASIN) and
    `author`/`authors` (fall back to "Unknown Author").

    Args:
        book: Book tuple from the database (indices as per the library schema)

    Returns:
        Dictionary keyed by placeholder name
    """
    asin = book[0]
    authors = json.loads(book[3]) if book[3] else []
    narrators = json.loads(book[4]) if book[4] else []
    series = json.loads(book[5]) if book[5] else []
    release_date = str(book[11]) if book[11] else ""
    first_series = series[0] if series else {}
    # A sequence without a series title would render as a bare "2 - Title" folder
    # directly under the author, which loses the series context entirely.
    series_title = first_series.get("title")
    sequence = first_series.get("sequence") if series_title else ""

    def clean(value) -> str:
        return sanitize_filename(value, fallback="") if value not in (None, "") else ""

    return {
        "asin": asin,
        "title": sanitize_filename(book[1], fallback=asin),
        "subtitle": clean(book[2]),
        "author": sanitize_filename(authors[0], fallback="Unknown Author") if authors else "Unknown Author",
        "authors": sanitize_filename(", ".join(authors), fallback="Unknown Author") if authors else "Unknown Author",
        "narrator": clean(narrators[0]) if narrators else "",
        "narrators": clean(", ".join(narrators)),
        "series": clean(series_title),
        "sequence": clean(sequence),
        "year": clean(release_date[:4]),
    }


def _placeholder_value(name: str, values: dict[str, str], template: str) -> str:
    if name not in PLACEHOLDERS:
        valid = ", ".join(f"{{{p}}}" for p in sorted(PLACEHOLDERS))
        raise ValueError(
            f"Unknown placeholder '{{{name}}}' in naming template '{template}'. Valid placeholders: {valid}"
        )
    return values.get(name, "")


def render_template(template: str, values: dict[str, str]) -> str:
    """
    Render a naming template against placeholder values.

    - `[...]` groups are kept only when every placeholder inside them is non-empty
    - `{placeholder}` tokens are replaced by their value (empty string if no data)

    Raises:
        ValueError: if the template uses a placeholder that does not exist
    """

    def resolve_group(match: re.Match) -> str:
        content = match.group(1)
        names = _PLACEHOLDER.findall(content)
        if any(not _placeholder_value(name, values, template) for name in names):
            return ""
        return content

    def substitute(match: re.Match) -> str:
        return _placeholder_value(match.group(1), values, template)

    resolved = _OPTIONAL_GROUP.sub(resolve_group, template)
    return _PLACEHOLDER.sub(substitute, resolved)


def validate_templates(folder_template: str, filename_template: str) -> None:
    """
    Check naming templates up front so a typo fails at startup rather than
    after a book has been downloaded and decrypted.

    Raises:
        ValueError: on an unknown placeholder, or a path separator in the filename template
    """
    if "/" in filename_template or "\\" in filename_template:
        raise ValueError(f"naming filename template '{filename_template}' must not contain '/' or '\\'")

    dummy = dict.fromkeys(PLACEHOLDERS, "x")
    render_template(folder_template, dummy)
    render_template(filename_template, dummy)


def book_output_paths(
    book: tuple,
    audiobook_folder: str,
    folder_template: str = DEFAULT_FOLDER_TEMPLATE,
    filename_template: str = DEFAULT_FILENAME_TEMPLATE,
) -> tuple[Path, str]:
    """
    Work out where a converted book and its accessories should be filed.

    Args:
        book: Book tuple from the database
        audiobook_folder: Root folder of the organised library
        folder_template: Folder template, relative to `audiobook_folder`
        filename_template: File name template without extension

    Returns:
        (final_folder, stem): callers append the extension, e.g. `final_folder / f"{stem}.m4b"`.
        A folder that renders to nothing falls back to the ASIN so a book never
        lands directly in the library root; an empty stem falls back to the ASIN too.
    """
    values = book_template_values(book)
    asin = values["asin"]

    segments = []
    for raw_segment in render_template(folder_template, values).split("/"):
        segment = sanitize_filename(raw_segment, fallback="")
        if segment:
            segments.append(segment)
    if not segments:
        segments = [asin]

    stem = sanitize_filename(render_template(filename_template, values), fallback=asin)

    return Path(audiobook_folder).joinpath(*segments), stem
