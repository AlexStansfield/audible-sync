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

def decrypt_aaxc_to_m4b(input_file: str, voucher: str):
    input_path = Path(input_file)
    base_path = input_path.with_suffix('')
    voucher_file = voucher
    metadata_file = f"{input_file}_metadata_new"
    output_file = f"{input_file}.m4b"

    # Load key and iv from .voucher JSON
    with open(voucher_file, 'r') as f:
        voucher_data = json.load(f)

    key = voucher_data['content_license']['license_response']['key']
    iv = voucher_data['content_license']['license_response']['iv']

    # Build ffmpeg input with custom decryption options
    input_args = {
        'audible_key': key,
        'audible_iv': iv
    }

    # Create the ffmpeg command
    (
        ffmpeg
        .input(input_file, **input_args)
        .input(metadata_file)
        .output(
            output_file,
            map='0:a:0',
            c='copy',
            dn=None,
            map_metadata=1,
            map_chapters=1,
            movflags='use_metadata_tags',
            loglevel='warning',
            y=None  # Overwrite output file if it exists
        )
        .run()
    )

    logger.info("Conversion complete: %s", output_file)

def decrypt_aaxc(book: str, voucher: str):
    output_file = f"{book}.m4b"

    # Load key and iv from .voucher JSON
    with open(voucher, 'r') as f:
        voucher_data = json.load(f)
    
    key = voucher_data['key']
    iv = voucher_data['iv']

    # Build ffmpeg command
    (
        ffmpeg
        .input(book, audible_key=key, audible_iv=iv)
        .output(
            output_file,
            map='0:a',
            c='copy',
            dn=None,
            loglevel='warning',
            y=None  # Overwrite existing file
        )
        .run()
    )

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

        # Decrypt the Book
        audiobook = decrypt_aaxc(download['book'], download['voucher'])

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

        # Download accessories
        pdf_path = None
        cover_path = None
        annotations_path = None

        # Download PDF if available
        if has_pdf:
            pdf_file = final_folder / f"{title}.pdf"
            if downloader.download_pdf(asin, str(pdf_file)):
                pdf_path = str(pdf_file)

        # Download high-resolution cover
        if cover_url:
            # Determine file extension from URL (usually .jpg)
            cover_ext = ".jpg"
            if ".png" in cover_url.lower():
                cover_ext = ".png"
            cover_file = final_folder / f"{title}_cover{cover_ext}"
            if downloader.download_cover(cover_url, str(cover_file)):
                cover_path = str(cover_file)

        # Download annotations
        annotations_file = final_folder / f"{title}_annotations.json"
        if downloader.download_annotations(asin, str(annotations_file)):
            annotations_path = str(annotations_file)

        # Update database with accessory paths
        update_book_accessories(asin, pdf_path=pdf_path, cover_path=cover_path, annotations_path=annotations_path)

        # Cleanup temporary download folder
        cleanup_folder = Path(audiobook).parent
        shutil.rmtree(cleanup_folder)

        # Mark Book downloaded
        mark_book_downloaded(asin)

    logger.info("Completed downloads")

