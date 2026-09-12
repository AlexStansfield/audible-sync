"""
Accounts: one Audible marketplace login each, credentials kept in the database.

The `audible` library's `Authenticator` is built from and saved as a dict
(`from_dict`/`to_dict`), which is exactly what the `accounts.auth` column holds, so an
account's credentials round-trip without a file. This module is the only thing that
encodes or decodes them; `src.database` just keeps the text.

An account with no credentials (`auth` NULL) is allowed on purpose. The migration that
introduced accounts creates one when it has to rebuild a pre-accounts library and no
auth file is to hand, and a login can later be invalidated; either way the pipeline
skips it and the API reports it as needing a login, rather than the whole service
refusing to start.
"""

import logging
from pathlib import Path
from typing import Any

from audible import Authenticator

from src.database import add_account, get_accounts, save_account_auth, update_account
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
    """
    current = auth_to_dict(auth)
    if current == account.auth:
        return False
    save_account_auth(account.id, current)
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
    """Store a freshly logged-in or imported authenticator as a new account."""
    return add_account(
        name or display_name(auth),
        auth.locale.country_code if auth.locale else "",
        auth=auth_to_dict(auth),
        customer_name=customer_name_of(auth),
        monitor_existing=monitor_existing,
    )


def import_auth_file(path: str | Path, *, name: str | None = None, monitor_existing: bool = True) -> int:
    """
    Create an account from an `audible-cli` style auth file and return its id.

    Raises:
        FileNotFoundError: if there is no such file
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"auth file not found: {path}")
    auth = Authenticator.from_file(path)
    return add_account_from_authenticator(auth, name=name, monitor_existing=monitor_existing)


def ensure_account_from_auth_file(path: str | Path) -> bool:
    """
    Bring an existing installation across: give the configured auth file an account.

    Two cases, both once only. A database with no accounts at all and a file at `path`
    gets its first account from the file. A database whose only account has no
    credentials - the placeholder the library migration creates - has that account
    filled in from the file, keeping its id, so the library rows already pointing at it
    stay attached. Anything else is left alone: once there is a working account, the
    file is no longer the source of truth and the API is.

    Returns:
        Whether an account was created or filled in
    """
    path = Path(path)
    accounts = get_accounts()

    if not accounts:
        if not path.is_file():
            return False
        account_id = import_auth_file(path)
        logger.info("Imported %s as account %d", path, account_id)
        return True

    if len(accounts) == 1 and accounts[0].needs_login and path.is_file():
        auth = Authenticator.from_file(path)
        account = accounts[0]
        update_account(
            account.id,
            name=display_name(auth),
            country_code=auth.locale.country_code if auth.locale else None,
            customer_name=customer_name_of(auth),
        )
        save_account_auth(account.id, auth_to_dict(auth))
        logger.info("Imported %s into account %d", path, account.id)
        return True

    return False
