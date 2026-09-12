"""
The HTTP API, built for the web app that is Milestone 4.

`create_app` takes the scheduler and run state it serves rather than building them, so
a test can hand it fakes and the service can hand it the real ones. Every endpoint is
a plain `def`: FastAPI runs those in a threadpool, which is what the SQLite calls in
`src.database` (one connection per call) need, and keeps the event loop free for the
polling the UI will do.

Everything under `/api` except `/api/health` needs `Authorization: Bearer <token>`.
Errors are `{"detail": ...}` throughout, which is FastAPI's own shape, so the client
has one error format to handle.
"""

import logging
import secrets
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from src.accounts import add_account_from_authenticator, authenticator_for, import_auth_file
from src.audible_login import MARKETPLACES, PendingLogins, complete_login
from src.database import (
    count_sync_runs,
    delete_account,
    get_account,
    get_accounts,
    get_sync_run,
    get_sync_runs,
    save_settings,
    update_account,
)
from src.paths import REPO_ROOT
from src.runstate import RunState
from src.scheduler import Scheduler
from src.schemas import (
    AccountImport,
    AccountOut,
    AccountUpdate,
    Health,
    LoginComplete,
    LoginStart,
    LoginStarted,
    MarketplaceOut,
    Message,
    SettingsOut,
    SettingsUpdate,
    Status,
    SyncRunList,
    SyncRunOut,
)
from src.settings import Settings

logger = logging.getLogger(__name__)


def read_version() -> str:
    """The version from `pyproject.toml`, which the image carries beside the code."""
    try:
        with open(REPO_ROOT / "pyproject.toml", "rb") as f:
            return tomllib.load(f)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def _bearer_auth(api_token: str):
    """A dependency that rejects any request without the configured token."""
    scheme = HTTPBearer(auto_error=False)

    def require_token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(scheme)]) -> None:
        # compare_digest so a wrong token takes the same time whatever it gets right
        if credentials is None or not secrets.compare_digest(credentials.credentials, api_token):
            raise HTTPException(status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})

    return require_token


