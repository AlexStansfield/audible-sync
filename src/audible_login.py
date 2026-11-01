"""
Logging in to an Audible marketplace without a terminal, a browser driver or a password.

Two ways in, both ending in an `Authenticator` whose `to_dict()` is what `accounts.auth`
stores, and neither passing an Amazon password through the app:

**Device code** (the default). The app asks Amazon for a code pair, shows the user the
five-letter public code and the address to enter it at (`https://www.amazon.{domain}/code`),
and polls Amazon's registration endpoint with the private code until the user has signed
in - on any device, a phone included - and confirmed it. Amazon answers `401` until then
and `200` with the tokens after. The polling runs in a thread of the `PendingLogins`
store, so a UI only has to show the code and poll the login's status. This is the flow
the Audible apps use and it does not depend on the user's browser being one Amazon
likes, which the browser flow does ("Your device can't be used with this site").

**Browser** (the fallback, for pre-Amazon Audible accounts). OAuth with PKCE from the
`audible` library: a sign-in URL carrying a code challenge, the user signing in to Amazon
in *their own* browser, and Amazon redirecting to an `/ap/maplanding` page that shows
"page not found" but whose address carries the authorization code. The pasted address
is exchanged for tokens with `register`.

`PendingLogins` holds the logins in flight for both methods, in memory, each with a
`finish` callback the API gives it to turn the `Authenticator` into an account; a login
nobody finishes expires and is forgotten.
"""

import logging
import secrets
import string
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from textwrap import dedent
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from audible import Authenticator
from audible.localization import LOCALE_TEMPLATES, Locale
from audible.login import build_oauth_url, create_code_verifier
from audible.register import register

logger = logging.getLogger(__name__)

# How long a started browser login stays completable, and how long a finished or expired
# login of either kind stays readable afterwards
LOGIN_TTL = timedelta(minutes=15)

# Turns an `Authenticator` into an account and returns the account id; the API's business
Finish = Callable[[Authenticator], int]


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


# --- the device the app registers as ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """
    What the registration says about the app and its device.

    Mirrors what `audible` 0.13.1's `register` sends (Audible for iPhone 4.60 on iOS
    26.2); Audible refuses download licences to devices registered with older
    profiles, so this is kept in one place and changed with the library.
    """

    device_type: str
    device_model: str
    device_name: str
    os_version: str
    software_version: str
    app_name: str
    app_version: str
    user_agent: str


IPHONE = DeviceProfile(
    device_type="A2CZJZGLK2JJVM",
    device_model="iPhone",
    device_name="%FIRST_NAME%%FIRST_NAME_POSSESSIVE_STRING%%DUPE_STRATEGY_1ST%Audible for iPhone",
    os_version="26.2",
    software_version="46000856",
    app_name="Audible",
    app_version="4.60",
    # WebKit freezes the OS token: the app's web view reports 18_7 on an iOS 26 device
    user_agent=(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    ),
)


def _device_serial() -> str:
    """A new device serial: 40 ASCII letters and digits, the length the current apps use."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(40))


# --- the logins -------------------------------------------------------------------------


class LoginMethod(StrEnum):
    CODE = "code"
    BROWSER = "browser"


class LoginStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    FAILED = "failed"


class LoginFailed(Exception):
    """Amazon refused the code pair or the registration."""


class LoginExpired(LoginFailed):
    """The code was not confirmed before it expired."""


@dataclass(slots=True, kw_only=True)
class Login:
    """A login in flight, whichever way it is being done."""

    id: str
    method: LoginMethod
    country_code: str
    # The device serial the registration is made under
    serial: str
    # After this the login can no longer succeed
    expires_at: datetime
    status: LoginStatus = LoginStatus.PENDING
    # The account the login produced, once confirmed
    account_id: int | None = None
    # Why it failed, once it has
    error: str | None = None


@dataclass(slots=True, kw_only=True)
class BrowserLogin(Login):
    """A browser login that has been started: what `complete_login` needs besides the pasted URL."""

    method: LoginMethod = LoginMethod.BROWSER
    url: str
    code_verifier: bytes


