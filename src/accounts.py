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
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from audible import Authenticator

from src.database import (
    add_account,
    customer_id_of,
    get_account,
    get_account_by_customer,
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
        customer_id=customer_id_of(auth.customer_info),
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
# stored. The unique index on `accounts.customer_id` enforces that across processes (a CLI
# login beside the service); this lock makes it an orderly wait within one, rather than a
# refused write.
_store_lock = threading.Lock()


class AccountGone(LookupError):
    """The account a login was for no longer exists."""


class LoginAlreadyHeld(LookupError):
    """Another account already holds this Audible login."""


class AnotherCustomer(LookupError):
    """A login for an account was made as a different Amazon customer from the one it belongs to."""


# The accounts whose device a run is using, and the logins that replaced it meanwhile.
# In memory: a CLI login beside the service cannot see the service's run, and a device
# waiting when the process is killed stays on Amazon's list, in the user's way but harmless.
_devices_lock = threading.Lock()
_devices_in_use: set[int] = set()
_replaced_devices: dict[int, list[tuple[Account, bool]]] = {}


@contextmanager
def device_in_use(account_id: int) -> Iterator[None]:
    """
    Hold off retiring this account's device for as long as a run is using it.

    Enter it **before** reading the account the run will use: a login storing new
    credentials then either finds the mark and waits, or has already stored them and the
    run reads the new ones.

    On leaving, the devices replaced meanwhile are deregistered, if the login asked for
    that, and the account's parked books are queued again. The login queued them once,
    but the run stayed on the old device, which may have been refusing licences all
    along: that is why people sign in again, and the books it parked since would
    otherwise wait for someone to retry them under a device that works.
    """
    with _devices_lock:
        _devices_in_use.add(account_id)
    try:
        yield
    finally:
        with _devices_lock:
            _devices_in_use.discard(account_id)
            replaced = _replaced_devices.pop(account_id, [])
        for previous, deregister in replaced:
            if deregister:
                _deregister_previous(previous)
        if replaced:
            _requeue_parked(account_id, "after the run on its previous device")


def _retire_device(previous: Account, *, deregister: bool) -> None:
    """Deal with the device of credentials just replaced, now or when the run using it ends."""
    with _devices_lock:
        if previous.id in _devices_in_use:
            _replaced_devices.setdefault(previous.id, []).append((previous, deregister))
            if deregister:
                logger.info("A run is using the previous device of %r: deregistering it when the run ends", previous)
            return
    if deregister:
        _deregister_previous(previous)


def _requeue_parked(account_id: int, why: str) -> int:
    """Queue an account's parked books again; a failure is logged, never raised."""
    try:
        requeued = requeue_unavailable(account_id)
    except Exception:
        logger.exception("Could not queue the parked books of account %d again; Retry unavailable will", account_id)
        return 0
    if requeued:
        logger.info("Queued %d parked book(s) of account %d again %s", requeued, account_id, why)
    return requeued


def replace_account_auth(account: Account, auth: Authenticator, *, deregister_previous: bool = True) -> StoredLogin:
    """
    Give an existing account new credentials, keeping its id and so its library.

    This is the recovery when a registration stops working - Audible began refusing
    download licences to devices registered with the library's old profile in October
    2026 - without deleting the account and losing which books are downloaded. Once the
    new credentials are stored the previous device is deregistered with Amazon, best
    effort: it is the one that stopped working, so it may well not be able to. A run on
    this account may still be using it, so then it waits for the run (`device_in_use`). Books
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
        try:
            previous, requeued = _store_auth(account, auth)
        except sqlite3.IntegrityError as error:
            # Another process stored this login on another account since `_store_auth` looked
            raise _already_held(_holder(auth)) from error
    _retire_device(previous, deregister=deregister_previous)
    return StoredLogin(previous.id, replaced=True, requeued=requeued)


def _store_auth(account: Account, auth: Authenticator) -> tuple[Account, int]:
    """
    Store new credentials for an account. Returns the account as it was, whose device is
    now held by nothing, and how many parked books were queued again. The caller
    deregisters that device, outside `_store_lock`: Amazon may be slow, and every other
    login would wait.
    """
    # The account as it is now: a login takes minutes, and the one it was started for may
    # have been removed or given other credentials meanwhile
    current = get_account(account.id)
    if current is None:
        raise AccountGone(f"Account {account.id} ({account.name}) was removed while the login was in progress")
    check_marketplace(current, _country_of(auth))
    # The column, so a customer is known even when the credentials cannot be read
    stored_customer = current.customer_id or customer_id_of((current.auth or {}).get("customer_info"))
    new_customer = customer_id_of(auth.customer_info)
    if stored_customer and new_customer and stored_customer != new_customer:
        raise AnotherCustomer(
            f"This is a different Amazon login from the one {current.name} belongs to. "
            "Sign in as that customer, or use Add account to add this login as a new account"
        )
    holder = _holder(auth)
    if holder is not None and holder.id != current.id:
        # One login, one account. Except a duplicate from before the rule, which
        # `backfill_customer_ids` left without the customer: it may still sign in on its
        # own row, but the customer stays with the account that holds it
        if stored_customer != new_customer:
            raise _already_held(holder)
        new_customer = None
    stored = save_account_auth(
        current.id,
        auth_to_dict(auth),
        country_code=_country_of(auth),
        customer_name=customer_name_of(auth),
        customer_id=new_customer,
    )
    if not stored:
        raise AccountGone(f"Account {current.id} ({current.name}) was removed while the login was in progress")
    logger.info("Replaced the credentials of %r", current)
    # The books the old device was refused licences for get another go with the new
    # one; a title Audible really has retired is refused once more and parks again.
    # Past the store, so a failure here must not read as the login not being kept.
    return current, _requeue_parked(current.id, "for the new device")


def _deregister_previous(previous: Account) -> None:
    """Deregister the device of credentials just replaced; best effort, never raises."""
    if previous.auth is None:
        return
    try:
        authenticator_for(previous).deregister_device()
        logger.info("Deregistered the previous device of %r", previous)
    except Exception:
        logger.warning("Could not deregister the previous device of %r", previous, exc_info=True)


def _holder(auth: Authenticator) -> Account | None:
    """
    The account already holding this Audible login: the same customer in the same
    marketplace. Amazon's `customer_info.user_id` says who it is, and every registration
    carries it. None when the login does not say.

    Read from `accounts.customer_id`, so an account whose credentials cannot be read -
    the key was lost, say - is still found, rather than a login adding a second account
    that downloads the whole library again.
    """
    user_id = customer_id_of(auth.customer_info)
    country_code = _country_of(auth)
    if not user_id or not country_code:
        return None
    return get_account_by_customer(user_id, country_code)


def _already_held(holder: Account | None) -> LoginAlreadyHeld:
    name = holder.name if holder is not None else "Another account"
    return LoginAlreadyHeld(f"{name} already holds this Audible login. Sign in on that account, or remove it first")


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

    A database error means nothing was stored, and the caller owns the new device. So
    does `LoginAlreadyHeld`, raised only when another process keeps adding an account
    for this login as fast as this one looks for it.
    """
    with _store_lock:
        previous = None
        requeued = 0
        # Twice at most: a new account the unique index refuses is one another process
        # added for this login since the first look, and the second look finds it
        for _ in range(2):
            holder = _holder(auth)
            if holder is not None:
                try:
                    previous, requeued = _store_auth(holder, auth)
                    break
                except AccountGone:
                    logger.info("%r was removed as a login for it arrived; adding a new account", holder)
            try:
                return StoredLogin(add_account_from_authenticator(auth, name=name, monitor_existing=monitor_existing))
            except sqlite3.IntegrityError:
                logger.info("Another process added an account for this login meanwhile; storing it there")
        if previous is None:
            raise LoginAlreadyHeld("This Audible login is being stored by another process; try again")
    _retire_device(previous, deregister=deregister_previous)
    return StoredLogin(previous.id, replaced=True, requeued=requeued)
