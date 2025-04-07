import json
from pathlib import Path
import shutil
import httpx
import ffmpeg
import logging
from tqdm import tqdm
from src.audible import Audible
from audible.aescipher import decrypt_voucher_from_licenserequest
from src.database import get_books_to_download, mark_book_downloaded

class Downloader:
    def __init__(self, audible: Audible):
        self.audible = audible
        self.logger = logging.getLogger("Downloader")

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
            self.logger.debug(f"Error: {e}")
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
            self.logger.info('Unable to download book')
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
    logger = logging.getLogger("coverter")
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

    logger.debug(f"Conversion complete: {output_file}")

def decrypt_aaxc(book: str, voucher: str):
    logger = logging.getLogger("coverter")
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

    logger.debug(f"Conversion complete: {output_file}")
    return output_file


def download_books(audible, download_folder, audiobook_folder, max:int=None):
    logger = logging.getLogger("downloader")
    waiting_download = get_books_to_download();
    total_to_download = len(waiting_download)
    number_to_download:int = max if max != None else total_to_download

    logger.info(f"Downloading {number_to_download} books of {total_to_download} waiting download")
    
    loop = waiting_download[0:int(number_to_download)]

    for book in loop:
        # Download the Book
        logger.info(f"Downloading {book[1]}")
        downloader = Downloader(audible)
        download = downloader.download_book(book, download_folder)
        logger.debug(f"Book: {download['book']}")
        logger.debug(f"Voucher: {download['voucher']}")
        logger.info("Download complete, decypting book")

        # Decrypt the Book
        audiobook = decrypt_aaxc(download['book'], download['voucher'])
        logger.debug(f"Book decrypted to {audiobook}")

        # Move the Book to final location
        logger.debug(f"Determine filename to copy to")
        series = json.loads(book[5])
        authors = json.loads(book[3])
        if len(series) > 0:
            to_path = Path("{0}/{1}/{2}/{3} - {4}/{4}.m4b".format(audiobook_folder, authors[0], series[0]['title'], series[0]['sequence'], book[1]))
        else:
            to_path = Path("{0}/{1}/{2}/{2}.m4b".format(audiobook_folder, authors[0], book[1]))
        to_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("Copy book")
        shutil.copy(audiobook, to_path)
        logger.info("Book saved to {to_path}")

        # Cleanup
        cleanup_folder = Path(audiobook).parent
        shutil.rmtree(cleanup_folder)

        # Mark Book downloaded
        mark_book_downloaded(book[0])

    logging.info("Completed downloads")

