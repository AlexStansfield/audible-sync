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

from src.database import count_sync_runs, get_sync_run, get_sync_runs, save_settings
from src.paths import REPO_ROOT
from src.runstate import RunState
from src.scheduler import Scheduler
from src.schemas import (
    Health,
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
    """

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
    ) -> SyncRunList:
        return SyncRunList(items=get_sync_runs(limit=limit, offset=offset), total=count_sync_runs())

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

    app.include_router(open_router)
    app.include_router(router)
    return app
