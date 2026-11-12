"""
The wishlists: every account's in one list, adding and removing books, and reading them
again from Audible.

A run reads each account's wishlist after its library, so the list is as of the last
sync unless refreshed. Adding and removing go to Audible first, then to the table.
"""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Response

from audible_sync.api.routes.catalog import Asin, account_for, ask, marked
from audible_sync.api.schemas import (
    Message,
    WishlistAccountOut,
    WishlistAdd,
    WishlistItemOut,
    WishlistOut,
    WishlistRefresh,
)
from audible_sync.catalog import Catalog
from audible_sync.database import WISHLIST_SORT_COLUMNS, get_accounts, list_wishlist, wishlist_reads
from audible_sync.model import Account, WishlistItem
from audible_sync.wishlist import NotInCatalogue, OwnedBook, Wishlists

logger = logging.getLogger(__name__)

# Newest first by date, A to Z by title
_DEFAULT_DESCENDING = {"date_added": True, "title": False, "release_date": True}


def router(*, catalog: Catalog | None = None, wishlists: Wishlists | None = None) -> APIRouter:
    """The wishlist's routes; `catalog` picks the accounts, `wishlists` makes the requests."""
    router = APIRouter()
    catalog = catalog or Catalog()
    wishlists = wishlists or Wishlists(catalog=catalog)

    def items_out(items: list[WishlistItem]) -> list[WishlistItemOut]:
        accounts = {account.id: account for account in get_accounts()}
        # An account removed since the list was read takes its books with it
        items = [item for item in items if item.account_id in accounts]
        out: list[WishlistItemOut] = []
        # Marked per marketplace, as a book's marks are its marketplace's accounts'
        for country in dict.fromkeys(accounts[item.account_id].country_code for item in items):
            group = [item for item in items if accounts[item.account_id].country_code == country]
            out.extend(
                marked(
                    country,
                    [item.book for item in group],
                    WishlistItemOut,
                    [{"account_id": item.account_id, "added_at": item.added_at} for item in group],
                )
            )
        order = {(item.account_id, item.book.asin): index for index, item in enumerate(items)}
        return sorted(out, key=lambda book: order[(book.account_id, book.asin)])

    def account_states() -> list[WishlistAccountOut]:
        reads = wishlist_reads()
        return [
            WishlistAccountOut(
                account_id=account.id,
                needs_login=account.needs_login,
                **reads.get(account.id, {"read_at": None, "error": None, "error_at": None}),
            )
            for account in get_accounts()
        ]

    @router.get("/wishlist", response_model=WishlistOut)
    def get_wishlist(
        account_id: int | None = None,
        sort: Literal["date_added", "title", "release_date"] = "date_added",
        order: Literal["asc", "desc"] | None = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> WishlistOut:
        """
        Every account's wishlist in one list, or one account's, as last read from Audible.

        `sort` is `date_added` (newest first), `title` (A to Z) or `release_date` (newest
        first); `order` turns it round. A book on two accounts' wishlists is two items.
        Each is a catalogue book (marked in a library and on wishlists, as the catalogue's
        are) with its `account_id` and `added_at`. `accounts` says when each account's
        wishlist was last read, and the error of a read that failed since: its books are
        then as of the last read that worked.
        """
        assert sort in WISHLIST_SORT_COLUMNS  # the Literal above is the same set; keep them in step
        if account_id is not None:
            account_for_listing(account_id)
        descending = _DEFAULT_DESCENDING[sort] if order is None else order == "desc"
        items, total = list_wishlist(
            account_id=account_id,
            sort=sort,
            descending=descending,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return WishlistOut(
            items=items_out(items), total=total, page=page, page_size=page_size, accounts=account_states()
        )

    def account_for_listing(account_id: int) -> None:
        if not any(account.id == account_id for account in get_accounts()):
            raise HTTPException(status_code=404, detail=f"No account with id {account_id}")

    @router.post("/wishlist", response_model=WishlistItemOut, status_code=201)
    def add_to_wishlist(body: WishlistAdd, response: Response) -> WishlistItemOut:
        """
        Put a book on an account's wishlist, on Audible and here: `201` when it was not
        on it, `200` when it was (Audible does not say; the table does). `409` for a
        book the account owns, which does not go on its wishlist, or an account that
        needs a sign-in; `404` for an ASIN Audible does not know or an unknown account.
        """
        account = account_for(catalog, body.account_id)
        try:
            item, added = ask(account, lambda: wishlists.add(account, body.asin))
        except OwnedBook as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except NotInCatalogue as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        if not added:
            response.status_code = 200
        return items_out([item])[0]

    @router.delete("/wishlist/{account_id}/{asin}", response_model=Message)
    def remove_from_wishlist(account_id: Annotated[int, Path()], asin: Asin) -> Message:
        """
        Take a book off an account's wishlist, on Audible and here. Audible is told even
        when the book was not on it as last read, so one added on Audible since comes off
        too; `404` then, since as far as the list knew it was not there.
        """
        account = account_for(catalog, account_id)
        removed = ask(account, lambda: wishlists.remove(account, asin))
        if not removed:
            raise HTTPException(status_code=404, detail=f"{asin} was not on {account.name}'s wishlist")
        return Message(status="removed")

    @router.post("/wishlist/refresh", response_model=list[WishlistAccountOut])
    def refresh_wishlists(body: WishlistRefresh | None = None) -> list[WishlistAccountOut]:
        """
        Read the wishlists from Audible again now: one account's (`account_id`) or every
        signed-in account's. A wishlist that cannot be read is not an error here: the
        answer says so per account, as `GET /api/wishlist` does, and the others are read.
        """
        if body is not None and body.account_id is not None:
            accounts: list[Account] = [account_for(catalog, body.account_id)]
        else:
            accounts = [account for account in get_accounts() if not account.needs_login]
        for account in accounts:
            wishlists.refresh(account)
        return account_states()

    return router
