import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import httpx
from audible.aescipher import decrypt_voucher_from_licenserequest
from audible.exceptions import AuthFlowError, NoRefreshToken, Unauthorized
from tqdm import tqdm

from src.audible import Audible
from src.database import get_books_to_download, mark_book_downloaded
from src.encoding import (
    DEFAULT_BITRATE,
    DEFAULT_FORMAT,
    chapter_tags,
    image_info,
    output_extension,
    picture_block,
    read_embedded_asin,
    write_m4b_extra_tags,
)
from src.model import Book
from src.naming import (
    DEFAULT_FILENAME_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    book_output_paths,
    sanitize_filename,
    temp_book_folder,
)

logger = logging.getLogger(__name__)

# A book is a single multi-hundred-megabyte response, so the read timeout has to be
# generous, but httpx's 5 second default applies to the whole stream and aborts a
# download on any brief CDN stall, throwing away everything fetched so far.
_HTTP_TIMEOUT = httpx.Timeout(30.0, read=120.0)
_http_client: httpx.Client | None = None


class LicenseError(RuntimeError):
    """Audible would not grant a download license for a book."""


class DownloadedBook(NamedTuple):
    """The files `download_book` produced for one book."""

    aaxc: Path
    voucher: Path
    chapters: list | None


def get_http_client() -> httpx.Client:
    """
    Shared client for downloads that must not carry Audible credentials.

    The AAXC comes from a CDN and the cover from a public image host; the
    authenticated session signs every request it makes, so using it for those
    would send the account's ADP token to third-party hosts.
    """
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True)
    return _http_client


def _stream_to_file(response: httpx.Response, path: str | Path, desc: str | None = None) -> None:
    """
    Write a streaming response to `path`, showing progress when the size is known.

    The bytes go to a sibling `.part` file that is renamed once the stream ends, so
    an interrupted run cannot leave a truncated file that looks complete.
    """
    path = Path(path)
    partial = path.with_name(f"{path.name}.part")
    total = int(response.headers.get("Content-Length", 0)) or None

    with tqdm(total=total, unit_scale=True, unit_divisor=1024, unit="B", desc=desc) as progress:
        written = response.num_bytes_downloaded
        with open(partial, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)
                progress.update(response.num_bytes_downloaded - written)
                written = response.num_bytes_downloaded

    partial.replace(path)


