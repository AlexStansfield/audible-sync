import json
import logging
from pathlib import Path
import shutil
import httpx
import ffmpeg
from tqdm import tqdm
from src.audible import Audible
from audible.aescipher import decrypt_voucher_from_licenserequest
from src.database import get_books_to_download, mark_book_downloaded, update_book_accessories

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

    def get_download_link(license_response):
        return license_response["content_license"]["content_metadata"]["content_url"][
            "offline_url"
        ]

    def download_file(url, filename):
        headers = {"User-Agent": "Audible/671 CFNetwork/1240.0.4 Darwin/20.6.0"}
        with httpx.stream("GET", url, headers=headers) as r:
            total = int(r.headers["Content-Length"])

            with tqdm(total=total, unit_scale=True, unit_divisor=1024, unit="B") as progress:
                num_bytes_downloaded = r.num_bytes_downloaded
                with open(filename, "wb") as f:
                    for chunck in r.iter_bytes():
                        f.write(chunck)
                        progress.update(r.num_bytes_downloaded - num_bytes_downloaded)
                        num_bytes_downloaded = r.num_bytes_downloaded

        return filename

    def download_book(self, book, folder: str):
        asin = book[0]
        title = book[1]
        book_folder = f"{asin}_{title}"
        lr = self.get_license_response(asin, quality="High")

        if lr == None:
            logger.error("Unable to download book: %s", title)
            return

        # Get the download link
        dl_link = Downloader.get_download_link(lr)

        # Determine Filename and create parent folder
        filename = Path("{0}/{1}/{2}.aaxc".format(folder, book_folder, title))
        filename.parent.mkdir(parents=True, exist_ok=True)

        # Download the file
        status = Downloader.download_file(dl_link, filename)

        # save voucher
        voucher_file = filename.with_suffix(".json")
        decrypted_voucher = decrypt_voucher_from_licenserequest(self.audible.auth, lr)
        voucher_file.write_text(json.dumps(decrypted_voucher, indent=4))

        return {"book": status, "voucher": voucher_file}

    def download_pdf(self, asin: str, output_path: str) -> bool:
        """Download PDF companion file if available"""
        try:
            # Get the domain from the auth object (defaults to 'com')
            domain = getattr(self.audible.auth.locale, 'domain', 'com')
            url = f"https://www.audible.{domain}/companion-file/{asin}"

            logger.info("Downloading PDF for %s", asin)

            # Use the authenticated client session (has auth headers built-in)
            with self.audible.client.session.stream("GET", url, follow_redirects=True) as r:
                # Check if PDF exists (200 status)
                if r.status_code != 200:
                    logger.info("No PDF available for %s (HTTP %d)", asin, r.status_code)
                    return False

                # Check content type to ensure we got a PDF and not HTML login page
                content_type = r.headers.get('content-type', '').lower()
                if 'pdf' not in content_type and 'application/octet-stream' not in content_type:
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
    metadata['title'] = full_title
    metadata['album'] = full_title

    # Authors (primary artist)
    if authors:
        metadata['artist'] = "; ".join(authors)
        metadata['album_artist'] = "; ".join(authors)
        metadata['author'] = "; ".join(authors)

    # Narrators (composer field often used for narrators in audiobooks)
    if narrators:
        metadata['composer'] = "; ".join(narrators)

    # Series information
    if series:
        series_info = series[0]
        metadata['series'] = series_info.get('title', '')
        metadata['series-part'] = series_info.get('sequence', '')

    # Genre
    if genres:
        metadata['genre'] = "; ".join(genres)

    # Release date (year)
    if release_date:
        try:
            # Try to extract year from release_date string
            year = release_date.split('-')[0] if '-' in release_date else release_date[:4]
            metadata['date'] = year
        except:
            pass

    # ASIN as comment for reference
    metadata['comment'] = f"ASIN: {asin}"

    # Media type
    metadata['media_type'] = 'audiobook'

    logger.debug("Generated metadata: %s", metadata)
    return metadata


