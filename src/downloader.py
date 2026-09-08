import json
import logging
import shutil
import subprocess
from pathlib import Path

import httpx
from audible.aescipher import decrypt_voucher_from_licenserequest
from tqdm import tqdm

from src.audible import Audible
from src.database import get_books_to_download, mark_book_downloaded, update_book_accessories
from src.encoding import DEFAULT_BITRATE, DEFAULT_FORMAT, chapter_tags, output_extension, picture_block
from src.naming import (
    DEFAULT_FILENAME_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    book_output_paths,
    sanitize_filename,
    temp_book_folder,
)

logger = logging.getLogger(__name__)


class Downloader:
    def __init__(self, audible: Audible):
        self.audible = audible

    def get_license_response(self, asin, quality):
        try:
            response = self.audible.client.post(
                f"content/{asin}/licenserequest",
                body={
                    "drm_type": "Adrm",
                    "consumption_type": "Download",
                    "quality": quality,
                },
            )
            return response
        except Exception as e:
            logger.error("Error getting license response: %s", e)
            return

    @staticmethod
    def get_download_link(license_response):
        return license_response["content_license"]["content_metadata"]["content_url"]["offline_url"]

    @staticmethod
    def download_file(url, filename):
        headers = {"User-Agent": "Audible/671 CFNetwork/1240.0.4 Darwin/20.6.0"}
        with httpx.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0)) or None

            with tqdm(total=total, unit_scale=True, unit_divisor=1024, unit="B") as progress:
                num_bytes_downloaded = r.num_bytes_downloaded
                with open(filename, "wb") as f:
                    for chunck in r.iter_bytes():
                        f.write(chunck)
                        progress.update(r.num_bytes_downloaded - num_bytes_downloaded)
                        num_bytes_downloaded = r.num_bytes_downloaded

        return filename

    def get_chapter_info(self, asin: str):
        """
        Fetch chapter information from Audible API.

        Args:
            asin: Book ASIN

        Returns:
            Chapter info dictionary or None if not available
        """
        try:
            url = f"content/{asin}/metadata"
            response = self.audible.client.get(url, params={"response_groups": "chapter_info"})
            return response.get("content_metadata", {}).get("chapter_info")
        except Exception as e:
            logger.warning("Could not fetch chapter info for %s: %s", asin, e)
            return None

    def download_book(self, book, folder: str):
        asin = book[0]
        title = book[1]
        safe_title = sanitize_filename(title, fallback=asin)
        lr = self.get_license_response(asin, quality="High")

        if lr is None:
            logger.error("Unable to download book: %s", title)
            return

        # Get the download link
        dl_link = Downloader.get_download_link(lr)

        # Determine Filename and create parent folder
        filename = temp_book_folder(folder, asin, title) / f"{safe_title}.aaxc"
        filename.parent.mkdir(parents=True, exist_ok=True)

        # Download the file
        status = Downloader.download_file(dl_link, filename)

        # save voucher
        voucher_file = filename.with_suffix(".json")
        decrypted_voucher = decrypt_voucher_from_licenserequest(self.audible.auth, lr)
        voucher_file.write_text(json.dumps(decrypted_voucher, indent=4))

        # Fetch chapter information from separate API endpoint
        chapters = None
        chapter_info = self.get_chapter_info(asin)
        if chapter_info:
            chapters = chapter_info.get("chapters", [])
            if chapters:
                logger.info("Fetched %d chapters for %s", len(chapters), title)

        return {"book": status, "voucher": voucher_file, "chapters": chapters}

    def download_pdf(self, asin: str, output_path: str) -> bool:
        """Download PDF companion file if available"""
        try:
            # Get the domain from the auth object (defaults to 'com')
            domain = getattr(self.audible.auth.locale, "domain", "com")
            url = f"https://www.audible.{domain}/companion-file/{asin}"

            logger.info("Downloading PDF for %s", asin)

            # Use the authenticated client session (has auth headers built-in)
            with self.audible.client.session.stream("GET", url, follow_redirects=True) as r:
                # Check if PDF exists (200 status)
                if r.status_code != 200:
                    logger.info("No PDF available for %s (HTTP %d)", asin, r.status_code)
                    return False

                # Check content type to ensure we got a PDF and not HTML login page
                content_type = r.headers.get("content-type", "").lower()
                if "pdf" not in content_type and "application/octet-stream" not in content_type:
                    logger.warning("Received non-PDF content for %s: %s", asin, content_type)
                    return False

                # Get content length if available
                total = int(r.headers.get("Content-Length", 0))

                if total > 0:
                    with tqdm(total=total, unit_scale=True, unit_divisor=1024, unit="B", desc="PDF") as progress:
                        num_bytes_downloaded = r.num_bytes_downloaded
                        with open(output_path, "wb") as f:
                            for chunk in r.iter_bytes():
                                f.write(chunk)
                                progress.update(r.num_bytes_downloaded - num_bytes_downloaded)
                                num_bytes_downloaded = r.num_bytes_downloaded
                else:
                    # No content length header, download without progress
                    with open(output_path, "wb") as f:
                        for chunk in r.iter_bytes():
                            f.write(chunk)

            logger.info("PDF downloaded: %s", output_path)
            return True

        except Exception as e:
            logger.error("Error downloading PDF for %s: %s", asin, e)
            return False

    def download_cover(self, cover_url: str, output_path: str) -> bool:
        """Download high-resolution cover image"""
        try:
            logger.info("Downloading cover image")

            # Use the authenticated client session for consistency
            with self.audible.client.session.stream("GET", cover_url, follow_redirects=True) as r:
                if r.status_code != 200:
                    logger.error("Failed to download cover: HTTP %d", r.status_code)
                    return False

                total = int(r.headers.get("Content-Length", 0))

                if total > 0:
                    with tqdm(total=total, unit_scale=True, unit_divisor=1024, unit="B", desc="Cover") as progress:
                        num_bytes_downloaded = r.num_bytes_downloaded
                        with open(output_path, "wb") as f:
                            for chunk in r.iter_bytes():
                                f.write(chunk)
                                progress.update(r.num_bytes_downloaded - num_bytes_downloaded)
                                num_bytes_downloaded = r.num_bytes_downloaded
                else:
                    with open(output_path, "wb") as f:
                        for chunk in r.iter_bytes():
                            f.write(chunk)

            logger.info("Cover downloaded: %s", output_path)
            return True

        except Exception as e:
            logger.error("Error downloading cover: %s", e)
            return False

    def download_annotations(self, asin: str, output_path: str) -> bool:
        """Download user annotations and bookmarks"""
        try:
            logger.info("Downloading annotations for %s", asin)
            url = "https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar"
            params = {"type": "AUDI", "key": asin}

            # Use the authenticated client (automatically parses JSON responses)
            annotations_data = self.audible.client.get(url, params=params)

            # Only save if there are actual annotations
            if annotations_data and (annotations_data.get("clips") or annotations_data.get("bookmarks")):
                with open(output_path, "w") as f:
                    json.dump(annotations_data, f, indent=2)
                logger.info("Annotations downloaded: %s", output_path)
                return True
            else:
                logger.info("No annotations found for %s", asin)
                return False

        except Exception as e:
            logger.error("Error downloading annotations for %s: %s", asin, e)
            return False


