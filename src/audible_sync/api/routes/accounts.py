"""
Accounts and signing in to Audible: the accounts, the marketplaces and the logins in progress.
"""

import logging
from collections.abc import Callable

import httpx
from audible import Authenticator
from fastapi import APIRouter, HTTPException

from audible_sync.api.schemas import (
    AccountOut,
    AccountUpdate,
    LoginComplete,
    LoginOut,
    LoginStart,
    MarketplaceOut,
    Message,
)
from audible_sync.audible_api.accounts import (
    authenticator_for,
    check_marketplace,
    replace_account_auth,
    store_login,
)
from audible_sync.audible_api.login import (
    MARKETPLACES,
    BrowserLogin,
    CodeLogin,
    LoginFailed,
    LoginNotStored,
    PendingLogins,
)
from audible_sync.database import (
    delete_account,
    get_account,
    get_accounts,
    update_account,
)
from audible_sync.model import Account, StoredLogin
from audible_sync.pipeline.runstate import RunState

logger = logging.getLogger(__name__)


def router(*, state: RunState, logins: PendingLogins) -> APIRouter:
    router = APIRouter()

    @router.get("/accounts", response_model=list[AccountOut])
    def list_accounts() -> list[AccountOut]:
        return get_accounts()

    @router.get("/accounts/{account_id}", response_model=AccountOut)
    def get_one_account(account_id: int) -> AccountOut:
        return account_or_404(account_id)

    @router.patch("/accounts/{account_id}", response_model=AccountOut)
    def patch_account(account_id: int, update: AccountUpdate) -> AccountOut:
        account_or_404(account_id)
        update_account(account_id, **update.model_dump(exclude_unset=True))
        return get_account(account_id)

    @router.delete("/accounts/{account_id}", response_model=Message)
    def remove_account(account_id: int, deregister: bool = False) -> Message:
        """
        Remove an account, its library rows and its run history; files on disk stay.

        With `deregister`, the device this login registered with Amazon is deregistered
        first, so it does not linger on the account's device list. That is best effort:
        a login that no longer works cannot deregister itself, and the account is
        removed either way.

        409 while the run in flight is on this account: its sync would leave rows behind
        for an account that no longer exists, and its download a book on disk with no
        row. An account the run has not reached yet is fine - the run reads each account
        again at its turn and skips one that has gone.
        """
        account = account_or_404(account_id)
        current = state.snapshot()
        if current is not None and (current.get("account") or {}).get("id") == account_id:
            raise HTTPException(status_code=409, detail=f"{account.name} is being synced; cancel the run first")
        if deregister and not account.needs_login:
            try:
                authenticator_for(account).deregister_device()
            except Exception:
                logger.warning("Could not deregister %r with Amazon; removing it anyway", account, exc_info=True)
        delete_account(account_id)
        return Message(status="deleted")

    @router.get("/marketplaces", response_model=list[MarketplaceOut])
    def list_marketplaces() -> list[MarketplaceOut]:
        return list(MARKETPLACES)

    def _finisher(body: LoginStart, existing: Account | None) -> Callable[[Authenticator], StoredLogin]:
        """What a confirmed login does with its credentials: new ones for an account, or a new account."""

        def finish(auth: Authenticator) -> StoredLogin:
            if existing is not None:
                return replace_account_auth(existing, auth, deregister_previous=body.deregister_previous)
            return store_login(
                auth,
                name=body.name,
                monitor_existing=body.monitor_existing,
                deregister_previous=body.deregister_previous,
            )

        return finish

    def _login_out(login: BrowserLogin | CodeLogin) -> LoginOut:
        account = get_account(login.account_id) if login.account_id is not None else None
        return LoginOut(
            login_id=login.id,
            method=login.method,
            status=login.status,
            expires_at=login.expires_at.isoformat(),
            code=login.code if isinstance(login, CodeLogin) else None,
            verification_url=login.verification_url if isinstance(login, CodeLogin) else None,
            url=login.url if isinstance(login, BrowserLogin) else None,
            account=AccountOut.model_validate(account) if account is not None else None,
            replaced=login.replaced,
            requeued=login.requeued,
            error=login.error,
        )

    @router.post("/accounts/login", response_model=LoginOut, status_code=202)
    def start_account_login(body: LoginStart) -> LoginOut:
        """
        Start signing in to a marketplace.

        With the default `code` method the answer carries the `code` to enter at
        `verification_url`, on any device; the service polls Amazon and the login turns
        `confirmed` (with the account) on `GET /api/accounts/login/{login_id}` once the
        user has entered it. With `browser`, open `url`, sign in, and post the "page not
        found" address to `POST /api/accounts/login/{login_id}`. 422 for an unknown
        marketplace or one that is not `account_id`'s, 404 for an unknown `account_id`,
        502 when Amazon will not issue a code.
        """
        existing = account_or_404(body.account_id) if body.account_id is not None else None
        if existing is not None:
            check_marketplace(existing, body.country_code)  # ValueError -> 422
        finish = _finisher(body, existing)
        if body.method == "browser":
            return _login_out(logins.start(body.country_code, finish))  # ValueError -> 422
        try:
            login = logins.start_code(body.country_code, finish)
        except LoginFailed as error:
            raise HTTPException(status_code=502, detail=str(error)) from None
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail=f"Could not reach Amazon: {error}") from None
        return _login_out(login)

    @router.get("/accounts/login/{login_id}", response_model=LoginOut)
    def login_status(login_id: str) -> LoginOut:
        """How a login is going; 404 once it has been forgotten (a while after it ended)."""
        login = logins.get(login_id)
        if login is None:
            raise HTTPException(status_code=404, detail="No such login, or it has expired; start again")
        return _login_out(login)

    @router.delete("/accounts/login/{login_id}", response_model=LoginOut)
    def cancel_account_login(login_id: str) -> LoginOut:
        """
        Cancel a login and answer with how it ended: `cancelled` when it was pending (a
        code login stops polling Amazon; a browser login can no longer be completed), or
        unchanged when it had already ended, so a code confirmed just before the cancel
        reads `confirmed` and its account stays. 404 once the login has been forgotten.
        """
        login = logins.cancel(login_id)
        if login is None:
            raise HTTPException(status_code=404, detail="No such login, or it has expired; start again")
        return _login_out(login)

    @router.post("/accounts/login/{login_id}", response_model=LoginOut, status_code=201)
    def complete_account_login(login_id: str, body: LoginComplete) -> LoginOut:
        """
        Step two of a browser login: the pasted address becomes the account.

        404 for a login that is unknown, expired or already finished (start again), 400
        for an address that carries no authorization code (the login stays open for the
        right one), 409 when Amazon accepted but the login could not be stored - another
        account holds it, the account has gone - which ends it, 502 when Amazon rejects
        the code.
        """
        try:
            login = logins.complete(login_id, body.response_url)
        except KeyError:
            raise HTTPException(status_code=404, detail="No such login, or it has expired; start again") from None
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except LoginNotStored as error:
            logger.warning("The login %s could not be stored", login_id, exc_info=True)
            raise HTTPException(status_code=409, detail=str(error)) from None
        except Exception as error:
            logger.warning("Amazon rejected the login %s", login_id, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Amazon rejected the login: {error}") from None
        return _login_out(login)

    return router


def account_or_404(account_id: int):
    account = get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"No account with id {account_id}")
    return account