@dataclass(slots=True, kw_only=True)
class CodeLogin(Login):
    """A device-code login that has been started: the code to show and what the polling needs."""

    method: LoginMethod = LoginMethod.CODE
    # What the user enters, and where
    code: str
    verification_url: str
    # The other half of the pair, which only the polling sends
    private_code: str
    # How often Amazon wants to be polled, in seconds
    interval: float


# --- the browser flow -------------------------------------------------------------------


def start_login(country_code: str, *, now: datetime | None = None, ttl: timedelta = LOGIN_TTL) -> BrowserLogin:
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
    return BrowserLogin(
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


def complete_login(pending: BrowserLogin, response_url: str) -> Authenticator:
    """
    Exchange the pasted URL for credentials.

    This is the one step of the browser flow that talks to Amazon: `register` trades the
    authorization code and the verifier `start_login` kept for the tokens an
    `Authenticator` needs.

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


# --- the device-code flow ---------------------------------------------------------------

_shared_http: httpx.Client | None = None
_shared_http_lock = threading.Lock()


def _http(client: httpx.Client | None) -> httpx.Client:
    """The client given, else one shared client for Amazon's auth endpoints."""
    global _shared_http
    if client is not None:
        return client
    with _shared_http_lock:
        if _shared_http is None:
            _shared_http = httpx.Client(timeout=30)
        return _shared_http


def _code_headers(domain: str, device: DeviceProfile) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": device.user_agent,
        "x-amzn-identity-auth-domain": f"api.amazon.{domain}",
    }


def _error_code(response: httpx.Response) -> str | None:
    """The error code in an Amazon auth response, lower-cased, if there is one."""
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    nested = data.get("response")
    error = data.get("error", nested.get("error") if isinstance(nested, dict) else None)
    if isinstance(error, str):
        return error.lower()
    if isinstance(error, dict):
        for key in ("code", "error_code", "error", "type"):
            if isinstance(error.get(key), str):
                return error[key].lower()
    return None


def start_code_login(
    country_code: str,
    *,
    now: datetime | None = None,
    http: httpx.Client | None = None,
    device: DeviceProfile = IPHONE,
) -> CodeLogin:
    """
    Ask Amazon for a code pair for a new device on a marketplace.

    The public code is what the user enters at `verification_url`; the private code is
    what the polling identifies itself with. The code is good for `expires_at`
    (Amazon's `expires_in`, ten minutes when asked for as here).

    Raises:
        ValueError: for an unknown marketplace
        LoginFailed: when Amazon refuses to create a code, or answers with something else
        httpx.HTTPError: when Amazon cannot be reached
    """
    locale = locale_for(country_code)
    serial = _device_serial()
    response = _http(http).post(
        f"https://api.amazon.{locale.domain}/auth/create/codepair",
        json={
            "code_data": {"device_serial": serial, "device_type": device.device_type, "domain": "Device"},
            "code_properties": {"code_duration": "600", "code_length": "5"},
        },
        headers=_code_headers(locale.domain, device),
    )
    error = _error_code(response)
    if response.status_code != 200 or error is not None:
        raise LoginFailed(
            f"Amazon refused to create a device code (HTTP {response.status_code}, {error or 'no error code'})"
        )
    data = response.json()
    public_code, private_code = data.get("public_code"), data.get("private_code")
    if not public_code or not private_code:
        raise LoginFailed("Amazon's code-pair response carries no code")
    try:
        expires_in = float(data.get("expires_in", 600))
        interval = float(data.get("polling_interval_in_seconds", 5))
    except (TypeError, ValueError):
        raise LoginFailed("Amazon's code-pair response is malformed") from None
    logger.info("Started a device-code login for Audible %s", locale.country_code)
    return CodeLogin(
        id=secrets.token_urlsafe(16),
        country_code=locale.country_code,
        serial=serial,
        expires_at=(now or datetime.now(UTC)) + timedelta(seconds=expires_in),
        code=public_code,
        verification_url=f"https://www.amazon.{locale.domain}/code",
        private_code=private_code,
        interval=max(1.0, interval),
    )