class Downloader:
    def __init__(self, audible: Audible):
        self.audible = audible

    def get_license_response(self, asin: str, quality: str) -> dict:
        """
        Request a download license.

        Raises:
            LicenseError: if Audible answers but does not grant the license
        """
        response = self.audible.client.post(
            f"content/{asin}/licenserequest",
            body={
                "drm_type": "Adrm",
                "consumption_type": "Download",
                "quality": quality,
            },
        )

        # A denied license is a normal 200 response with no content_metadata, so
        # without this check the failure surfaces as a bare KeyError further down.
        content_license = response.get("content_license", {})
        status = content_license.get("status_code")
        if status != "Granted":
            message = content_license.get("message", "no reason given")
            raise LicenseError(f"Audible did not grant a license for {asin} (status {status}): {message}")

        return response

    @staticmethod
    def get_download_link(license_response: dict) -> str:
        return license_response["content_license"]["content_metadata"]["content_url"]["offline_url"]

    @staticmethod
    def download_file(url: str, filename: str | Path) -> None:
        headers = {"User-Agent": "Audible/671 CFNetwork/1240.0.4 Darwin/20.6.0"}
        with get_http_client().stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            _stream_to_file(r, filename)

    def get_chapter_info(self, asin: str) -> dict | None:
        """
        Fetch chapter information from the Audible API.

        Args:
            asin: Book ASIN

        Returns:
            Chapter info dictionary or None if not available
        """
        try:
            url = f"content/{asin}/metadata"
            response = self.audible.client.get(url, params={"response_groups": "chapter_info"})
            return response.get("content_metadata", {}).get("chapter_info")
        except httpx.HTTPError as e:
            logger.warning("Could not fetch chapter info for %s: %s", asin, e)
            return None

    def download_book(self, book: Book, temp_dir: Path) -> DownloadedBook:
        """
        Download the AAXC, its voucher and the chapter list into `temp_dir`.

        The voucher is decrypted before the audio is fetched so a key problem is
        found in a second rather than after several hundred megabytes.
        """
        asin = book.asin
        title = book.title
        safe_title = sanitize_filename(title, fallback=asin)

        license_response = self.get_license_response(asin, quality="High")
        dl_link = Downloader.get_download_link(license_response)

        temp_dir.mkdir(parents=True, exist_ok=True)
        aaxc_file = temp_dir / f"{safe_title}.aaxc"

        voucher_file = aaxc_file.with_suffix(".json")
        decrypted_voucher = decrypt_voucher_from_licenserequest(self.audible.auth, license_response)
        voucher_file.write_text(json.dumps(decrypted_voucher, indent=4))

        Downloader.download_file(dl_link, aaxc_file)

        chapters = None
        chapter_info = self.get_chapter_info(asin)
        if chapter_info:
            chapters = flatten_chapters(chapter_info.get("chapters", []))
            if chapters:
                logger.info("Fetched %d chapters for %s", len(chapters), title)

        return DownloadedBook(aaxc=aaxc_file, voucher=voucher_file, chapters=chapters)

    def download_pdf(self, asin: str, output_path: str) -> bool:
        """
        Download the companion PDF.

        Returns False when the book simply has no PDF. Anything else (a timeout, a
        5xx, a disk error) is raised: treating it as "no PDF" would file the book
        as complete and no run would ever fetch the PDF again.
        """
        # Get the domain from the auth object
        domain = self.audible.auth.locale.domain
        url = f"https://www.audible.{domain}/companion-file/{asin}"

        logger.info("Downloading PDF for %s", asin)

        # Use the authenticated client session (has auth headers built-in)
        with self.audible.client.session.stream("GET", url, follow_redirects=True) as r:
            if r.status_code == 404:
                logger.info("No PDF available for %s", asin)
                return False
            r.raise_for_status()

            # Check content type to ensure we got a PDF and not an HTML login page
            content_type = r.headers.get("content-type", "").lower()
            if "pdf" not in content_type and "application/octet-stream" not in content_type:
                logger.warning("Received non-PDF content for %s: %s", asin, content_type)
                return False

            _stream_to_file(r, output_path, desc="PDF")

        logger.info("PDF downloaded: %s", output_path)
        return True

    def download_cover(self, cover_url: str, output_path: str) -> bool:
        """
        Download the high-resolution cover.

        Returns False only when the image is genuinely absent; other failures are
        raised so the book is retried rather than filed without its cover.
        """
        logger.info("Downloading cover image")

        with get_http_client().stream("GET", cover_url) as r:
            if r.status_code == 404:
                logger.info("No cover available at %s", cover_url)
                return False
            r.raise_for_status()
            _stream_to_file(r, output_path, desc="Cover")

        logger.info("Cover downloaded: %s", output_path)
        return True

    def download_annotations(self, asin: str, output_path: str) -> bool:
        """
        Download user annotations and bookmarks.

        Returns False when the book has none; transport failures are raised.
        """
        logger.info("Downloading annotations for %s", asin)
        url = "https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar"
        params = {"type": "AUDI", "key": asin}

        # Use the authenticated client (automatically parses JSON responses)
        annotations_data = self.audible.client.get(url, params=params)

        # Only save if there are actual annotations
        if not annotations_data or not (annotations_data.get("clips") or annotations_data.get("bookmarks")):
            logger.info("No annotations found for %s", asin)
            return False

        with open(output_path, "w") as f:
            json.dump(annotations_data, f, indent=2)
        logger.info("Annotations downloaded: %s", output_path)
        return True


def flatten_chapters(chapters: list | None) -> list:
    """
    Flatten Audible's chapter tree into the list of real chapters.

    A book split into parts comes back as a handful of "Part One" entries, each
    carrying its own nested `chapters` list. Taking only the top level leaves the
    output with a few hours-long chapters instead of the real ones.
    """
    flat = []
    for chapter in chapters or []:
        nested = chapter.get("chapters")
        if nested:
            flat.extend(flatten_chapters(nested))
        else:
            flat.append(chapter)
    return flat


