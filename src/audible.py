import audible
from collections.abc import Iterable
from src.model import Book

def _prepare_book(item):
    genres = set([])
    for genre in item["category_ladders"]:
        for ladder in genre["ladder"]:
            genres.add(ladder["name"])

    series = []
    if isinstance(item["series"], Iterable):
        series = [{"title": item["title"], "sequence": item["sequence"]} for item in item["series"]]
    

    data_row = {
        "asin": item["asin"],
        "title": item["title"],
        "subtitle": item["subtitle"],
        "authors": [author["name"] for author in item["authors"]],
        "narrators": [author["name"] for author in item["narrators"]],
        "series": series,
        "genres": list(genres),
        "length": item["runtime_length_min"],
        "is_finished": item["is_finished"],
        "percent_complete": item["percent_complete"],
        "date_added": item["library_status"]["date_added"],
        "release_date": item["release_date"],
        "cover_url": item["product_images"]["500"]
    }

    return Book(**data_row)

class Audible:
    def __init__(self, auth_file):
        self.auth = audible.Authenticator.from_file(filename=auth_file)
        self.client = audible.Client(self.auth)

    def get_library(self, purchased_after=None):
        params = {
            "response_groups": (
                "contributors, media, price, product_attrs, product_desc, "
                "product_extended_attrs, product_plan_details, product_plans, "
                "rating, sample, sku, series, ws4v, origin, "
                "relationships, review_attrs, categories, badge_types, "
                "category_ladders, claim_code_url, "
                "is_finished, origin_asin, pdf_url, "
                "percent_complete, provided_review"
                ),
            "sort_by": 'PurchaseDate',
            "num_results": 1000
            }
        
        if purchased_after != None:
            params["purchased_after"] = purchased_after

        response = self.client.get("library", params=params)
        
        return [_prepare_book(item) for item in response["items"]]

    def get_book(self, asin):
        response = self.client.get(path=f"library/{asin}", params={
            "response_groups": (
                "contributors, media, price, product_attrs, product_desc, "
                "product_extended_attrs, product_plan_details, product_plans, "
                "rating, sample, sku, series, ws4v, origin, "
                "relationships, review_attrs, categories, badge_types, "
                "category_ladders, claim_code_url, "
                "is_finished, origin_asin, pdf_url, "
                "percent_complete, provided_review"
                )
            })
        
        return _prepare_book(response["item"])
