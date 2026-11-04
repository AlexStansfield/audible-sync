"""
The HTTP API, built for the web app that is Milestone 4.

`create_app` takes the scheduler and run state it serves rather than building them, so
a test can hand it fakes and the service can hand it the real ones. Every endpoint is
a plain `def`: FastAPI runs those in a threadpool, which is what the SQLite calls in
`src.database` (one connection per call) need, and keeps the event loop free for the
polling the UI will do.

Everything under `/api` except `/api/health`, `/api/auth/login` and `/api/auth/logout`
needs either `Authorization: Bearer <token>` - scripts and the CLI - or the session
cookie the web app gets from signing in (`src.webauth`).
Errors are `{"detail": ...}` throughout, which is FastAPI's own shape, so the client
has one error format to handle.
"""

import logging
import re
import secrets
import tomllib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
from audible import Authenticator
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from src import crypto, database, library, system, webauth
from src.accounts import (
    authenticator_for,
    check_marketplace,
    replace_account_auth,
    store_login,
)
from src.apitoken import ApiToken, TokenFromEnv
from src.audible_login import MARKETPLACES, BrowserLogin, CodeLogin, LoginFailed, PendingLogins
from src.database import (
    BOOK_SORT_COLUMNS,
    book_ids_matching,
    count_sync_runs,
    delete_account,
    downloaded_file_paths,
    get_account,
    get_accounts,
    get_book,
    get_run_books,
    get_sync_run,
    get_sync_runs,
    library_stats,
    list_books,
    requeue_unavailable,
    save_settings,
    set_monitored,
    update_account,
)
from src.logbuffer import RingBufferHandler
from src.model import Account, BookStatus, RunBookOutcome, StoredLogin
from src.notify import send_webhook
from src.paths import REPO_ROOT
from src.runstate import RunState
from src.scheduler import Scheduler
from src.schemas import (
    AccountOut,
    AccountStats,
    AccountUpdate,
    ApiTokenInfo,
    BookAction,
    BookList,
    BookOut,
    BookUpdate,
    BulkRequest,
    BulkResultOut,
    BulkSkip,
    DiskOut,
    Health,
    LoginComplete,
    LoginOut,
    LoginStart,
    LogList,
    MarketplaceOut,
    Me,
    Message,
    NewApiToken,
    PasswordChange,
    QueueItem,
    QueueOut,
    Requeued,
    RetryUnavailable,
    RunBookOut,
    RunBooks,
    SettingsOut,
    SettingsUpdate,
    SignIn,
    Stats,
    Status,
    SyncRunList,
    SyncRunOut,
    SystemInfo,
    UsernameChange,
    WebhookTest,
)
from src.settings import Settings
from src.webauth import SESSION_COOKIE, LoginThrottle

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


