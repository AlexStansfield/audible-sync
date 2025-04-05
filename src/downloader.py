import json
import pathlib
import httpx
import ffmpeg
from src.audible import Audible
from audible.aescipher import decrypt_voucher_from_licenserequest
from src.database import get_books_to_download, mark_book_downloaded

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
            print(f"Error: {e}")
            return

    def get_download_link(license_response):
        return license_response["content_license"]["content_metadata"]["content_url"][
            "offline_url"
        ]

    def download_file(url, filename):
        headers = {"User-Agent": "Audible/671 CFNetwork/1240.0.4 Darwin/20.6.0"}
        with httpx.stream("GET", url, headers=headers) as r:
            with open(filename, "wb") as f:
                for chunck in r.iter_bytes():
                    f.write(chunck)
        return filename

    def download_book(self, book):
        asin = book[0]
        title = book[1] + f"({asin}).aaxc"
        lr = self.get_license_response(asin, quality="High")

        if lr:
            # download book
            dl_link = Downloader.get_download_link(lr)
            filename = pathlib.Path.cwd() / "audiobooks" / title
            status = Downloader.download_file(dl_link, filename)

            # save voucher
            voucher_file = filename.with_suffix(".json")
            decrypted_voucher = decrypt_voucher_from_licenserequest(self.audible.auth, lr)
            voucher_file.write_text(json.dumps(decrypted_voucher, indent=4))

            return {"book": status, "voucher": voucher_file}

def decrypt_aaxc_to_m4b(input_file: str, voucher: str):
    input_path = pathlib.Path(input_file)
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

    print(f"Conversion complete: {output_file}")

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

    print(f"Conversion complete: {output_file}")


def download_books(audible, max:int=None):
    waiting_download = get_books_to_download();
    total_to_download = len(waiting_download)
    number_to_download:int = max if max != None else total_to_download

    print("Downloading {0} books of {1} waiting download".format(number_to_download, total_to_download))
    
    loop = waiting_download[0:int(number_to_download)]

    for book in loop:
        print("Downloading {0}".format(book[1]))
        downloader = Downloader(audible)
        download = downloader.download_book(book)
        print("Download complete")
        print("Book: {0}".format(download['book']))
        print("Voucher: {0}".format(download['voucher']))
        decrypt_aaxc(download['book'], download['voucher'])
        mark_book_downloaded(book[0])

    print("Completed downloads")