def create_app(
    *,
    scheduler: Scheduler,
    state: RunState,
    api_token: str,
    cors_origins: list[str] | None = None,
    version: str | None = None,
    logins: PendingLogins | None = None,
) -> FastAPI:
    """
    Build the application.

    Args:
        scheduler: Started on startup and stopped on shutdown, which is how a
            `docker stop` becomes a clean cancel
        state: The run in flight, as `/api/status` reports it
        api_token: The bearer token every request except the health check must carry
        cors_origins: Origins allowed to call from a browser, e.g. the UI's dev server
        version: Reported by the health check; defaults to `pyproject.toml`'s
        logins: The store of logins started and not yet completed; defaults to a fresh one
    """
    logins = logins or PendingLogins()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(title="Audible Sync", version=version or read_version(), lifespan=lifespan)

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(ValueError)
    async def _value_error(_request: Request, error: ValueError) -> JSONResponse:
        # Every validator in `src.settings` raises ValueError with a message meant to
        # be read, so it is the body of the 422 rather than a generic one. Pydantic's
        # own errors are ValueErrors too, and one reaching here means a response did
        # not fit its schema - a bug, which must surface as a 500 rather than a 422.
        if isinstance(error, ValidationError):
            raise error
        return JSONResponse(status_code=422, content={"detail": str(error)})

    open_router = APIRouter(prefix="/api")
    router = APIRouter(prefix="/api", dependencies=[Depends(_bearer_auth(api_token))])

    @open_router.get("/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok", version=app.version)

    @router.get("/status", response_model=Status)
    def status() -> Status:
        settings = Settings.from_db()
        last = get_sync_runs(limit=1)
        return Status(
            scheduler=scheduler.status(settings),
            current_run=state.snapshot(),
            last_run=last[0] if last else None,
            accounts=get_accounts(),
        )

    @router.post("/sync", response_model=Message, status_code=202)
    def start_sync() -> Message:
        if not scheduler.trigger():
            raise HTTPException(status_code=409, detail="A sync is already running")
        return Message(status="started")

    @router.post("/sync/cancel", response_model=Message, status_code=202)
    def cancel_sync() -> Message:
        if not scheduler.cancel():
            raise HTTPException(status_code=409, detail="No sync is running")
        return Message(status="cancelling")

    @router.get("/sync/runs", response_model=SyncRunList)
    def list_runs(
        limit: Annotated[int, Query(ge=1, le=200)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        account_id: int | None = None,
    ) -> SyncRunList:
        return SyncRunList(
            items=get_sync_runs(limit=limit, offset=offset, account_id=account_id),
            total=count_sync_runs(account_id=account_id),
        )

    @router.get("/sync/runs/{run_id}", response_model=SyncRunOut)
    def get_run(run_id: int) -> SyncRunOut:
        run = get_sync_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
        return run

    @router.get("/settings", response_model=SettingsOut)
    def get_settings() -> SettingsOut:
        return SettingsOut.model_validate(Settings.from_db())

    @router.put("/settings", response_model=SettingsOut)
    def update_settings(update: SettingsUpdate) -> SettingsOut:
        changes = update.model_dump(exclude_unset=True)
        # `with_changes` validates; a ValueError becomes the 422 above
        settings = Settings.from_db().with_changes(**changes)
        save_settings(settings.to_db_values())
        if "debug" in changes:
            logging.getLogger().setLevel(logging.DEBUG if settings.debug else logging.INFO)
        # A changed interval or a toggled schedule applies to the wait already in progress
        scheduler.wake()
        return SettingsOut.model_validate(settings)

    @router.get("/accounts", response_model=list[AccountOut])
    def list_accounts() -> list[AccountOut]:
        return get_accounts()

    @router.get("/accounts/{account_id}", response_model=AccountOut)
    def get_one_account(account_id: int) -> AccountOut:
        return _account_or_404(account_id)

    @router.patch("/accounts/{account_id}", response_model=AccountOut)
    def patch_account(account_id: int, update: AccountUpdate) -> AccountOut:
        _account_or_404(account_id)
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
        """
        account = _account_or_404(account_id)
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

    @router.post("/accounts/login", response_model=LoginStarted)
    def start_account_login(body: LoginStart) -> LoginStarted:
        """
        Step one of adding an account: the address to sign in at.

        The user opens `url`, signs in to Amazon in their own browser, and lands on a
        "page not found" page; its address goes to step two within `expires_at`.
        """
        pending = logins.start(body.country_code)  # ValueError for an unknown marketplace -> 422
        return LoginStarted(login_id=pending.id, url=pending.url, expires_at=pending.expires_at.isoformat())

    @router.post("/accounts/login/{login_id}", response_model=AccountOut, status_code=201)
    def complete_account_login(login_id: str, body: LoginComplete) -> AccountOut:
        """
        Step two: the pasted address becomes an account.

        404 for a login that was never started or has expired (start again), 400 for an
        address that carries no authorization code, 502 when Amazon rejects the code.
        """
        pending = logins.pop(login_id)
        if pending is None:
            raise HTTPException(status_code=404, detail="No such login, or it has expired; start again")
        try:
            auth = complete_login(pending, body.response_url)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except Exception as error:
            logger.warning("Amazon rejected the login for %s", pending.country_code, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Amazon rejected the login: {error}") from None
        account_id = add_account_from_authenticator(auth, name=body.name, monitor_existing=body.monitor_existing)
        return get_account(account_id)

    @router.post("/accounts/import", response_model=AccountOut, status_code=201)
    def import_account(body: AccountImport) -> AccountOut:
        """Create an account from an auth file on the server, e.g. one audible-cli wrote."""
        try:
            account_id = import_auth_file(body.path, name=body.name, monitor_existing=body.monitor_existing)
        except FileNotFoundError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except Exception as error:
            # Anything the audible library rejects: encrypted, not JSON, missing fields
            logger.warning("Could not import %s", body.path, exc_info=True)
            raise HTTPException(status_code=400, detail=f"could not read {body.path}: {error}") from None
        return get_account(account_id)

    app.include_router(open_router)
    app.include_router(router)
    return app


def _account_or_404(account_id: int):
    account = get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"No account with id {account_id}")
    return account