def write_ffmpeg_metadata_file(metadata: dict, output_path: str) -> str:
    """
    Write metadata to FFmpeg FFMETADATA format file.

    Args:
        metadata: Dictionary of metadata key-value pairs
        output_path: Path where metadata file should be written

    Returns:
        Path to the created metadata file
    """
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(';FFMETADATA1\n')
        for key, value in metadata.items():
            # Escape special characters for FFmpeg metadata format
            escaped_value = str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('=', '\\=').replace(';', '\\;').replace('#', '\\#')
            f.write(f'{key}={escaped_value}\n')

    logger.debug("Wrote FFmpeg metadata file: %s", output_path)
    return output_path

def decrypt_aaxc(book: str, voucher: str, book_data: tuple = None, cover_path: str = None):
    """
    Decrypt AAXC audiobook file to M4B format with optional metadata and cover art.

    Args:
        book: Path to the AAXC file
        voucher: Path to the voucher JSON file
        book_data: Optional book data tuple from database for metadata generation
        cover_path: Optional path to cover image to embed

    Returns:
        Path to the output M4B file
    """
    output_file = f"{book}.m4b"

    # Load key and iv from .voucher JSON
    with open(voucher, 'r') as f:
        voucher_data = json.load(f)

    key = voucher_data['key']
    iv = voucher_data['iv']

    # Generate and write metadata if book_data is provided
    metadata_file = None
    if book_data:
        metadata = generate_metadata(book_data)
        metadata_file = f"{book}.ffmetadata"
        write_ffmpeg_metadata_file(metadata, metadata_file)
        logger.info("Metadata file created: %s", metadata_file)

    # Build ffmpeg command using ffmpeg-python library
    # Create primary audio input with decryption keys
    audio_input = ffmpeg.input(book, audible_key=key, audible_iv=iv)

    # Collect all inputs and build output options
    inputs = [audio_input]
    output_kwargs = {
        'c:a': 'copy',      # Copy audio stream without re-encoding
        'dn': None,         # Discard data streams
        'loglevel': 'warning',
        'y': None           # Overwrite output file if exists
    }

    # Build map list for stream selection
    stream_maps = ['0:a']  # Map audio from first input (AAXC file)

    # Add cover art if provided
    if cover_path and Path(cover_path).exists():
        cover_input = ffmpeg.input(cover_path)
        inputs.append(cover_input)
        stream_maps.append('1:v')  # Map video (cover) from second input
        output_kwargs['c:v'] = 'copy'  # Copy cover without re-encoding
        output_kwargs['disposition:v'] = 'attached_pic'  # Mark as attached picture
        logger.info("Embedding cover art from: %s", cover_path)

    # Add metadata file if generated
    if metadata_file and Path(metadata_file).exists():
        metadata_input = ffmpeg.input(metadata_file, f='ffmetadata')
        inputs.append(metadata_input)
        # Map metadata from last input (index depends on whether cover was added)
        metadata_index = len(inputs) - 1
        output_kwargs['map_metadata'] = str(metadata_index)

    # Set the map option
    output_kwargs['map'] = stream_maps

    # Create the output stream with all inputs and options
    stream = ffmpeg.output(*inputs, output_file, **output_kwargs)

    # Run the ffmpeg command
    try:
        ffmpeg.run(stream, capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        logger.error("FFmpeg error: %s", e.stderr.decode() if e.stderr else str(e))
        raise Exception(f"FFmpeg conversion failed: {e.stderr.decode() if e.stderr else str(e)}")

    # Cleanup metadata file
    if metadata_file and Path(metadata_file).exists():
        Path(metadata_file).unlink()
        logger.debug("Cleaned up metadata file: %s", metadata_file)

    logger.info("Conversion complete: %s", output_file)
    return output_file


def download_books(audible, download_folder, audiobook_folder, max:int=None):
    waiting_download = get_books_to_download();
    total_to_download = len(waiting_download)
    number_to_download:int = max if max != None else total_to_download

    logger.info("Downloading %d books of %d waiting download", number_to_download, total_to_download)

    loop = waiting_download[0:int(number_to_download)]

    for book in loop:
        # Extract book information
        asin = book[0]
        title = book[1]
        cover_url = book[12]
        has_pdf = book[17]  # Index for has_pdf field

        # Download the Book
        logger.info("Downloading %s", title)
        downloader = Downloader(audible)
        download = downloader.download_book(book, download_folder)
        logger.info("Download complete")
        logger.debug("Book: %s", download['book'])
        logger.debug("Voucher: %s", download['voucher'])

        # Download accessories before decryption so we can embed cover
        pdf_path = None
        temp_cover_path = None
        annotations_path = None

        # Create temporary directory for accessories
        temp_dir = Path(download['book']).parent

        # Download PDF if available
        if has_pdf:
            temp_pdf = temp_dir / f"{title}.pdf"
            if downloader.download_pdf(asin, str(temp_pdf)):
                pdf_path = str(temp_pdf)

        # Download high-resolution cover for embedding
        if cover_url:
            # Determine file extension from URL (usually .jpg)
            cover_ext = ".jpg"
            if ".png" in cover_url.lower():
                cover_ext = ".png"
            temp_cover = temp_dir / f"{title}_cover{cover_ext}"
            if downloader.download_cover(cover_url, str(temp_cover)):
                temp_cover_path = str(temp_cover)

        # Download annotations
        temp_annotations = temp_dir / f"{title}_annotations.json"
        if downloader.download_annotations(asin, str(temp_annotations)):
            annotations_path = str(temp_annotations)

        # Decrypt the Book with metadata and cover art embedded
        logger.info("Decrypting and embedding metadata")
        audiobook = decrypt_aaxc(download['book'], download['voucher'], book_data=book, cover_path=temp_cover_path)

        # Determine final location
        series = json.loads(book[5])
        authors = json.loads(book[3])
        if len(series) > 0:
            final_folder = Path("{0}/{1}/{2}/{3} - {4}".format(audiobook_folder, authors[0], series[0]['title'], series[0]['sequence'], title))
        else:
            final_folder = Path("{0}/{1}/{2}".format(audiobook_folder, authors[0], title))
        final_folder.mkdir(parents=True, exist_ok=True)

        # Move the Book to final location
        to_path = final_folder / f"{title}.m4b"
        shutil.copy(audiobook, to_path)
        logger.info("Book copied to %s", to_path)

        # Move accessories to final location
        final_pdf_path = None
        final_cover_path = None
        final_annotations_path = None

        # Move PDF to final location
        if pdf_path and Path(pdf_path).exists():
            final_pdf = final_folder / f"{title}.pdf"
            shutil.move(pdf_path, final_pdf)
            final_pdf_path = str(final_pdf)
            logger.info("PDF moved to %s", final_pdf)

        # Move high-resolution cover to final location (separate from embedded cover)
        if temp_cover_path and Path(temp_cover_path).exists():
            cover_ext = Path(temp_cover_path).suffix
            final_cover = final_folder / f"{title}_cover{cover_ext}"
            shutil.move(temp_cover_path, final_cover)
            final_cover_path = str(final_cover)
            logger.info("Cover moved to %s", final_cover)

        # Move annotations to final location
        if annotations_path and Path(annotations_path).exists():
            final_annotations = final_folder / f"{title}_annotations.json"
            shutil.move(annotations_path, final_annotations)
            final_annotations_path = str(final_annotations)
            logger.info("Annotations moved to %s", final_annotations)

        # Update database with accessory paths
        update_book_accessories(asin, pdf_path=final_pdf_path, cover_path=final_cover_path, annotations_path=final_annotations_path)

        # Cleanup temporary download folder
        cleanup_folder = Path(audiobook).parent
        shutil.rmtree(cleanup_folder)

        # Mark Book downloaded
        mark_book_downloaded(asin)

    logger.info("Completed downloads")