def _me() -> Me:
    user = webauth.current_user()
    if user is None:
        # ensure_admin runs at every start, so only an app built without it gets here
        raise HTTPException(status_code=404, detail="No web app user has been set up")
    return Me(username=user.username, generated_password=user.generated_password)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


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
        # Every validator in `src.settings` raises ValueError with a message meant to
        # be read, so it is the body of the 422 rather than a generic one. Pydantic's
        # own errors are ValueErrors too, and one reaching here means a response did
        # not fit its schema - a bug, which must surface as a 500 rather than a 422.
        if isinstance(error, ValidationError):
            raise error
        return JSONResponse(status_code=422, content={"detail": str(error)})

    open_router = APIRouter(prefix="/api")
    router = APIRouter(prefix="/api", dependencies=[Depends(_require_auth(api_token))])

    @open_router.get("/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok", version=app.version)

    # --- the web app's own sign-in -----------------------------------------------

    def _throttled(request: Request) -> None:
        # Counts the attempt before the password is checked; `succeeded` takes it back
        retry_after = throttle.attempt(_client_key(request))
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="Too many failed attempts; try again later",
                headers={"Retry-After": str(retry_after)},
            )

    def _check_current_password(request: Request, password: str) -> None:
        # A wrong current password counts as a failed sign-in: it is the same guess
        _throttled(request)
        if not webauth.check_current_password(password):
            raise HTTPException(status_code=403, detail="The current password is wrong")
        throttle.succeeded(_client_key(request))

    @open_router.post("/auth/login", response_model=Me)
    def sign_in(body: SignIn, request: Request, response: Response) -> Me:
        _throttled(request)
        if not webauth.verify(body.username, body.password):
            # One message for either mistake: which one was wrong is not said
            raise HTTPException(status_code=401, detail="Wrong username or password")
        throttle.succeeded(_client_key(request))
        # Signing in again replaces the browser's session rather than leaving the old one live
        previous = request.cookies.get(SESSION_COOKIE)
        if previous:
            webauth.end_session(previous)
        token = webauth.create_session(persistent=body.remember)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=int(webauth.PERSISTENT_SESSION.total_seconds()) if body.remember else None,
            httponly=True,
            samesite="strict",
            # Over https only, unless configured: the service is often reached over plain
            # http on a LAN, where a Secure cookie would never be sent back
            secure=secure_cookie or request.url.scheme == "https",
            path=cookie_path,
        )
        return _me()

    @open_router.post("/auth/logout", status_code=204)
    def sign_out(request: Request) -> Response:
        # Open, so a browser whose session has already ended can still clear the cookie
        session = request.cookies.get(SESSION_COOKIE)
        if session:
            webauth.end_session(session)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path=cookie_path, httponly=True, samesite="strict")
        return response

    @router.get("/auth/me", response_model=Me)
    def me() -> Me:
        return _me()

    @router.put("/auth/username", response_model=Me)
    def change_username(body: UsernameChange, request: Request) -> Me:
        _check_current_password(request, body.current_password)
        # ValueError for an empty or over-long name, the 422 below
        webauth.set_username(body.username)
        return _me()

    @router.put("/auth/password", response_model=Me)
    def change_password(body: PasswordChange, request: Request) -> Me:
        _check_current_password(request, body.current_password)
        webauth.set_password(body.new_password)
        # Every other browser is signed out; this one stays. Under the bearer token
        # there is no "this one", so every session goes.
        webauth.end_other_sessions(request.cookies.get(SESSION_COOKIE))
        return _me()

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

    @router.get("/sync/runs/{run_id}/books", response_model=RunBooks)
    def get_run_books_route(run_id: int) -> RunBooks:
        """
        The books a run downloaded, failed and was refused, for the Activity screen.
        `recorded` is false on a run from before the per-book records, which says so
        rather than showing three empty lists.
        """
        run = get_sync_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
        groups: dict[str, list[RunBookOut]] = {outcome.value: [] for outcome in RunBookOutcome}
        for record in get_run_books(run_id):
            if record.outcome in groups:
                groups[record.outcome].append(RunBookOut.model_validate(record))
        return RunBooks(run_id=run_id, recorded=run.books_recorded, **groups)

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

    @router.post("/settings/webhook/test", response_model=Message)
    def test_webhook(body: WebhookTest) -> Message:
        """Send a sample event to `url`, or the configured webhook; 502 if it fails."""
        url = body.url or Settings.from_db().webhook_url
        if not url:
            raise HTTPException(status_code=400, detail="No webhook URL configured")
        try:
            send_webhook(url, {"event": "test", "message": "Audible Sync webhook test"})
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"Webhook failed: {error}") from None
        return Message(status="sent")

    @router.get("/settings/api-token", response_model=ApiTokenInfo)
    def get_api_token() -> ApiTokenInfo:
        """The token masked to its last four characters, and whether it is the env's or stored."""
        return ApiTokenInfo(last4=api_token.last4, source=api_token.source)

    @router.post("/settings/api-token/regenerate", response_model=NewApiToken)
    def regenerate_api_token(request: Request) -> NewApiToken:
        """
        Replace the token; the old one stops working at once. The new one is returned
        this once and never logged. 409 when `AUDIBLE_SYNC_API_TOKEN` sets it.

        Only from a signed-in web app (403 under the bearer token alone): a leaked token
        must not be able to lock its owner's scripts out by rotating itself.
        """
        session = request.cookies.get(SESSION_COOKIE)
        if not (session and webauth.check_session(session)):
            raise HTTPException(status_code=403, detail="Regenerate the API token from the signed-in web app")
        try:
            return NewApiToken(token=api_token.regenerate())
        except TokenFromEnv as error:
            raise HTTPException(status_code=409, detail=str(error)) from None

    @router.get("/system", response_model=SystemInfo)
    def get_system() -> SystemInfo:
        """FFmpeg, the library's disk, the key and the database, for the System card."""
        ffmpeg = system.ffmpeg()
        library_disk = system.disk(Settings.from_db().audiobook_folder)
        key = crypto.key_source()
        return SystemInfo(
            version=app.version,
            ffmpeg_version=ffmpeg.version,
            libopus=ffmpeg.libopus,
            audiobook_folder=DiskOut.model_validate(library_disk),
            key_source=key.kind,
            key_file=str(key.path) if key.path else None,
            database_bytes=system.database_bytes(database.DB_FILE),
        )

    @router.get("/logs", response_model=LogList)
    def get_logs(
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
        level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None = None,
    ) -> LogList:
        """The newest log lines, oldest first, at or above `level`."""
        return LogList(items=log_buffer.records(limit=limit, level=level) if log_buffer else [])

    @router.get("/stats", response_model=Stats)
    def get_stats() -> Stats:
        """The numbers a dashboard opens with: per account and in total."""
        settings = Settings.from_db()
        per_account = []
        for account in get_accounts():
            last = get_sync_runs(limit=1, account_id=account.id)
            per_account.append(
                AccountStats(
                    account=account,
                    books=library_stats(account_id=account.id),
                    bytes_on_disk=_bytes_on_disk(downloaded_file_paths(account_id=account.id)),
                    last_run=last[0] if last else None,
                )
            )
        last = get_sync_runs(limit=1)
        return Stats(
            accounts=per_account,
            books=library_stats(),
            bytes_on_disk=sum(a.bytes_on_disk for a in per_account),
            last_run=last[0] if last else None,
            next_run_at=scheduler.status(settings)["next_run_at"],
        )

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

        409 while the run in flight is on this account: its sync would leave rows behind
        for an account that no longer exists, and its download a book on disk with no
        row. An account the run has not reached yet is fine - the run reads each account
        again at its turn and skips one that has gone.
        """
        account = _account_or_404(account_id)
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
                replace_account_auth(existing, auth, deregister_previous=body.deregister_previous)
                return StoredLogin(existing.id, replaced=True)
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
        existing = _account_or_404(body.account_id) if body.account_id is not None else None
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
        right one), 502 when Amazon rejects the code.
        """
        try:
            login = logins.complete(login_id, body.response_url)
        except KeyError:
            raise HTTPException(status_code=404, detail="No such login, or it has expired; start again") from None
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except Exception as error:
            logger.warning("Amazon rejected the login %s", login_id, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Amazon rejected the login: {error}") from None
        return _login_out(login)

    @router.get("/books", response_model=BookList)
    def get_books(
        account_id: int | None = None,
        status: BookStatus | None = None,
        monitored: bool | None = None,
        q: Annotated[str | None, Query(max_length=200)] = None,
        sort: Literal["date_added", "title", "author", "release_date", "downloaded_at"] = "date_added",
        order: Literal["asc", "desc"] = "desc",
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> BookList:
        assert sort in BOOK_SORT_COLUMNS  # the Literal above is the same set; keep them in step
        items, total = list_books(
            account_id=account_id,
            status=status,
            monitored=monitored,
            q=q,
            sort=sort,
            descending=order == "desc",
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return BookList(items=items, total=total, page=page, page_size=page_size)

    @router.post("/books/retry-unavailable", response_model=Requeued)
    def retry_unavailable(body: RetryUnavailable | None = None) -> Requeued:
        """
        Queue every parked book Audible still reports consumable again.

        For the day Audible refused every licence - a device registration it had
        stopped accepting, say - rather than one title it has retired. A re-login
        does this for the account by itself; this is the button for everything else.
        """
        account_id = body.account_id if body else None
        if account_id is not None:
            _account_or_404(account_id)
        return Requeued(requeued=requeue_unavailable(account_id))

    @router.post("/books/bulk", response_model=BulkResultOut)
    def bulk_books(body: BulkRequest) -> BulkResultOut:
        """
        One action on many books: the ids selected, or everything the filters match.

        The filters mean what they mean on `GET /api/books`, so "all 354 matching" acts
        on the 354 the listing counted. Books a run is downloading, a retry of a
        downloaded book and deleting the files of a book with none are skipped, each
        with a reason; an unknown id is skipped as `not_found`. 404 for a filter on an
        unknown account.
        """
        if body.filter is not None:
            if body.filter.account_id is not None:
                _account_or_404(body.filter.account_id)
            ids = book_ids_matching(**body.filter.model_dump())
        else:
            ids = body.ids
        result = library.bulk(body.action, ids, Settings.from_db())
        return BulkResultOut(
            updated=result.updated,
            queued=result.queued,
            skipped=[BulkSkip(id=book_id, reason=reason) for book_id, reason in result.skipped],
        )

    @router.get("/queue", response_model=QueueOut)
    def get_queue(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> QueueOut:
        """
        The books still to come, in the order a run takes them: each enabled account
        that can log in, oldest purchase first, cut at `max_download` per account. A
        plan rather than a promise (refusals, titles the sync restores, a run in flight;
        see `library.next_run_queue`). `limit` is how many to list; `total` (planned)
        and `waiting` count them all.
        """
        settings = Settings.from_db()
        queue = library.next_run_queue(settings)
        return QueueOut(
            items=[
                QueueItem(position=position, account_id=account_id, book=book)
                for position, (account_id, book) in enumerate(queue.planned[:limit], start=1)
            ],
            total=len(queue.planned),
            waiting=queue.waiting,
            max_download=settings.max_download,
        )

    @router.get("/books/{book_id}", response_model=BookOut)
    def get_one_book(book_id: int) -> BookOut:
        return _book_or_404(book_id)

    @router.patch("/books/{book_id}", response_model=BookOut)
    def patch_book(book_id: int, update: BookUpdate) -> BookOut:
        _book_or_404(book_id)
        set_monitored(book_id, update.monitored)
        return get_book(book_id)

    @router.post("/books/{book_id}/delete-files", response_model=BookAction)
    def delete_book_files(book_id: int) -> BookAction:
        """Remove the files and unmonitor the book, so it stays deleted."""
        book = _book_or_404(book_id)
        removed = _busy_to_409(library.delete_download, book, Settings.from_db())
        return BookAction(status="deleted", removed=[str(p) for p in removed])

    @router.post("/books/{book_id}/redownload", response_model=BookAction)
    def redownload_book(book_id: int) -> BookAction:
        """Remove the files and queue the book afresh, attempts reset."""
        book = _book_or_404(book_id)
        removed = _busy_to_409(library.redownload, book, Settings.from_db())
        return BookAction(status="queued", removed=[str(p) for p in removed])

    @router.post("/books/{book_id}/retry", response_model=BookAction)
    def retry_book(book_id: int) -> BookAction:
        """Queue a failed or parked book again without touching any files it has."""
        book = _book_or_404(book_id)
        try:
            library.retry(book)
        except library.BookBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return BookAction(status="queued")

    @router.post("/books/{book_id}/refresh", response_model=BookOut)
    def refresh_book(book_id: int) -> BookOut:
        """Re-read the book from Audible."""
        book = _book_or_404(book_id)
        try:
            return library.refresh(book)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except Exception as error:
            logger.warning("Could not refresh %s from Audible", book.asin, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Audible did not answer: {error}") from None

    @router.get("/books/{book_id}/cover")
    def book_cover(book_id: int):
        """The cover: the file once downloaded, Audible's URL before that."""
        book = _book_or_404(book_id)
        if book.cover_path and Path(book.cover_path).is_file():
            return FileResponse(book.cover_path)
        if book.cover_url:
            return RedirectResponse(book.cover_url, status_code=302)
        raise HTTPException(status_code=404, detail="No cover for this book")

    app.include_router(open_router)
    app.include_router(router)
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


def _book_or_404(book_id: int):
    book = get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail=f"No book with id {book_id}")
    return book


def _busy_to_409(action, book, settings):
    try:
        return action(book, settings)
    except library.BookBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


def _bytes_on_disk(paths: list[str]) -> int:
    """Sum of the file sizes, ignoring any file that has gone."""
    total = 0
    for path in paths:
        try:
            total += Path(path).stat().st_size
        except OSError:
            continue
    return total


def _account_or_404(account_id: int):
    account = get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"No account with id {account_id}")
    return account
