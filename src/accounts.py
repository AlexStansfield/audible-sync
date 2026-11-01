"""
Accounts: one Audible marketplace login each, credentials kept in the database.

The `audible` library's `Authenticator` is built from and saved as a dict
(`from_dict`/`to_dict`), which is exactly what the `accounts.auth` column holds, so an
account's credentials round-trip without a file. This module is the only thing that
encodes or decodes them; `src.database` just keeps the text.

An account with no credentials (`auth` NULL) is allowed on purpose. The migration that
introduced accounts creates one when it has to rebuild a pre-accounts library, and a
login can later be invalidated; either way the pipeline skips it and the API reports
it as needing a login, rather than the whole service refusing to start. The way in is
always a login (`src.audible_login`): a new account, or `replace_account_auth` for one
that has none or whose registration stopped working.
"""

import logging
from typing import Any

from audible import Authenticator

from src.database import (
    add_account,
    replace_account_auth_if_unchanged,
    requeue_unavailable,
    save_account_auth,
    update_account,
)
from src.model import Account

logger = logging.getLogger(__name__)


def authenticator_for(account: Account) -> Authenticator:
    """
    The `Authenticator` for an account that has credentials.

    Raises:
        ValueError: if the account has none
    """
    if account.auth is None:
        raise ValueError(f"{account!r} has no credentials")
    # `from_dict` pops `locale_code` out of the dict it is given
    return Authenticator.from_dict(dict(account.auth))


def auth_to_dict(auth: Authenticator) -> dict[str, Any]:
    """What the database stores for an authenticator: `to_dict`, which `from_dict` reverses."""
    return auth.to_dict()


def persist_auth_if_changed(account: Account, auth: Authenticator) -> bool:
    """
    Write the credentials back when a run refreshed them.

    `Authenticator` refreshes its access token in memory only; without this the stored
    token would age until every run began with a refresh. Compared as dicts so nothing
    is written when nothing moved.

    `account` is the snapshot the run started from. If the stored credentials are no
    longer the ones in it - a re-login replaced them while the run was going - the
    refreshed old ones are dropped rather than written over the new device.
    """
    current = auth_to_dict(auth)
    if current == account.auth:
        return False
    if not replace_account_auth_if_unchanged(account.id, account.auth, current):
        logger.info("Not storing the refreshed credentials of %r: they were replaced during the run", account)
        return False
    logger.debug("Stored refreshed credentials for %r", account)
    return True


def customer_name_of(auth: Authenticator) -> str | None:
    """The name Amazon returned at registration, if it did."""
    return (auth.customer_info or {}).get("name")


def display_name(auth: Authenticator) -> str:
    """A default account name: the customer's name and the marketplace, e.g. "Alex (UK)"."""
    country = (auth.locale.country_code if auth.locale else "").upper()
    name = customer_name_of(auth) or "Audible"
    return f"{name} ({country})" if country else name


def add_account_from_authenticator(
    auth: Authenticator, *, name: str | None = None, monitor_existing: bool = True
) -> int:
    """Store a freshly logged-in authenticator as a new account."""
    return add_account(
        name or display_name(auth),
        auth.locale.country_code if auth.locale else "",
        auth=auth_to_dict(auth),
        customer_name=customer_name_of(auth),
        monitor_existing=monitor_existing,
    )


def check_marketplace(account: Account, country_code: str | None) -> None:
    """
    Refuse a login for `account` in another marketplace than the one its library is.

    An account with no marketplace (the migration's placeholder) accepts any.

    Raises:
        ValueError: naming both marketplaces
    """
    if account.country_code and country_code and account.country_code.lower() != country_code.lower():
        raise ValueError(
            f"{account.name} is a {account.country_code.upper()} account; "
            f"log in to the {account.country_code.upper()} marketplace, not {country_code.upper()}"
        )


def replace_account_auth(account: Account, auth: Authenticator, *, deregister_previous: bool = True) -> None:
    """
    Give an existing account new credentials, keeping its id and so its library.

    This is the recovery when a registration stops working - Audible began refusing
    download licences to devices registered with the library's old profile in October
    2026 - without deleting the account and losing which books are downloaded. The
    previous device is deregistered with Amazon first, best effort: it is the one that
    stopped working, so it may well not be able to, and the new credentials are stored
    either way. Books parked `unavailable` because that device was refused a licence
    are queued again for the new one (`requeue_unavailable`).

    Raises:
        ValueError: if `auth` is another marketplace's login. An account's library
            is one marketplace's: credentials for another would leave its books asking
            the wrong store for licences, and its cursor skipping the other's catalogue.
            The migration's placeholder has no marketplace yet and takes any
    """
    check_marketplace(account, auth.locale.country_code if auth.locale else None)
    if deregister_previous and account.auth is not None:
        try:
            authenticator_for(account).deregister_device()
            logger.info("Deregistered the previous device of %r", account)
        except Exception:
            logger.warning(
                "Could not deregister the previous device of %r; replacing it anyway", account, exc_info=True
            )
    update_account(
        account.id,
        country_code=auth.locale.country_code if auth.locale else None,
        customer_name=customer_name_of(auth),
    )
    save_account_auth(account.id, auth_to_dict(auth))
    logger.info("Replaced the credentials of %r", account)
    # The books the old device was refused licences for get another go with the new
    # one; a title Audible really has retired is refused once more and parks again.
    requeued = requeue_unavailable(account.id)
    if requeued:
        logger.info("Queued %d parked book(s) of %r again for the new device", requeued, account)