def generate_metadata(book: Book) -> dict:
    """
    Generate comprehensive metadata dictionary from a book.

    Args:
        book: The book to describe

    Returns:
        Dictionary with metadata fields for FFmpeg
    """
    asin = book.asin
    title = book.title
    subtitle = book.subtitle
    authors = book.authors
    narrators = book.narrators
    series = book.series
    genres = book.genres
    release_date = book.release_date

    metadata = {}

    # Title (with subtitle if available)
    full_title = title
    if subtitle:
        full_title = f"{title}: {subtitle}"
    metadata["title"] = full_title
    metadata["album"] = full_title

    # Authors (primary artist)
    if authors:
        metadata["artist"] = "; ".join(authors)
        metadata["album_artist"] = "; ".join(authors)
        metadata["author"] = "; ".join(authors)

    # Narrators (composer field often used for narrators in audiobooks)
    if narrators:
        metadata["composer"] = "; ".join(narrators)

    # Series information
    if series:
        series_info = series[0]
        metadata["series"] = series_info.get("title", "")
        metadata["series-part"] = series_info.get("sequence", "")

    # Genre
    if genres:
        metadata["genre"] = "; ".join(genres)

    # Release date (year). Audible returns ISO dates such as "2020-01-15"
    if release_date:
        metadata["date"] = str(release_date)[:4]

    # ASIN as comment for reference
    metadata["comment"] = f"ASIN: {asin}"

    # Media type
    metadata["media_type"] = "audiobook"

    logger.debug("Generated metadata: %s", metadata)
    return metadata


def _escape_ffmetadata(value) -> str:
    """
    Escape a value for the FFMETADATA1 format.

    FFmpeg treats a backslash as "take the next character literally", so a newline
    is escaped as a backslash followed by the real newline. Writing the two
    characters ``\\`` and ``n`` instead would decode back to a literal ``n``.
    """
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\\n")
        .replace("=", "\\=")
        .replace(";", "\\;")
        .replace("#", "\\#")
    )


def write_ffmpeg_metadata_file(metadata: dict, output_path: str, chapters: list | None = None) -> str:
    """
    Write metadata (and optional chapters) to a file in FFMETADATA1 format.

    Args:
        metadata: Dictionary of metadata key-value pairs
        output_path: Path where metadata file should be written
        chapters: Optional list of chapter dictionaries from Audible API

    Returns:
        Path to the created metadata file
    """
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(";FFMETADATA1\n")

        # Write metadata tags
        for key, value in metadata.items():
            f.write(f"{key}={_escape_ffmetadata(value)}\n")

        # Write chapters if provided
        if chapters:
            f.write("\n")
            for chapter in chapters:
                start_ms = chapter.get("start_offset_ms", 0)
                length_ms = chapter.get("length_ms", 0)
                end_ms = start_ms + length_ms
                title = chapter.get("title", "Chapter")

                f.write("[CHAPTER]\n")
                f.write("TIMEBASE=1/1000\n")
                f.write(f"START={start_ms}\n")
                f.write(f"END={end_ms}\n")
                f.write(f"title={_escape_ffmetadata(title)}\n")
                f.write("\n")

    logger.debug("Wrote FFmpeg metadata file: %s", output_path)
    return output_path


