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

def get_auth():
    return audible.Authenticator.from_file(filename="audible.json")

def get_client():
    auth = get_auth()
    return audible.Client(auth)

def get_library():
    client = get_client()
    response = client.get("library", params={
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
    
    return [_prepare_book(item) for item in response["items"]]

def get_book(asin):
    client = get_client()

    response = client.get(path=f"library/{asin}", params={
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
    
    return _prepare_book(response)

def get_download_link(asin, quality):
    client = get_client()
    data = client.post(
        path=f"content/{asin}/licenserequest",
        body={"drm_type": "Adrm", "consumption_type": "Download", "quality": quality},
    )
    return data["content_license"]["content_metadata"]["content_url"]["offline_url"]