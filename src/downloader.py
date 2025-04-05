import json
import pathlib
import httpx
from src.audible import Audible
from audible.aescipher import decrypt_voucher_from_licenserequest

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
            print(f"download link now: {dl_link}")
            status = Downloader.download_file(dl_link, filename)
            print(f"downloaded file: {status} to {filename}")

            # save voucher
            voucher_file = filename.with_suffix(".json")
            decrypted_voucher = decrypt_voucher_from_licenserequest(self.audible.auth, lr)
            voucher_file.write_text(json.dumps(decrypted_voucher, indent=4))
            print(f"saved voucher to: {voucher_file}")