def decrypt_aaxc(
    book: str,
    voucher: str,
    book_data: Book | None = None,
    cover_path: str | None = None,
    chapters: list | None = None,
    *,
    encoding_format: str = DEFAULT_FORMAT,
    bitrate: int = DEFAULT_BITRATE,
) -> str:
    """
    Decrypt an AAXC audiobook with optional metadata, cover art and chapters embedded.

    With ``m4b`` the AAC audio is copied unchanged into an MP4 container. With
    ``oga`` it is re-encoded to Opus at ``bitrate`` kbps in an Ogg container; the
    cover becomes a METADATA_BLOCK_PICTURE comment and the chapters CHAPTERxxx
    comments, because FFmpeg cannot map either into Ogg itself.

    Args:
        book: Path to the AAXC file
        voucher: Path to the voucher JSON file
        book_data: Optional book to generate metadata from
        cover_path: Optional path to cover image to embed
        chapters: Optional list of chapter dictionaries to embed
        encoding_format: Output format, a key of ``src.encoding.FORMATS``
        bitrate: Opus bitrate in kbps, ignored for ``m4b``

    Returns:
        Path to the output file
    """
    output_file = f"{book}{output_extension(encoding_format)}"

    # Load key and iv from .voucher JSON
    with open(voucher) as f:
        voucher_data = json.load(f)

    key = voucher_data["key"]
    iv = voucher_data["iv"]

    # Built once and shared by the FFMETADATA file and the freeform MP4 atoms, so the
    # two can never describe the same book differently.
    metadata = generate_metadata(book_data) if book_data is not None else None
    has_cover = bool(cover_path and Path(cover_path).exists())
    metadata_file: str | None = None

    if encoding_format == "oga":
        tags = dict(metadata) if metadata else {}
        if chapters:
            tags.update(chapter_tags(chapters))
            logger.info("Embedding %d chapters as vorbis comments", len(chapters))
        if has_cover:
            tags["METADATA_BLOCK_PICTURE"] = picture_block(cover_path)
            logger.info("Embedding cover art as METADATA_BLOCK_PICTURE from: %s", cover_path)
        if tags:
            metadata_file = f"{book}.ffmetadata"
            write_ffmpeg_metadata_file(tags, metadata_file)
            logger.info("Metadata file created: %s", metadata_file)
        cmd = _opus_ffmpeg_args(book, key, iv, output_file, metadata_file, bitrate)
    else:
        if metadata:
            metadata_file = f"{book}.ffmetadata"
            write_ffmpeg_metadata_file(metadata, metadata_file, chapters=chapters)
            logger.info("Metadata file with %d chapters created: %s", len(chapters or []), metadata_file)
        cmd = _m4b_ffmpeg_args(
            book, key, iv, output_file, metadata_file, cover_path if has_cover else None, bool(chapters)
        )

    # Run the command
    logger.debug("FFmpeg command: %s", " ".join(str(x) for x in cmd))
    # Only stderr is captured, and -loglevel error keeps it to real errors rather
    # than a progress line every half second for the length of the encode.
    result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        logger.error("FFmpeg error: %s", result.stderr)
        raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)

    # The MP4 muxer drops keys it does not know (series, series-part, author, ...);
    # add them afterwards as iTunes freeform atoms so the M4B carries everything the OGA does
    if encoding_format == "m4b" and metadata:
        extra = write_m4b_extra_tags(output_file, metadata)
        if extra:
            logger.info("Added M4B tags the MP4 muxer cannot write: %s", ", ".join(extra))

    # Cleanup temporary metadata file
    if metadata_file:
        Path(metadata_file).unlink(missing_ok=True)
        logger.debug("Cleaned up metadata file: %s", metadata_file)

    logger.info("Conversion complete: %s", output_file)
    return output_file


def _ffmpeg_base_args(key: str, iv: str, book: str) -> list[str]:
    """
    Start of every ffmpeg command: no banner, no per-second progress lines.

    The progress stream is captured into memory for the whole run and only ever
    read on failure, where it buries the actual error under thousands of lines.
    """
    return ["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error",
            "-audible_key", key, "-audible_iv", iv, "-i", book]  # fmt: skip


def _m4b_ffmpeg_args(
    book: str,
    key: str,
    iv: str,
    output_file: str,
    metadata_file: str | None,
    cover_path: str | None,
    has_chapters: bool,
) -> list[str]:
    """
    Build the ffmpeg command for an M4B stream copy. The cover is mapped as an
    attached picture and chapters come from the FFMETADATA file.
    """
    # Note: ffmpeg-python library has issues with map_metadata/map_chapters
    # See: https://github.com/kkroening/ffmpeg-python/issues/463
    cmd = _ffmpeg_base_args(key, iv, book)

    # Inputs in order, so the metadata file's index is simply the last one
    inputs = [book]
    if cover_path:
        cmd.extend(["-i", cover_path])
        inputs.append(cover_path)
        logger.info("Embedding cover art from: %s", cover_path)
    if metadata_file:
        cmd.extend(["-i", metadata_file])
        inputs.append(metadata_file)

    # Map audio stream from first input
    cmd.extend(["-map", "0:a"])

    # Map cover as attached picture if provided
    if cover_path:
        cmd.extend(["-map", "1:v"])
        cmd.extend(["-c:v", "copy"])
        cmd.extend(["-disposition:v", "attached_pic"])

    if metadata_file:
        metadata_idx = str(len(inputs) - 1)
        cmd.extend(["-map_metadata", metadata_idx])
        # Only claim the chapters when the file actually has some. Pointing
        # -map_chapters at a chapterless metadata file discards the chapter track
        # the AAXC itself carries, which ffmpeg would otherwise have copied.
        if has_chapters:
            cmd.extend(["-map_chapters", metadata_idx])

    # Audio codec and other options
    cmd.extend(["-c:a", "copy"])
    cmd.extend(["-dn"])

    # Add output file
    cmd.append(output_file)
    return cmd


