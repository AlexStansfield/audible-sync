"""
Logging in to an Audible marketplace without a terminal, a browser driver or a password.

The `audible` library's external login is an OAuth flow with PKCE: build a sign-in URL
carrying a code challenge, have the user sign in to Amazon in *their own* browser
(captcha and two-factor included), and read the authorization code out of the URL
Amazon redirects to - an `/ap/maplanding` page that shows "page not found", which is
expected. That splits cleanly into two steps with nothing held open in between: `start`
returns the URL and keeps the verifier and device serial; `complete` takes the pasted
URL, exchanges the code for tokens with `register`, and hands back an `Authenticator`.
No Amazon password ever passes through the app.

The library's own `external_login` does the same thing around a blocking callback, so
this module uses its building blocks (`create_code_verifier`, `build_oauth_url`,
`register`) rather than the callback. `PendingLogins` is the in-memory store between
the two steps; a login nobody completes expires.
"""

import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from textwrap import dedent
from typing import Any
from urllib.parse import parse_qs, urlsplit

from audible import Authenticator
from audible.localization import LOCALE_TEMPLATES, Locale
from audible.login import build_oauth_url, create_code_verifier
from audible.register import register

logger = logging.getLogger(__name__)

# How long a started login stays completable
LOGIN_TTL = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class Marketplace:
    country_code: str
    domain: str
    name: str


def _marketplaces() -> tuple[Marketplace, ...]:
    return tuple(
        Marketplace(country_code=entry["country_code"], domain=entry["domain"], name=key.replace("_", " ").title())
        for key, entry in LOCALE_TEMPLATES.items()
    )


# The marketplaces the library knows, in its order (Germany, United States, ...)
MARKETPLACES: tuple[Marketplace, ...] = _marketplaces()


def locale_for(country_code: str) -> Locale:
    """
    The marketplace for a country code.

    Raises:
        ValueError: for a code the library does not know
    """
    codes = {m.country_code for m in MARKETPLACES}
    if country_code not in codes:
        raise ValueError(f"unknown marketplace {country_code!r}; one of {', '.join(sorted(codes))}")
    return Locale(country_code)


@dataclass(frozen=True, slots=True)
class PendingLogin:
    """A login that has been started: what `complete_login` needs besides the pasted URL."""

    id: str
    country_code: str
    url: str
    code_verifier: bytes
    serial: str
    expires_at: datetime


def start_login(country_code: str, *, now: datetime | None = None, ttl: timedelta = LOGIN_TTL) -> PendingLogin:
    """
    Build the sign-in URL for a marketplace and remember what completing it will need.

    Raises:
        ValueError: for an unknown marketplace
    """
    locale = locale_for(country_code)
    code_verifier = create_code_verifier()
    url, serial = build_oauth_url(
        country_code=locale.country_code,
        domain=locale.domain,
        market_place_id=locale.market_place_id,
        code_verifier=code_verifier,
    )
    return PendingLogin(
        id=secrets.token_urlsafe(16),
        country_code=locale.country_code,
        url=url,
        code_verifier=code_verifier,
        serial=serial,
        expires_at=(now or datetime.now(UTC)) + ttl,
    )


def authorization_code_from(response_url: str) -> str:
    """
    The authorization code Amazon put in the URL it redirected to.

    Raises:
        ValueError: if the URL carries none - usually the sign-in URL pasted back, or a
            plain Amazon page, rather than the "page not found" URL after signing in
    """
    query = parse_qs(urlsplit(response_url.strip()).query)
    codes = query.get("openid.oa2.authorization_code")
    if not codes or not codes[0]:
        raise ValueError(
            "that URL carries no authorization code; paste the address of the 'page not found' page "
            "the browser lands on after signing in"
        )
    return codes[0]


def complete_login(pending: PendingLogin, response_url: str) -> Authenticator:
    """
    Exchange the pasted URL for credentials.

    This is the one step that talks to Amazon: `register` trades the authorization code
    and the verifier `start_login` kept for the tokens an `Authenticator` needs.

    Raises:
        ValueError: if the URL carries no authorization code
        Exception: from `register`, if Amazon rejects the code (it raises the response)
    """
    code = authorization_code_from(response_url)
    locale = locale_for(pending.country_code)
    registered = register(
        authorization_code=code,
        code_verifier=pending.code_verifier,
        domain=locale.domain,
        serial=pending.serial,
    )
    logger.info("Registered a new device with Audible %s", pending.country_code)
    return Authenticator.from_dict({**registered, "locale_code": pending.country_code, "with_username": False})


class PendingLogins:
    """
    The logins started and not yet completed, in memory, each good for `ttl`.

    In memory on purpose: a login is a minute's interaction, a restart in the middle of
    one just means starting again, and nothing secret is persisted. `pop` is single
    use, so a pasted URL cannot register two devices.
    """

    def __init__(self, *, ttl: timedelta = LOGIN_TTL, now: Callable[[], datetime] | None = None) -> None:
        self._ttl = ttl
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._pending: dict[str, PendingLogin] = {}

    def start(self, country_code: str) -> PendingLogin:
        pending = start_login(country_code, now=self._now(), ttl=self._ttl)
        with self._lock:
            self._prune()
            self._pending[pending.id] = pending
        return pending

    def pop(self, login_id: str) -> PendingLogin | None:
        """The login with this id, removed from the store, or None if unknown or expired."""
        with self._lock:
            self._prune()
            return self._pending.pop(login_id, None)

    def _prune(self) -> None:
        now = self._now()
        for login_id in [i for i, p in self._pending.items() if p.expires_at <= now]:
            del self._pending[login_id]


def login_interactively(
    country_code: str,
    *,
    prompt: Callable[[str], str] = input,
    echo: Callable[[str], Any] = print,
) -> Authenticator:
    """
    The same two steps at a terminal: show the URL, read the pasted one back.

    `prompt` and `echo` are injectable so the CLI path is tested without a terminal.
    """
    pending = start_login(country_code)
    echo(
        dedent(
            f"""
            Open this address in your browser and sign in to Amazon:

              {pending.url}

            After signing in the browser lands on a "page not found" error page. That is
            expected: copy the full address from the browser's address bar and paste it here.
            """
        )
    )
    response_url = prompt("Paste the address here: ")
    return complete_login(pending, response_url)
