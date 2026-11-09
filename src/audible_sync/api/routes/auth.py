"""
The web app's own sign-in: signing in and out, and the signed-in user's name and password.

`/api/auth/login` and `/api/auth/logout` are open; the rest need the token or a session.
"""

from fastapi import APIRouter, HTTPException, Request, Response

from audible_sync.api.schemas import (
    Me,
    PasswordChange,
    SignIn,
    UsernameChange,
)
from audible_sync.security import webauth
from audible_sync.security.webauth import SESSION_COOKIE, LoginThrottle


def _me() -> Me:
    user = webauth.current_user()
    if user is None:
        # ensure_admin runs at every start, so only an app built without it gets here
        raise HTTPException(status_code=404, detail="No web app user has been set up")
    return Me(username=user.username, generated_password=user.generated_password)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def routers(*, throttle: LoginThrottle, secure_cookie: bool, cookie_path: str) -> tuple[APIRouter, APIRouter]:
    """The open routes and the ones behind the token or a session, in that order."""
    open_router = APIRouter()
    router = APIRouter()

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
        # ValueError for an empty or over-long name: `create_app` makes it a 422
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

    return open_router, router