def _registration_body(login: CodeLogin, domain: str, device: DeviceProfile) -> dict[str, Any]:
    return {
        "requested_token_type": ["bearer", "mac_dms", "website_cookies"],
        "cookies": {"website_cookies": [], "domain": f".amazon.{domain}"},
        "registration_data": {
            "domain": "Device",
            "app_version": device.app_version,
            "device_serial": login.serial,
            "device_type": device.device_type,
            "device_name": device.device_name,
            "os_version": device.os_version,
            "software_version": device.software_version,
            "device_model": device.device_model,
            "app_name": device.app_name,
        },
        "auth_data": {
            "code_pair": {"public_code": login.code, "private_code": login.private_code},
            "use_global_authentication": "true",
        },
        "requested_extensions": ["device_info", "customer_info"],
    }


def credentials_from(success: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """
    The `Authenticator` fields in the `response.success` of a registration.

    The same shape `audible.register.register` returns, so the two flows store the same
    blob. `expires` is an absolute timestamp worked out from Amazon's `expires_in`.

    Raises:
        LoginFailed: when the response lacks a credential
    """
    try:
        tokens = success["tokens"]
        bearer, mac_dms = tokens["bearer"], tokens["mac_dms"]
        credentials = {
            "adp_token": mac_dms["adp_token"],
            "device_private_key": mac_dms["device_private_key"],
            "access_token": bearer["access_token"],
            "refresh_token": bearer["refresh_token"],
        }
        expires_in = int(bearer["expires_in"])
    except (KeyError, TypeError, ValueError):
        raise LoginFailed("the registration response lacks credentials") from None
    extensions = success.get("extensions") or {}
    website_cookies = {
        cookie["Name"]: str(cookie["Value"]).replace('"', "")
        for cookie in tokens.get("website_cookies") or []
        if isinstance(cookie, dict) and "Name" in cookie and "Value" in cookie
    }
    return {
        **credentials,
        "expires": ((now or datetime.now(UTC)) + timedelta(seconds=expires_in)).timestamp(),
        "website_cookies": website_cookies or None,
        "store_authentication_cookie": tokens.get("store_authentication_cookie"),
        "device_info": extensions.get("device_info"),
        "customer_info": extensions.get("customer_info"),
    }


def try_register(
    login: CodeLogin, *, http: httpx.Client | None = None, device: DeviceProfile = IPHONE
) -> dict[str, Any] | None:
    """
    One poll of Amazon's registration endpoint with the code pair.

    Returns the credentials (see `credentials_from`) once the user has confirmed the
    code, `None` while they have not - a `401 Unauthorized`, the only answer seen while
    waiting - and also `None`, with a warning, for a `429`, a `5xx` or a `200` whose body
    is not JSON, which are Amazon rather than the login and are retried on the next poll.

    Raises:
        LoginFailed: for any other answer: the code was refused, or has expired on
            Amazon's side
        httpx.HTTPError: when Amazon cannot be reached
    """
    locale = locale_for(login.country_code)
    response = _http(http).post(
        f"https://api.amazon.{locale.domain}/auth/register",
        json=_registration_body(login, locale.domain, device),
        headers=_code_headers(locale.domain, device),
    )
    error = _error_code(response)
    if response.status_code == 200:
        try:
            data = response.json()
        except ValueError:
            # A truncated or proxy-mangled answer, not a refusal: poll again like a 5xx
            logger.warning("Amazon answered a login poll with a body that is not JSON; trying again")
            return None
        success = data.get("response", {}).get("success") if isinstance(data, dict) else None
        if isinstance(success, dict):
            logger.info("Registered a new device with Audible %s", login.country_code)
            return credentials_from(success)
    if response.status_code == 401 and error == "unauthorized":
        return None
    if response.status_code == 429 or response.status_code >= 500:
        logger.warning("Amazon answered %d to a login poll; trying again", response.status_code)
        return None
    raise LoginFailed(
        f"Amazon refused the device registration (HTTP {response.status_code}, {error or 'no error code'})"
    )


def poll_code_login(
    login: CodeLogin,
    *,
    http: httpx.Client | None = None,
    sleep: Callable[[float], Any] = time.sleep,
    now: Callable[[], datetime] | None = None,
) -> Authenticator:
    """
    Poll until the user has confirmed the code, then build the `Authenticator`.

    Blocks for up to the code's lifetime, sleeping `login.interval` between polls; a
    network error is logged and waited out the same way rather than ending the login.

    Raises:
        LoginExpired: when the code's lifetime passes unconfirmed
        LoginFailed: when Amazon refuses the registration
    """
    now = now or (lambda: datetime.now(UTC))
    while now() < login.expires_at:
        try:
            registered = try_register(login, http=http)
        except httpx.HTTPError as error:
            logger.warning("Could not reach Amazon to poll the login (%s); trying again", error)
            registered = None
        if registered is not None:
            return Authenticator.from_dict({**registered, "locale_code": login.country_code, "with_username": False})
        sleep(login.interval)
    raise LoginExpired("the code expired before it was confirmed; start again")


# --- the store -------------------------------------------------------------------------


def discard_device(auth: Authenticator) -> bool:
    """
    Deregister a device a login registered but that could not be stored; best effort.

    Without this a failed store leaves the device on the user's Amazon device list with
    nothing holding its credentials. Returns whether Amazon took it; a failure is logged,
    never raised, because the caller is already reporting the error that led here.
    """
    try:
        auth.deregister_device()
    except Exception:
        logger.warning("Could not deregister the device of a login that was not stored", exc_info=True)
        return False
    logger.info("Deregistered the device of a login that could not be stored")
    return True


class PendingLogins:
    """
    The logins started and not yet finished, in memory, with the thread that polls each
    device-code login.

    In memory on purpose: a login is a minute's interaction, a restart in the middle of
    one just means starting again, and nothing secret is persisted. Every login carries
    the `finish` callback given when it was started, which turns the `Authenticator`
    into an account; a confirmed login records the account id it returned. A finished
    or expired login stays readable for `ttl` so a client polling it sees how it ended.
    """

    def __init__(
        self,
        *,
        ttl: timedelta = LOGIN_TTL,
        now: Callable[[], datetime] | None = None,
        http: httpx.Client | None = None,
        sleep: Callable[[float], Any] = time.sleep,
    ) -> None:
        self._ttl = ttl
        self._now = now or (lambda: datetime.now(UTC))
        self._http = http
        self._sleep = sleep
        self._lock = threading.Lock()
        self._logins: dict[str, tuple[BrowserLogin | CodeLogin, Finish]] = {}
        self._threads: dict[str, threading.Thread] = {}
        # Browser logins whose step two is in progress, so a second paste cannot register twice
        self._busy: set[str] = set()

    def start(self, country_code: str, finish: Finish) -> BrowserLogin:
        """Start a browser login; `complete` finishes it with the pasted address."""
        login = start_login(country_code, now=self._now(), ttl=self._ttl)
        with self._lock:
            self._prune()
            self._logins[login.id] = (login, finish)
        return replace(login)

    def start_code(self, country_code: str, finish: Finish) -> CodeLogin:
        """
        Start a device-code login and the thread that polls Amazon for it.

        Raises:
            ValueError: for an unknown marketplace
            LoginFailed, httpx.HTTPError: from `start_code_login`
        """
        login = start_code_login(country_code, now=self._now(), http=self._http)
        thread = threading.Thread(target=self._poll, args=(login,), name=f"login-{login.id}", daemon=True)
        with self._lock:
            self._prune()
            self._logins[login.id] = (login, finish)
            self._threads[login.id] = thread
        # The copy is taken before the thread runs, so what is handed back says `pending`
        started = replace(login)
        thread.start()
        return started

    def get(self, login_id: str) -> BrowserLogin | CodeLogin | None:
        """A copy of the login as it stands, or None if unknown or long finished."""
        with self._lock:
            self._prune()
            entry = self._logins.get(login_id)
            if entry is None:
                return None
            login = replace(entry[0])
        if login.status is LoginStatus.PENDING and login.expires_at <= self._now():
            login.status = LoginStatus.EXPIRED
        return login

    def complete(self, login_id: str, response_url: str) -> BrowserLogin:
        """
        Step two of a browser login: the pasted address becomes an account.

        Raises:
            KeyError: for a login that is unknown, expired, already finished, being
                finished, or not a browser login
            ValueError: for an address that carries no code; the login is left pending,
                so the right address can still be pasted
            Exception: from `register`, when Amazon rejects the code; the login is failed
        """
        with self._lock:
            self._prune()
            entry = self._logins.get(login_id)
            if entry is None or not isinstance(entry[0], BrowserLogin) or login_id in self._busy:
                raise KeyError(login_id)
            login, finish = entry
            if login.status is not LoginStatus.PENDING or login.expires_at <= self._now():
                raise KeyError(login_id)
            self._busy.add(login_id)
        try:
            authorization_code_from(response_url)  # the ValueError, before anything is spent
            try:
                auth = complete_login(login, response_url)
            except Exception as error:
                self._fail(login, f"Amazon rejected the login: {error}")
                raise
            self._finish(login, finish, auth)
        finally:
            with self._lock:
                self._busy.discard(login_id)
        return replace(login)

    def join(self, login_id: str, timeout: float | None = None) -> None:
        """Wait for a device-code login's polling thread; for tests and a tidy shutdown."""
        with self._lock:
            thread = self._threads.get(login_id)
        if thread is not None:
            thread.join(timeout)

    def _poll(self, login: CodeLogin) -> None:
        with self._lock:
            finish = self._logins[login.id][1]
        try:
            auth = poll_code_login(login, http=self._http, sleep=self._sleep, now=self._now)
        except LoginExpired:
            with self._lock:
                login.status = LoginStatus.EXPIRED
            logger.info("The device code for Audible %s expired unconfirmed", login.country_code)
            return
        except Exception as error:
            logger.warning("The device-code login for Audible %s failed", login.country_code, exc_info=True)
            self._fail(login, str(error))
            return
        try:
            self._finish(login, finish, auth)
        except Exception:
            logger.exception("Could not store the account for a confirmed login")

    def _finish(self, login: BrowserLogin | CodeLogin, finish: Finish, auth: Authenticator) -> None:
        try:
            account_id = finish(auth)
        except Exception as error:
            # Amazon has registered a device that nothing will ever hold or deregister
            discarded = discard_device(auth)
            self._fail(
                login,
                f"the login was confirmed but the account could not be stored: {error}"
                + ("; the new device was deregistered, log in again" if discarded else ""),
            )
            raise
        with self._lock:
            login.status = LoginStatus.CONFIRMED
            login.account_id = account_id

    def _fail(self, login: BrowserLogin | CodeLogin, error: str) -> None:
        with self._lock:
            login.status = LoginStatus.FAILED
            login.error = error

    def _prune(self) -> None:
        """Forget logins that expired `ttl` ago; the thread of a code login is gone by then."""
        cutoff = self._now() - self._ttl
        for login_id in [i for i, (login, _) in self._logins.items() if login.expires_at <= cutoff]:
            del self._logins[login_id]
            self._threads.pop(login_id, None)


# --- the terminal ----------------------------------------------------------------------


def login_with_code(
    country_code: str,
    *,
    echo: Callable[[str], Any] = print,
    http: httpx.Client | None = None,
    sleep: Callable[[float], Any] = time.sleep,
) -> Authenticator:
    """
    The device-code flow at a terminal: show the code, wait for it to be confirmed.

    `echo`, `http` and `sleep` are injectable so the CLI path is tested without a
    terminal or Amazon.

    Raises:
        LoginFailed: see `poll_code_login`; `LoginExpired` when nobody entered the code
    """
    login = start_code_login(country_code, http=http)
    minutes = max(1, round((login.expires_at - datetime.now(UTC)).total_seconds() / 60))
    echo(
        dedent(
            f"""
            On any device, open this address and sign in to Amazon if asked:

              {login.verification_url}

            then enter this code:

              {login.code}

            The code is good for {minutes} minutes. Waiting for it to be confirmed...
            """
        )
    )
    return poll_code_login(login, http=http, sleep=sleep)


def login_interactively(
    country_code: str,
    *,
    prompt: Callable[[str], str] = input,
    echo: Callable[[str], Any] = print,
) -> Authenticator:
    """
    The browser flow at a terminal: show the URL, read the pasted one back.

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
