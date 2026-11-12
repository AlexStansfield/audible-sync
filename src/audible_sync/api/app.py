"""
The HTTP API, built for the web app that is Milestone 4.

`create_app` takes the scheduler and run state it serves rather than building them, so
a test can hand it fakes and the service can hand it the real ones. The routes are one
module per area, `audible_sync.api.routes`, each a function building its router from
what it needs; this module puts them together behind the authentication, and
`audible_sync.api.web` serves the web app. Every endpoint is a plain `def`: FastAPI runs
those in a threadpool, which is what the SQLite calls in `audible_sync.database` (one
connection per call) need, and keeps the event loop free for the polling the UI will do.

Everything under `/api` except `/api/health`, `/api/auth/login` and `/api/auth/logout`
needs either `Authorization: Bearer <token>` - scripts and the CLI - or the session
cookie the web app gets from signing in (`audible_sync.security.webauth`).
Errors are `{"detail": ...}` throughout, which is FastAPI's own shape, so the client
has one error format to handle.
"""

import logging
import secrets
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from audible_sync.api.routes import accounts as api_accounts
from audible_sync.api.routes import auth as api_auth
from audible_sync.api.routes import books as api_books
from audible_sync.api.routes import catalog as api_catalog
from audible_sync.api.routes import settings as api_settings
from audible_sync.api.routes import sync as api_sync
from audible_sync.api.routes import system as api_system
from audible_sync.api.routes import wishlist as api_wishlist
from audible_sync.api.schemas import (
    Health,
)
from audible_sync.api.web import BasePath, normalise_base_path, serve_web_app
from audible_sync.audible_api.login import PendingLogins
from audible_sync.catalog import Catalog
from audible_sync.logbuffer import RingBufferHandler
from audible_sync.paths import REPO_ROOT
from audible_sync.pipeline.runstate import RunState
from audible_sync.pipeline.scheduler import Scheduler
from audible_sync.security import webauth
from audible_sync.security.apitoken import ApiToken
from audible_sync.security.webauth import SESSION_COOKIE, LoginThrottle
from audible_sync.wishlist import Wishlists

logger = logging.getLogger(__name__)


def read_version() -> str:
    """The version from `pyproject.toml`, which the image carries beside the code."""
    try:
        with open(REPO_ROOT / "pyproject.toml", "rb") as f:
            return tomllib.load(f)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def _require_auth(api_token: ApiToken):
    """
    A dependency that lets a request through with the bearer token or a live session.

    The token is for scripts and the CLI; the cookie is for the web app, and is also
    what lets an `<img>` load a cover, which cannot send a header.
    """
    scheme = HTTPBearer(auto_error=False)

    def require_auth(
        request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(scheme)]
    ) -> None:
        # compare_digest so a wrong token takes the same time whatever it gets right;
        # bytes, because the str form refuses anything outside ASCII with a TypeError
        # The value is read per request, so a regenerated token applies at once
        expected = api_token.value.encode()
        if credentials is not None and secrets.compare_digest(credentials.credentials.encode(), expected):
            return
        session = request.cookies.get(SESSION_COOKIE)
        if session and webauth.check_session(session):
            return
        raise HTTPException(status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})

    return require_auth


def create_app(
    *,
    scheduler: Scheduler,
    state: RunState,
    api_token: ApiToken,
    cors_origins: list[str] | None = None,
    version: str | None = None,
    logins: PendingLogins | None = None,
    log_buffer: RingBufferHandler | None = None,
    login_throttle: LoginThrottle | None = None,
    secure_cookie: bool = False,
    web_dir: Path | None = None,
    base_path: str = "",
    catalog: Catalog | None = None,
    wishlists: Wishlists | None = None,
) -> FastAPI:
    """
    Build the application.

    Args:
        scheduler: Started on startup and stopped on shutdown, which is how a
            `docker stop` becomes a clean cancel
        state: The run in flight, as `/api/status` reports it
        api_token: The bearer token every request except the health check must carry,
            held so that regenerating it applies at once
        cors_origins: Origins allowed to call from a browser, e.g. the UI's dev server
        version: Reported by the health check; defaults to `pyproject.toml`'s
        logins: The store of logins started and not yet completed; defaults to a fresh one
        log_buffer: The handler holding recent log lines for `GET /api/logs`; without one
            the endpoint answers with nothing
        login_throttle: Counts failed sign-ins per client; defaults to a fresh one
        secure_cookie: Always mark the session cookie `Secure`. Without it the cookie is
            `Secure` only when the request arrived over https, which behind a
            TLS-terminating reverse proxy it never appears to
        web_dir: The built web app (`web/dist`), served for every path outside `/api`;
            nothing is served when it is None or holds no `index.html`
        base_path: The path a reverse proxy serves the app under, e.g. `/audible-sync`;
            empty at the root. Requests are accepted with the prefix or with it
            stripped, the web app is told it in `<base href>`, and the session cookie
            is scoped to it
        catalog: How the catalogue is asked, and its cache; defaults to a fresh one
        wishlists: How the wishlists are changed and read again; defaults to a fresh one
    """
    base_path = normalise_base_path(base_path)
    catalog = catalog or Catalog()
    cookie_path = f"{base_path}/"
    logins = logins or PendingLogins()
    throttle = login_throttle or LoginThrottle()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(title="Audible Sync", version=version or read_version(), lifespan=lifespan)

    if cors_origins:
        # The session cookie, for a web app served from one of these origins - but never
        # with `*`, for which Starlette echoes back whatever origin asked: any page on the
        # same host (another port is the same site, so SameSite=Strict still sends the
        # cookie) could then act as the signed-in user and read the answers
        credentials = "*" not in cors_origins
        if not credentials:
            logger.warning(
                "AUDIBLE_SYNC_CORS_ORIGINS is *: cross-origin requests can use the bearer token but not the "
                "web app's sign-in; list the web app's origin instead to allow that"
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(ValueError)
    async def _value_error(_request: Request, error: ValueError) -> JSONResponse:
        # Every validator in `audible_sync.settings` raises ValueError with a message meant to
        # be read, so it is the body of the 422 rather than a generic one. Pydantic's
        # own errors are ValueErrors too, and one reaching here means a response did
        # not fit its schema - a bug, which must surface as a 500 rather than a 422.
        if isinstance(error, ValidationError):
            raise error
        return JSONResponse(status_code=422, content={"detail": str(error)})

    open_router = APIRouter()

    @open_router.get("/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok", version=app.version)

    # One router per area. Included in this order, which is also the order of the
    # OpenAPI document: the open routes, then those behind the token or a session.
    open_auth, signed_in = api_auth.routers(throttle=throttle, secure_cookie=secure_cookie, cookie_path=cookie_path)
    for routes in (open_router, open_auth):
        app.include_router(routes, prefix="/api")
    for routes in (
        signed_in,
        api_sync.router(scheduler=scheduler, state=state),
        api_settings.router(scheduler=scheduler, api_token=api_token),
        api_system.router(scheduler=scheduler, log_buffer=log_buffer, version=app.version),
        api_accounts.router(state=state, logins=logins),
        api_books.router(),
        api_catalog.router(catalog=catalog),
        api_wishlist.router(catalog=catalog, wishlists=wishlists),
    ):
        app.include_router(routes, prefix="/api", dependencies=[Depends(_require_auth(api_token))])

    if web_dir is not None and (web_dir / "index.html").is_file():
        serve_web_app(app, web_dir, base_path)
    if base_path:
        # Last, so it is the outermost layer and every other one sees the full path
        app.add_middleware(BasePath, base_path=base_path)
    return app
