import json
import logging
from pathlib import Path
import shutil
import httpx
import ffmpeg
from tqdm import tqdm
from src.audible import Audible
from audible.aescipher import decrypt_voucher_from_licenserequest
from src.database import get_books_to_download, mark_book_downloaded

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

    def download_pdf(self, asin, folder: Path):
        """
        Download companion PDF file for a book using authenticated httpx client.

        Args:
            asin: The book's ASIN
            folder: Folder to save the PDF in

        Returns:
            Path to downloaded PDF file, or None if no PDF available
        """
        try:
            domain = self.audible.auth.locale.domain
            url = f"https://www.audible.{domain}/companion-file/{asin}"

            # Use authenticated httpx client (not the audible client)
            with httpx.Client(auth=self.audible.auth) as client:
                logger.info("Downloading PDF for %s", asin)
                response = client.get(url, follow_redirects=True)

                # Check if we got HTML (sign-in page) instead of a PDF
                content_type = response.headers.get("content-type", "")
                if "text/html" in content_type:
                    logger.info("No PDF available for %s", asin)
                    return None

                if response.status_code == 404:
                    logger.info("No PDF found for %s", asin)
                    return None

                response.raise_for_status()

                # Save PDF file
                pdf_path = folder / f"{asin}.pdf"
                pdf_path.write_bytes(response.content)
                logger.info("PDF saved to %s", pdf_path)
                return pdf_path

        except Exception as e:
            logger.error("Error downloading PDF for %s: %s", asin, e)
            return None

    def download_annotations(self, asin, folder: Path):
        """
        Download user annotations/bookmarks for a book using the Audible API.

        Args:
            asin: The book's ASIN
            folder: Folder to save the annotations in

        Returns:
            Path to saved annotations JSON file, or None if no annotations available
        """
        try:
            logger.info("Downloading annotations for %s", asin)

            # Use the authenticated audible client
            url = "https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar"
            params = {
                "type": "AUDI",
                "key": asin
            }

            # This uses the authenticated client with proper headers
            annotations = self.audible.client.get(url, params=params)

            # Save annotations as JSON
            annotations_path = folder / f"{asin}_annotations.json"
            annotations_path.write_text(json.dumps(annotations, indent=4))
            logger.info("Annotations saved to %s", annotations_path)
            return annotations_path

        except Exception as e:
            # 403 or 404 usually means no annotations available
            if "403" in str(e) or "404" in str(e):
                logger.info("No annotations found for %s", asin)
            else:
                logger.error("Error downloading annotations for %s: %s", asin, e)
            return None

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
        asin = book[0]
        title = book[1]

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
            to_path = Path("{0}/{1}/{2}/{3} - {4}/{4}.m4b".format(audiobook_folder, authors[0], series[0]['title'], series[0]['sequence'], title))
        else:
            to_path = Path("{0}/{1}/{2}/{2}.m4b".format(audiobook_folder, authors[0], title))
        to_path.parent.mkdir(parents=True, exist_ok=True)

        # Move the audiobook to final location
        shutil.copy(audiobook, to_path)
        logger.info("Book copied to %s", to_path)

        # Download PDF companion file (if available)
        pdf_path = downloader.download_pdf(asin, to_path.parent)
        if pdf_path:
            logger.info("PDF companion downloaded")

        # Download annotations (if available)
        annotations_path = downloader.download_annotations(asin, to_path.parent)
        if annotations_path:
            logger.info("Annotations downloaded")

        # Cleanup temporary download folder
        cleanup_folder = Path(audiobook).parent
        shutil.rmtree(cleanup_folder)

        # Mark Book downloaded
        mark_book_downloaded(asin)

    logger.info("Completed downloads")