def _opus_ffmpeg_args(
    book: str,
    key: str,
    iv: str,
    output_file: str,
    metadata_file: str | None,
    bitrate: int,
) -> list[str]:
    """
    Build the ffmpeg command for an Ogg Opus re-encode. Ogg has no attached picture
    stream or chapter track, so the cover (METADATA_BLOCK_PICTURE) and chapters
    (CHAPTERxxx) travel as plain tags in the FFMETADATA file, and any chapters in the
    AAXC itself are dropped with ``-map_chapters -1`` so they are not duplicated.
    """
    cmd = _ffmpeg_base_args(key, iv, book)
    if metadata_file:
        cmd.extend(["-i", metadata_file])
    cmd.extend(["-map", "0:a"])
    if metadata_file:
        cmd.extend(["-map_metadata", "1"])
    cmd.extend(["-map_chapters", "-1"])
    cmd.extend(["-c:a", "libopus", "-b:a", f"{bitrate}k", "-vbr", "on"])
    cmd.extend(["-dn"])
    cmd.append(output_file)
    return cmd


def _resolve_output_path(final_folder: Path, stem: str, extension: str, asin: str) -> tuple[Path, str]:
    """
    Pick a path for the finished book that will not overwrite a different book.

    Two owned titles can render to the same name (an abridged and an unabridged
    edition, a re-recording, or two long titles that match once truncated). The
    file carries its own ASIN, so a file belonging to this book is reused and
    anything else gets the ASIN appended.

    Returns:
        (path, stem): the accessories are named from the same stem.
    """
    path = final_folder / f"{stem}{extension}"
    if not path.exists() or read_embedded_asin(path) == asin:
        return path, stem

    unique_stem = f"{stem} [{asin}]"
    logger.warning(
        "%s already exists and belongs to another book, filing this one as %s",
        path,
        f"{unique_stem}{extension}",
    )
    return final_folder / f"{unique_stem}{extension}", unique_stem


def _download_accessories(downloader: Downloader, book: Book, temp_dir: Path, safe_title: str) -> dict[str, Path]:
    """
    Fetch the PDF, cover and annotations into the working folder.

    Returns the ones that exist, keyed by the database column they belong to.
    A book with no PDF or no annotations is normal; a failed request raises.
    """
    asin = book.asin
    cover_url = book.cover_url
    has_pdf = book.has_pdf
    accessories: dict[str, Path] = {}

    if has_pdf:
        temp_pdf = temp_dir / f"{safe_title}.pdf"
        if downloader.download_pdf(asin, str(temp_pdf)):
            accessories["pdf_path"] = temp_pdf

    if cover_url:
        # The extension comes from the bytes, not the URL: plenty of cover URLs carry
        # no extension at all and the name is kept permanently in the library.
        temp_cover = temp_dir / f"{safe_title}_cover"
        if downloader.download_cover(cover_url, str(temp_cover)):
            mime = image_info(temp_cover.read_bytes())[0]
            suffix = ".png" if mime == "image/png" else ".jpg"
            accessories["cover_path"] = temp_cover.replace(temp_cover.with_name(f"{safe_title}_cover{suffix}"))

    temp_annotations = temp_dir / f"{safe_title}_annotations.json"
    if downloader.download_annotations(asin, str(temp_annotations)):
        accessories["annotations_path"] = temp_annotations

    return accessories


