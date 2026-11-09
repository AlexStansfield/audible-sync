"""
The HTTP API, built for the web app that is Milestone 4.

`create_app` takes the scheduler and run state it serves rather than building them, so
a test can hand it fakes and the service can hand it the real ones. The routes are one
module per area, `audible_sync.api.routes`, each a function building its router from what it needs;
this module puts them together behind the authentication, and serves the web app. Every endpoint is
a plain `def`: FastAPI runs those in a threadpool, which is what the SQLite calls in
`audible_sync.database` (one connection per call) need, and keeps the event loop free for the
polling the UI will do.

Everything under `/api` except `/api/health`, `/api/auth/login` and `/api/auth/logout`
needs either `Authorization: Bearer <token>` - scripts and the CLI - or the session
cookie the web app gets from signing in (`audible_sync.security.webauth`).
Errors are `{"detail": ...}` throughout, which is FastAPI's own shape, so the client
has one error format to handle.
"""

import logging
import re
import secrets
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from audible_sync.api.routes import accounts as api_accounts
from audible_sync.api.routes import auth as api_auth
from audible_sync.api.routes import books as api_books
from audible_sync.api.routes import settings as api_settings
from audible_sync.api.routes import sync as api_sync
from audible_sync.api.routes import system as api_system
from audible_sync.api.schemas import (
    Health,
)
from audible_sync.audible_api.login import PendingLogins
from audible_sync.logbuffer import RingBufferHandler
from audible_sync.paths import REPO_ROOT
from audible_sync.pipeline.runstate import RunState
from audible_sync.pipeline.scheduler import Scheduler
from audible_sync.security import webauth
from audible_sync.security.apitoken import ApiToken
from audible_sync.security.webauth import SESSION_COOKIE, LoginThrottle

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
    """
    base_path = normalise_base_path(base_path)
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
    ):
        app.include_router(routes, prefix="/api", dependencies=[Depends(_require_auth(api_token))])

    if web_dir is not None and (web_dir / "index.html").is_file():
        _serve_web_app(app, web_dir, base_path)
    if base_path:
        # Last, so it is the outermost layer and every other one sees the full path
        app.add_middleware(BasePath, base_path=base_path)
    return app


# One or more segments of URL-safe characters that need no escaping in HTML or a
# cookie; `.` and `..` alone are not segments anyone means
_BASE_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")
# The app's own first path segments - the API, the docs, the build's files and the web
# app's pages (web/src/app/routes.tsx; a test fails when a page is missing here).
# Behind a proxy that strips the prefix, a request for `/api/...` under a base of
# `/api` cannot be told from one that kept it, so such a base is refused
_RESERVED_SEGMENTS = frozenset(
    {"api", "assets", "docs", "redoc", "openapi.json", "index.html", "favicon.svg"}
    | {"login", "library", "accounts", "activity", "settings", "profile"}
)


def normalise_base_path(value: str) -> str:
    """
    The base path as `/a/b`: a leading slash, no trailing one, and `""` for the root.
    `ValueError` for anything else, since the value is written into the web app's HTML.
    """
    trimmed = value.strip().strip("/")
    if not trimmed:
        return ""
    for segment in trimmed.split("/"):
        if not _BASE_SEGMENT.fullmatch(segment) or segment in (".", ".."):
            raise ValueError(f"The base path must be URL path segments of letters, digits and .-_~, got {value!r}")
    first = trimmed.split("/", 1)[0]
    if first.lower() in _RESERVED_SEGMENTS:
        raise ValueError(
            f"The base path cannot start with /{first}, which is one of the app's own paths, got {value!r}"
        )
    return f"/{trimmed}"


class BasePath:
    """
    Serve the app under `base_path` whichever way the reverse proxy forwards it.

    A proxy either strips the prefix (nginx's `proxy_pass http://app:8080/;`) or passes
    it on (Traefik without StripPrefix). Either way the request leaves here as the ASGI
    spec has it: `root_path` the prefix and `path` the full path including it, from
    which Starlette routes on the part after the prefix and builds every URL it hands
    out, `/docs`' link to the schema included. The prefix alone, with no slash after
    it, is redirected to the slash so the web app's relative URLs resolve under it.
    """

    def __init__(self, app: ASGIApp, base_path: str) -> None:
        self.app = app
        self.base_path = base_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path == self.base_path and scope["type"] == "http":
            query = scope.get("query_string", b"").decode("latin-1")
            location = f"{self.base_path}/" + (f"?{query}" if query else "")
            await RedirectResponse(location, status_code=308)(scope, receive, send)
            return
        scope = {**scope, "root_path": self.base_path}
        if not path.startswith(f"{self.base_path}/"):
            scope["path"] = f"{self.base_path}{path}"
            # raw_path keeps the client's percent-encoding, so it is prefixed, not rebuilt
            if scope.get("raw_path") is not None:
                scope["raw_path"] = self.base_path.encode("ascii") + scope["raw_path"]
        await self.app(scope, receive, send)


# Vite names everything under assets/ after its content, so a file there never changes
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
# index.html names the current assets, so it is checked on every load, however it is asked for
_REVALIDATE = {"Cache-Control": "no-cache"}
_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


# What the build's index.html carries for the service to point at the base path:
# `<base href="/">`, or `<base href="/" />` as written in web/index.html
_BASE_MARKER = re.compile(r'<base href="/"\s*/?>')


def _serve_web_app(app: FastAPI, web_dir: Path, base_path: str = "") -> None:
    """
    Serve the single-page app: a file that exists under `web_dir`, else `index.html`
    so the browser's router handles deep links like `/library?status=failed`.

    Registered after the API routes, so it only sees what they did not match. A path
    under `/api` is still a JSON 404, so a mistyped API URL never answers with HTML,
    and a missing file under `assets/` is a 404 rather than a page that would be
    parsed as script. Every method reaches it, so an unknown path is a JSON 404 and a
    known one asked with the wrong method a 405, with or without a build.
    """
    root = web_dir.resolve()
    index = root / "index.html"
    others = list(app.router.routes)  # everything registered before this route
    # The rewritten page, kept until the file changes: a rebuild beside a running service
    # (`npm run build`) replaces the hashed assets the previous page names
    cached: dict[str, object] = {}

    def index_page() -> HTMLResponse:
        try:
            stat = index.stat()
        except OSError:
            # Mid-rebuild Vite has emptied the folder: the last good page, not a 500
            if "page" in cached:
                return HTMLResponse(cached["page"], headers=_REVALIDATE)
            raise
        stamp = (stat.st_mtime_ns, stat.st_size)
        if cached.get("stamp") != stamp:
            # The web app takes its router's basename and the API's address from <base href>
            page, found = _BASE_MARKER.subn(f'<base href="{base_path}/">', index.read_text(encoding="utf-8"), count=1)
            if not found and base_path:
                logger.warning(
                    '%s has no <base href="/"> to point at %s; the web app will not load there', index, base_path
                )
            cached.update(stamp=stamp, page=page)
        return HTMLResponse(cached["page"], headers=_REVALIDATE)

    index_page()  # read at start, so a build without the marker is reported then

    @app.api_route("/{path:path}", methods=_ALL_METHODS, include_in_schema=False)
    def web_app(request: Request, path: str) -> Response:
        # A route that exists under another method is a 405, as it is without this
        # catch-all: matching every method here would otherwise turn it into a 404
        allowed = _methods_allowed(others, request.scope)
        if allowed:
            raise HTTPException(status_code=405, detail="Method Not Allowed", headers={"Allow": ", ".join(allowed)})
        if request.method not in ("GET", "HEAD") or path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if path:
            candidate = (root / path).resolve()
            # resolve() folds any `..`, so this is what keeps a request inside web_dir
            if candidate.is_relative_to(root) and candidate.is_file():
                # resolve() follows a symlinked index.html, so compare it resolved too
                if candidate == index.resolve():
                    return index_page()
                headers = _IMMUTABLE if candidate.is_relative_to(root / "assets") else None
                return FileResponse(candidate, headers=headers)
            if path.startswith("assets/"):
                raise HTTPException(status_code=404, detail="Not Found")
        return index_page()


def _methods_allowed(routes: list, scope: dict) -> list[str]:
    """
    The methods some route answers this path with, when the request's own method is not
    one of them; empty when no route has the path at all. Starlette's own matching, so
    it sees through the routers `include_router` wraps the API's routes in.
    """
    if not any(route.matches(scope)[0] is Match.PARTIAL for route in routes):
        return []
    return [
        method
        for method in _ALL_METHODS
        if any(route.matches({**scope, "method": method})[0] is Match.FULL for route in routes)
    ]
