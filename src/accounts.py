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
import threading
from typing import Any

from audible import Authenticator

from src.database import (
    add_account,
    get_account,
    get_accounts,
    replace_account_auth_if_unchanged,
    requeue_unavailable,
    save_account_auth,
)
from src.model import Account, StoredLogin

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
    country = (_country_of(auth) or "").upper()
    name = customer_name_of(auth) or "Audible"
    return f"{name} ({country})" if country else name


def add_account_from_authenticator(
    auth: Authenticator, *, name: str | None = None, monitor_existing: bool = True
) -> int:
    """Store a freshly logged-in authenticator as a new account."""
    return add_account(
        name or display_name(auth),
        _country_of(auth) or "",
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


def _country_of(auth: Authenticator) -> str | None:
    return auth.locale.country_code if auth.locale else None


# Two logins finishing together must not both find no account holding theirs and both be
# stored. Within this process only: the customer id is inside the encrypted blob, so the
# database has nothing to enforce it with (todo.md has the column that would).
_store_lock = threading.Lock()


class AccountGone(LookupError):
    """The account a login was for no longer exists."""


class LoginAlreadyHeld(LookupError):
    """Another account already holds this Audible login."""


class AnotherCustomer(LookupError):
    """A login for an account was made as a different Amazon customer from the one it belongs to."""


def replace_account_auth(account: Account, auth: Authenticator, *, deregister_previous: bool = True) -> None:
    """
    Give an existing account new credentials, keeping its id and so its library.

    This is the recovery when a registration stops working - Audible began refusing
    download licences to devices registered with the library's old profile in October
    2026 - without deleting the account and losing which books are downloaded. Once the
    new credentials are stored the previous device is deregistered with Amazon, best
    effort: it is the one that stopped working, so it may well not be able to. Books
    parked `unavailable` because that device was refused a licence are queued again for
    the new one (`requeue_unavailable`). Nothing raises after the store, so a caller that
    sees an exception can discard the new device knowing nothing holds it.

    Raises:
        AccountGone: if the account was deleted while the login was going; the caller
            owns the new device and should deregister it (`audible_login.discard_device`)
        LoginAlreadyHeld: if another account holds this Audible login: one login, one
            account, or both would download and file every book. Likewise the caller's
        AnotherCustomer: if the account's credentials say it belongs to a different Amazon
            customer. Its library is that customer's: its books would be asked for under
            the wrong login, and its cursor would never fetch the new one's. The working
            device is left alone. Likewise the caller's
        ValueError: if `auth` is another marketplace's login. An account's library
            is one marketplace's: credentials for another would leave its books asking
            the wrong store for licences, and its cursor skipping the other's catalogue.
            The migration's placeholder has no marketplace yet and takes any
    """
    with _store_lock:
        holders = _holders(auth)
        if holders and account.id not in {holder.id for holder in holders}:
            raise LoginAlreadyHeld(
                f"{holders[0].name} already holds this Audible login. Sign in on that account, or remove it first"
            )
        previous = _store_auth(account, auth)
    if deregister_previous:
        _deregister_previous(previous)


def _store_auth(account: Account, auth: Authenticator) -> Account:
    """
    Store new credentials for an account and return the account as it was, whose device
    is now held by nothing. The caller deregisters that, outside `_store_lock`: Amazon
    may be slow, and every other login would wait.
    """
    # The account as it is now: a login takes minutes, and the one it was started for may
    # have been removed or given other credentials meanwhile
    current = get_account(account.id)
    if current is None:
        raise AccountGone(f"Account {account.id} ({account.name}) was removed while the login was in progress")
    check_marketplace(current, _country_of(auth))
    stored_customer = _user_id((current.auth or {}).get("customer_info"))
    new_customer = _user_id(auth.customer_info)
    if stored_customer and new_customer and stored_customer != new_customer:
        raise AnotherCustomer(
            f"This is a different Amazon login from the one {current.name} belongs to. "
            "Sign in as that customer, or use Add account to add this login as a new account"
        )
    stored = save_account_auth(
        current.id, auth_to_dict(auth), country_code=_country_of(auth), customer_name=customer_name_of(auth)
    )
    if not stored:
        raise AccountGone(f"Account {current.id} ({current.name}) was removed while the login was in progress")
    logger.info("Replaced the credentials of %r", current)
    # The books the old device was refused licences for get another go with the new
    # one; a title Audible really has retired is refused once more and parks again.
    # Past the store, so a failure here must not read as the login not being kept.
    try:
        requeued = requeue_unavailable(current.id)
    except Exception:
        logger.exception("Could not queue the parked books of %r again; Retry unavailable will", current)
    else:
        if requeued:
            logger.info("Queued %d parked book(s) of %r again for the new device", requeued, current)
    return current


def _deregister_previous(previous: Account) -> None:
    """Deregister the device of credentials just replaced; best effort, never raises."""
    if previous.auth is None:
        return
    try:
        authenticator_for(previous).deregister_device()
        logger.info("Deregistered the previous device of %r", previous)
    except Exception:
        logger.warning("Could not deregister the previous device of %r", previous, exc_info=True)


def _holders(auth: Authenticator) -> list[Account]:
    """
    The accounts already holding this Audible login, oldest first: the same customer in
    the same marketplace. Amazon's `customer_info.user_id` says who it is, and every
    registration carries it. None when the login does not say, and an account whose
    credentials cannot be read (none yet, or under another key) is never one, since
    nothing says whose that is.
    """
    user_id = _user_id(auth.customer_info)
    country_code = _country_of(auth)
    if not user_id or not country_code:
        return []
    return [
        account
        for account in get_accounts()
        if (account.country_code or "").lower() == country_code.lower()
        and account.auth
        and _user_id(account.auth.get("customer_info")) == user_id
    ]


def _user_id(customer_info: dict | None) -> str | None:
    return (customer_info or {}).get("user_id")


def store_login(
    auth: Authenticator,
    *,
    name: str | None = None,
    monitor_existing: bool = True,
    deregister_previous: bool = True,
) -> StoredLogin:
    """
    Store a login that was not started for a particular account: new credentials for the
    account that already holds this Audible login, otherwise a new account.

    One login, one account: a second row would hold the same library and download and file
    every book again. On a match the account keeps its id, library and name, and `name` and
    `monitor_existing` are not used, since they describe an account being created. A match
    removed meanwhile means a new account.

    Raises nothing of its own; a database error means nothing was stored, and the caller
    owns the new device.
    """
    with _store_lock:
        holders = _holders(auth)
        previous = None
        if holders:
            try:
                previous = _store_auth(holders[0], auth)
            except AccountGone:
                logger.info("%r was removed as a login for it arrived; adding a new account", holders[0])
        if previous is None:
            return StoredLogin(add_account_from_authenticator(auth, name=name, monitor_existing=monitor_existing))
    if deregister_previous:
        _deregister_previous(previous)
    return StoredLogin(previous.id, replaced=True)