def _process_book(
    downloader: Downloader,
    book: Book,
    temp_dir: Path,
    audiobook_folder: str,
    folder_template: str = DEFAULT_FOLDER_TEMPLATE,
    filename_template: str = DEFAULT_FILENAME_TEMPLATE,
    encoding_format: str = DEFAULT_FORMAT,
    bitrate: int = DEFAULT_BITRATE,
):
    """
    Download, decrypt and file a single book. Raises on any failure so the
    caller can decide how to handle it.

    Args:
        downloader: Downloader bound to an authenticated Audible client
        book: The book to process
        temp_dir: Temporary working folder for this book
        audiobook_folder: Root folder for the final organised library
        folder_template: Naming template for the book folder (see src.naming)
        filename_template: Naming template for the file name without extension
        encoding_format: Output format (see src.encoding.FORMATS)
        bitrate: Opus bitrate in kbps, only used for oga
    """
    asin = book.asin
    title = book.title
    safe_title = sanitize_filename(title, fallback=asin)

    # Download the Book
    logger.info("Downloading %s", title)
    download = downloader.download_book(book, temp_dir)
    logger.info("Download complete")
    logger.debug("Book: %s", download.aaxc)
    logger.debug("Voucher: %s", download.voucher)

    # Accessories are fetched before decryption so the cover can be embedded
    accessories = _download_accessories(downloader, book, temp_dir, safe_title)

    # Decrypt the Book with metadata, cover art, and chapters embedded
    logger.info("Decrypting and embedding metadata")
    cover = accessories.get("cover_path")
    audiobook = decrypt_aaxc(
        download.aaxc,
        download.voucher,
        book_data=book,
        cover_path=str(cover) if cover else None,
        chapters=download.chapters,
        encoding_format=encoding_format,
        bitrate=bitrate,
    )

    # The encrypted source is no longer needed; releasing it here keeps peak disk
    # use at roughly one copy of the book rather than three.
    download.aaxc.unlink(missing_ok=True)
    download.voucher.unlink(missing_ok=True)

    # Determine final location from the naming templates (all path segments sanitized)
    final_folder, stem = book_output_paths(book, audiobook_folder, folder_template, filename_template)
    final_folder.mkdir(parents=True, exist_ok=True)
    to_path, stem = _resolve_output_path(final_folder, stem, output_extension(encoding_format), asin)

    # Move the Book to its final location. A move is a rename when the download and
    # library folders share a filesystem, where a copy would write the whole book again.
    shutil.move(audiobook, to_path)
    logger.info("Book moved to %s", to_path)

    # Move accessories alongside it, keeping the same stem
    suffixes = {"pdf_path": ".pdf", "annotations_path": "_annotations.json"}
    final_paths: dict[str, str] = {}
    for column, temp_path in accessories.items():
        suffix = suffixes.get(column) or f"_cover{temp_path.suffix}"
        final_path = final_folder / f"{stem}{suffix}"
        shutil.move(temp_path, final_path)
        final_paths[column] = str(final_path)
        logger.info("%s moved to %s", column.removesuffix("_path").capitalize(), final_path)

    # Record the accessory paths and the completed status in one statement
    mark_book_downloaded(asin, encoding_format=encoding_format, **final_paths)


def download_books(
    audible,
    download_folder: str,
    audiobook_folder: str,
    max: int | None = None,
    *,
    folder_template: str = DEFAULT_FOLDER_TEMPLATE,
    filename_template: str = DEFAULT_FILENAME_TEMPLATE,
    encoding_format: str = DEFAULT_FORMAT,
    bitrate: int = DEFAULT_BITRATE,
):
    """
    Download, decrypt and file every book waiting for download (up to `max`).

    Each book is processed independently: a failure is logged, its temporary
    files are removed, and processing continues with the next book. The book
    keeps its 'waiting_download' status so it is retried on the next run. An
    authentication failure stops the run instead, because every remaining book
    would fail the same way.

    `folder_template` and `filename_template` control where each book is filed
    under `audiobook_folder` (see src.naming for the placeholder syntax).
    `encoding_format` selects m4b (stream copy) or oga (Opus at `bitrate` kbps).
    """
    waiting_download = get_books_to_download()
    total_to_download = len(waiting_download)
    number_to_download = total_to_download if max is None else min(max, total_to_download)

    logger.info(
        "Downloading %d books of %d waiting download as %s", number_to_download, total_to_download, encoding_format
    )

    loop = waiting_download[:number_to_download]
    downloader = Downloader(audible)
    succeeded = 0
    failed = []

    for book in loop:
        asin = book.asin
        title = book.title
        temp_dir = temp_book_folder(download_folder, asin, title)

        try:
            _process_book(
                downloader,
                book,
                temp_dir,
                audiobook_folder,
                folder_template=folder_template,
                filename_template=filename_template,
                encoding_format=encoding_format,
                bitrate=bitrate,
            )
            succeeded += 1
        except (Unauthorized, NoRefreshToken, AuthFlowError):
            logger.exception("Audible rejected our credentials, stopping before %s (%s)", title, asin)
            failed.append((asin, title))
            break
        except Exception:
            logger.exception("Failed to process %s (%s), skipping", title, asin)
            failed.append((asin, title))
        finally:
            # Cleanup temporary download folder whether we succeeded or not
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    logger.info("Completed downloads: %d succeeded, %d failed", succeeded, len(failed))
    for asin, title in failed:
        logger.warning("Not downloaded: %s (%s)", title, asin)