def generate_metadata(book_data: tuple) -> dict:
    """
    Generate comprehensive metadata dictionary from book data tuple.

    Args:
        book_data: Book tuple from database (indices as per database schema)

    Returns:
        Dictionary with metadata fields for FFmpeg
    """
    asin = book_data[0]
    title = book_data[1]
    subtitle = book_data[2]
    authors = json.loads(book_data[3]) if book_data[3] else []
    narrators = json.loads(book_data[4]) if book_data[4] else []
    series = json.loads(book_data[5]) if book_data[5] else []
    genres = json.loads(book_data[6]) if book_data[6] else []
    release_date = book_data[11]

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


def write_ffmpeg_metadata_file(metadata: dict, output_path: str, chapters: list | None = None) -> str:
    """
    Write metadata and optional chapters to FFmpeg FFMETADATA format file.

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
            # Escape special characters for FFmpeg metadata format
            escaped_value = (
                str(value)
                .replace("\\", "\\\\")
                .replace("\n", "\\n")
                .replace("=", "\\=")
                .replace(";", "\\;")
                .replace("#", "\\#")
            )
            f.write(f"{key}={escaped_value}\n")

        # Write chapters if provided
        if chapters:
            f.write("\n")
            for chapter in chapters:
                start_ms = chapter.get("start_offset_ms", 0)
                length_ms = chapter.get("length_ms", 0)
                end_ms = start_ms + length_ms
                title = chapter.get("title", "Chapter")

                # Escape title for FFmpeg
                escaped_title = (
                    str(title)
                    .replace("\\", "\\\\")
                    .replace("\n", "\\n")
                    .replace("=", "\\=")
                    .replace(";", "\\;")
                    .replace("#", "\\#")
                )

                f.write("[CHAPTER]\n")
                f.write("TIMEBASE=1/1000\n")
                f.write(f"START={start_ms}\n")
                f.write(f"END={end_ms}\n")
                f.write(f"title={escaped_title}\n")
                f.write("\n")

    logger.debug("Wrote FFmpeg metadata file: %s", output_path)
    return output_path


def decrypt_aaxc(
    book: str,
    voucher: str,
    book_data: tuple | None = None,
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
        book_data: Optional book data tuple from database for metadata generation
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

    metadata_file = f"{book}.ffmetadata"
    if encoding_format == "oga":
        cmd, metadata_file = _opus_ffmpeg_args(
            book, key, iv, output_file, metadata_file, bitrate, book_data, cover_path, chapters
        )
    else:
        cmd, metadata_file = _m4b_ffmpeg_args(
            book, key, iv, output_file, metadata_file, book_data, cover_path, chapters
        )

    # Run the command
    logger.debug("FFmpeg command: %s", " ".join(str(x) for x in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error("FFmpeg error: %s", result.stderr)
        raise Exception(f"FFmpeg conversion failed: {result.stderr}")

    # Cleanup temporary metadata file
    if metadata_file and Path(metadata_file).exists():
        Path(metadata_file).unlink()
        logger.debug("Cleaned up metadata file: %s", metadata_file)

    logger.info("Conversion complete: %s", output_file)
    return output_file


def _m4b_ffmpeg_args(
    book: str,
    key: str,
    iv: str,
    output_file: str,
    metadata_file: str,
    book_data: tuple | None,
    cover_path: str | None,
    chapters: list | None,
) -> tuple[list[str], str | None]:
    """
    Build the ffmpeg command for an M4B stream copy. The cover is mapped as an
    attached picture and chapters come from the FFMETADATA file.

    Returns:
        The command and the metadata file path (None if none was written)
    """
    # Generate and write metadata (with chapters) if book_data is provided
    if book_data:
        metadata = generate_metadata(book_data)
        write_ffmpeg_metadata_file(metadata, metadata_file, chapters=chapters)
        if chapters:
            logger.info("Metadata file with %d chapters created: %s", len(chapters), metadata_file)
        else:
            logger.info("Metadata file created: %s", metadata_file)
    else:
        metadata_file = None

    # Build ffmpeg command using subprocess for precise control
    # Note: ffmpeg-python library has issues with map_metadata/map_chapters
    # See: https://github.com/kkroening/ffmpeg-python/issues/463
    cmd = ["ffmpeg", "-y"]

    # Add decryption keys and primary audio input
    cmd.extend(["-audible_key", key, "-audible_iv", iv, "-i", book])

    # Add cover image as second input if provided
    if cover_path and Path(cover_path).exists():
        cmd.extend(["-i", cover_path])
        logger.info("Embedding cover art from: %s", cover_path)

    # Add metadata file as input if generated (already includes chapters)
    if metadata_file and Path(metadata_file).exists():
        cmd.extend(["-i", metadata_file])

    # Map audio stream from first input
    cmd.extend(["-map", "0:a"])

    # Map cover as attached picture if provided
    if cover_path and Path(cover_path).exists():
        cmd.extend(["-map", "1:v"])
        cmd.extend(["-c:v", "copy"])
        cmd.extend(["-disposition:v", "attached_pic"])

    # Determine input index for metadata file
    # Input 0: audio (AAXC)
    # Input 1: cover (if present)
    # Input 2 or 1: metadata file (if present, includes chapters)
    input_idx = 1
    if cover_path and Path(cover_path).exists():
        input_idx += 1

    # Map metadata and chapters from the same metadata file
    if metadata_file and Path(metadata_file).exists():
        cmd.extend(["-map_metadata", str(input_idx)])
        cmd.extend(["-map_chapters", str(input_idx)])

    # Audio codec and other options
    cmd.extend(["-c:a", "copy"])
    cmd.extend(["-dn"])

    # Add output file
    cmd.append(output_file)
    return cmd, metadata_file


def _opus_ffmpeg_args(
    book: str,
    key: str,
    iv: str,
    output_file: str,
    metadata_file: str,
    bitrate: int,
    book_data: tuple | None,
    cover_path: str | None,
    chapters: list | None,
) -> tuple[list[str], str | None]:
    """
    Build the ffmpeg command for an Ogg Opus re-encode. Ogg has no attached picture
    stream or chapter track, so the cover (METADATA_BLOCK_PICTURE) and chapters
    (CHAPTERxxx) travel as plain tags in the FFMETADATA file, and any chapters in the
    AAXC itself are dropped with ``-map_chapters -1`` so they are not duplicated.

    Returns:
        The command and the metadata file path (None if none was written)
    """
    tags = generate_metadata(book_data) if book_data else {}

    if chapters:
        tags.update(chapter_tags(chapters))
        logger.info("Embedding %d chapters as vorbis comments", len(chapters))

    if cover_path and Path(cover_path).exists():
        tags["METADATA_BLOCK_PICTURE"] = picture_block(cover_path)
        logger.info("Embedding cover art as METADATA_BLOCK_PICTURE from: %s", cover_path)

    if tags:
        write_ffmpeg_metadata_file(tags, metadata_file)
        logger.info("Metadata file created: %s", metadata_file)
    else:
        metadata_file = None

    cmd = ["ffmpeg", "-y"]
    cmd.extend(["-audible_key", key, "-audible_iv", iv, "-i", book])
    if metadata_file:
        cmd.extend(["-i", metadata_file])
    cmd.extend(["-map", "0:a"])
    if metadata_file:
        cmd.extend(["-map_metadata", "1"])
    cmd.extend(["-map_chapters", "-1"])
    cmd.extend(["-c:a", "libopus", "-b:a", f"{bitrate}k", "-vbr", "on"])
    cmd.extend(["-dn"])
    cmd.append(output_file)
    return cmd, metadata_file


def _process_book(
    downloader: Downloader,
    book: tuple,
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
        book: Book tuple from the database
        temp_dir: Temporary working folder for this book (created by download_book)
        audiobook_folder: Root folder for the final organised library
        folder_template: Naming template for the book folder (see src.naming)
        filename_template: Naming template for the file name without extension
        encoding_format: Output format (see src.encoding.FORMATS)
        bitrate: Opus bitrate in kbps, only used for oga
    """
    asin = book[0]
    title = book[1]
    cover_url = book[12]
    has_pdf = book[17]  # Index for has_pdf field
    safe_title = sanitize_filename(title, fallback=asin)

    # Download the Book
    logger.info("Downloading %s", title)
    download = downloader.download_book(book, str(temp_dir.parent))
    if download is None:
        raise RuntimeError(f"Could not obtain a download license for {title} ({asin})")
    logger.info("Download complete")
    logger.debug("Book: %s", download["book"])
    logger.debug("Voucher: %s", download["voucher"])

    # Download accessories before decryption so we can embed cover
    pdf_path = None
    temp_cover_path = None
    annotations_path = None

    # Download PDF if available
    if has_pdf:
        temp_pdf = temp_dir / f"{safe_title}.pdf"
        if downloader.download_pdf(asin, str(temp_pdf)):
            pdf_path = str(temp_pdf)

    # Download high-resolution cover for embedding
    if cover_url:
        # Determine file extension from URL (usually .jpg)
        cover_ext = ".jpg"
        if ".png" in cover_url.lower():
            cover_ext = ".png"
        temp_cover = temp_dir / f"{safe_title}_cover{cover_ext}"
        if downloader.download_cover(cover_url, str(temp_cover)):
            temp_cover_path = str(temp_cover)

    # Download annotations
    temp_annotations = temp_dir / f"{safe_title}_annotations.json"
    if downloader.download_annotations(asin, str(temp_annotations)):
        annotations_path = str(temp_annotations)

    # Decrypt the Book with metadata, cover art, and chapters embedded
    logger.info("Decrypting and embedding metadata")
    chapters_data = download.get("chapters")
    audiobook = decrypt_aaxc(
        download["book"],
        download["voucher"],
        book_data=book,
        cover_path=temp_cover_path,
        chapters=chapters_data,
        encoding_format=encoding_format,
        bitrate=bitrate,
    )

    # Determine final location from the naming templates (all path segments sanitized)
    final_folder, stem = book_output_paths(book, audiobook_folder, folder_template, filename_template)
    final_folder.mkdir(parents=True, exist_ok=True)

    # Move the Book to final location
    to_path = final_folder / f"{stem}{output_extension(encoding_format)}"
    shutil.copy(audiobook, to_path)
    logger.info("Book copied to %s", to_path)

    # Move accessories to final location
    final_pdf_path = None
    final_cover_path = None
    final_annotations_path = None

    # Move PDF to final location
    if pdf_path and Path(pdf_path).exists():
        final_pdf = final_folder / f"{stem}.pdf"
        shutil.move(pdf_path, final_pdf)
        final_pdf_path = str(final_pdf)
        logger.info("PDF moved to %s", final_pdf)

    # Move high-resolution cover to final location (separate from embedded cover)
    if temp_cover_path and Path(temp_cover_path).exists():
        cover_ext = Path(temp_cover_path).suffix
        final_cover = final_folder / f"{stem}_cover{cover_ext}"
        shutil.move(temp_cover_path, final_cover)
        final_cover_path = str(final_cover)
        logger.info("Cover moved to %s", final_cover)

    # Move annotations to final location
    if annotations_path and Path(annotations_path).exists():
        final_annotations = final_folder / f"{stem}_annotations.json"
        shutil.move(annotations_path, final_annotations)
        final_annotations_path = str(final_annotations)
        logger.info("Annotations moved to %s", final_annotations)

    # Update database with accessory paths
    update_book_accessories(
        asin, pdf_path=final_pdf_path, cover_path=final_cover_path, annotations_path=final_annotations_path
    )

    # Mark Book downloaded, recording how it was encoded
    mark_book_downloaded(asin, encoding_format=encoding_format)


def download_books(
    audible,
    download_folder,
    audiobook_folder,
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
    keeps its 'waiting_download' status so it is retried on the next run.

    `folder_template` and `filename_template` control where each book is filed
    under `audiobook_folder` (see src.naming for the placeholder syntax).
    `encoding_format` selects m4b (stream copy) or oga (Opus at `bitrate` kbps).
    """
    waiting_download = get_books_to_download()
    total_to_download = len(waiting_download)
    number_to_download: int = max if max is not None else total_to_download

    logger.info(
        "Downloading %d books of %d waiting download as %s", number_to_download, total_to_download, encoding_format
    )

    loop = waiting_download[0 : int(number_to_download)]
    downloader = Downloader(audible)
    succeeded = 0
    failed = []

    for book in loop:
        asin = book[0]
        title = book[1]
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
