"""
The web app, served by the API: its build's files and pages, and the base path the
whole app answers under behind a reverse proxy (`AUDIBLE_SYNC_BASE_PATH`).
"""

import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


# One or more segments of URL-safe characters that need no escaping in HTML or a
# cookie; `.` and `..` alone are not segments anyone means
_BASE_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")
# The app's own first path segments - the API, the docs, the build's files and the web
# app's pages (web/src/app/routes.tsx; a test fails when a page is missing here).
# Behind a proxy that strips the prefix, a request for `/api/...` under a base of
# `/api` cannot be told from one that kept it, so such a base is refused
_RESERVED_SEGMENTS = frozenset(
    {"api", "assets", "docs", "redoc", "openapi.json", "index.html", "favicon.svg"}
    | {"manifest.webmanifest", "sw.js", "icons"}
    | {"login", "library", "search", "wishlist", "accounts", "activity", "settings", "profile"}
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
# Everything else keeps its name from build to build, so it is checked on every load:
# index.html names the current assets, and a changed sw.js is how an installed app
# learns of a new build
_REVALIDATE = {"Cache-Control": "no-cache"}
# Named here rather than guessed from the platform's table, which varies by host and
# may not know a manifest
_MEDIA_TYPES = {".webmanifest": "application/manifest+json", ".js": "text/javascript"}
_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


# What the build's index.html carries for the service to point at the base path:
# `<base href="/">`, or `<base href="/" />` as written in web/index.html
_BASE_MARKER = re.compile(r'<base href="/"\s*/?>')


def serve_web_app(app: FastAPI, web_dir: Path, base_path: str = "") -> None:
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
                headers = _IMMUTABLE if candidate.is_relative_to(root / "assets") else _REVALIDATE
                return FileResponse(candidate, headers=headers, media_type=_MEDIA_TYPES.get(candidate.suffix))
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
