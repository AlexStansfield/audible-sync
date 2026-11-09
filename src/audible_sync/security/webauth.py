"""
Signing in to the web app: the one user, its password, and the browser sessions.

There is one user and no sign-up. The first start creates `admin` with a generated
password that the service logs once (`ensure_admin`), the way it logs a generated API
token. The username and a flag saying the generated password is still in use are kept
in the settings table under keys `Settings.from_db` does not know, so they never show
in `GET /api/settings`; the password hash is kept there too, **encrypted** with the
process's key like the other secrets (`audible_sync.security.crypto`).

A browser signs in once and is then known by an HTTP-only cookie holding a random
session token. The database keeps only the token's SHA-256, so a copy of the database
cannot be used to take over a session. Sessions live in the database rather than in
memory so "Keep me signed in" survives a restart, and so the CLI's `reset-password`
signs out the browsers of a service running beside it.

Imports `database` and `crypto` only; the API and the CLI build on it.
"""

import hashlib
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from audible_sync.database import (
    WEB_PASSWORD_SETTING,
    add_session,
    delete_expired_sessions,
    delete_session,
    delete_sessions,
    get_session,
    get_settings,
    save_settings,
    touch_session,
)
from audible_sync.security import crypto

logger = logging.getLogger(__name__)

WEB_USERNAME_SETTING = "web_username"
WEB_PASSWORD_GENERATED_SETTING = "web_password_generated"
DEFAULT_USERNAME = "admin"

MIN_PASSWORD_LENGTH = 12
# scrypt's cost grows with the input; nothing anyone types is this long
MAX_PASSWORD_LENGTH = 1024
MAX_USERNAME_LENGTH = 64

SESSION_COOKIE = "audible_sync_session"
# "Keep me signed in": fixed from the sign-in, matching the cookie's Max-Age
PERSISTENT_SESSION = timedelta(days=30)
# Without it: the cookie lasts until the browser closes, and the server forgets the
# session after this long unused
BROWSER_SESSION = timedelta(hours=12)
# A UI polls /api/status every few seconds; recording each use would be a write per poll
TOUCH_INTERVAL = timedelta(minutes=1)

# scrypt with 16 MiB of memory: OWASP's recommended minimum, about 50 ms a check. The
# parameters are stored with each hash, so raising them later leaves old hashes readable.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
_SALT_BYTES = 16
_HASH_BYTES = 32


@dataclass(frozen=True, slots=True)
class User:
    username: str
    # Whether the password is still the one generated on the first start (or by a reset)
    generated_password: bool


# --- passwords -----------------------------------------------------------------


def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt$hash`, salt and hash in hex."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def check_password(password: str, encoded: str) -> bool:
    """Whether `password` matches the stored hash; False for a hash that cannot be read."""
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        digest = _scrypt(password, bytes.fromhex(salt), int(n), int(r), int(p))
        return secrets.compare_digest(digest, bytes.fromhex(expected))
    except ValueError:
        return False


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    # maxmem a little over what n and r need (128 * n * r bytes), which the default of
    # 32 MiB would refuse for anything above the current parameters
    return hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, maxmem=256 * n * r + 1024 * 1024, dklen=_HASH_BYTES
    )


# A hash checked when the username is wrong, so a wrong username takes as long as a
# wrong password and the time taken does not say which it was
_dummy_hash: str | None = None


def _dummy() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(16))
    return _dummy_hash


def _password_matches(password: str, encoded: str | None) -> bool:
    """
    Whether `password` matches `encoded`, always paying for one hash.

    With no stored hash the dummy is checked instead, so having no user (or an
    unreadable hash) takes as long as a wrong password. The hash runs *before* the
    result is combined, so no short-circuit can skip it.
    """
    matched = check_password(password, encoded or _dummy())
    return matched and encoded is not None


def validate_password(password: str) -> None:
    """
    Raises:
        ValueError: for a password shorter than `MIN_PASSWORD_LENGTH` or absurdly long
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"The password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"The password must be at most {MAX_PASSWORD_LENGTH} characters")


def _clean_username(username: str) -> str:
    username = username.strip()
    if not username:
        raise ValueError("The username cannot be empty")
    if len(username) > MAX_USERNAME_LENGTH:
        raise ValueError(f"The username must be at most {MAX_USERNAME_LENGTH} characters")
    return username


# --- the user ------------------------------------------------------------------


def _stored_hash(values: dict[str, str]) -> str | None:
    """The decrypted password hash, or None when there is none or it cannot be read."""
    stored = values.get(WEB_PASSWORD_SETTING)
    if not stored:
        return None
    try:
        return crypto.decrypt(stored)
    except crypto.WrongKey:
        return None


def password_readable() -> bool:
    """
    Whether the stored password hash, if any, decrypts under this process's key.

    False means another process - the service - wrote it under another key: a password
    stored from here now would be unreadable to it, which is what `reset-password`
    checks before doing anything.
    """
    values = get_settings()
    # Nothing stored is readable; something stored must decrypt
    return not values.get(WEB_PASSWORD_SETTING) or _stored_hash(values) is not None


def current_user() -> User | None:
    """The user, or None before `ensure_admin` has run."""
    values = get_settings()
    username = values.get(WEB_USERNAME_SETTING)
    if not username:
        return None
    return User(username=username, generated_password=values.get(WEB_PASSWORD_GENERATED_SETTING) == "true")


def ensure_admin() -> str | None:
    """
    Create the user on the first start; return the generated password, or None.

    Also generates a new password when the stored hash cannot be decrypted - the key
    changed - logging an ERROR, as the API token does: losing the key costs a sign-in,
    not access to the app. The username is kept then, and every session is ended. The caller logs the password
    once; it is not stored anywhere it could be read back.
    """
    values = get_settings()
    username = values.get(WEB_USERNAME_SETTING)
    stored = values.get(WEB_PASSWORD_SETTING)
    if username and stored:
        if _stored_hash(values) is not None:
            return None
        logger.error("The stored web app password cannot be read under this key, generating a new one")
    password = _generated_password()
    save_settings(
        {
            WEB_USERNAME_SETTING: username or DEFAULT_USERNAME,
            WEB_PASSWORD_SETTING: crypto.encrypt(hash_password(password)),
            WEB_PASSWORD_GENERATED_SETTING: "true",
        }
    )
    # Session rows do not depend on the key, so they would outlive it: a key replaced
    # because it leaked must not leave the browsers of before signed in
    end_all_sessions()
    return password


def _generated_password() -> str:
    # 16 URL-safe characters, about 96 bits: comfortably over MIN_PASSWORD_LENGTH
    return secrets.token_urlsafe(12)


def verify(username: str, password: str) -> bool:
    """
    Whether these are the user's username and password.

    Both are checked in constant time, and the password hash runs even when the
    username is wrong, so neither the answer nor the time taken says which was wrong.
    """
    values = get_settings()
    stored_username = values.get(WEB_USERNAME_SETTING) or ""
    encoded = _stored_hash(values)
    password_ok = _password_matches(password, encoded)
    # Trimmed as `set_username` trims what it stores: a stray space from a password
    # manager is not a different user
    username_ok = secrets.compare_digest(username.strip().encode(), stored_username.encode())
    return password_ok and username_ok and bool(stored_username)


def check_current_password(password: str) -> bool:
    """Whether `password` is the user's, for a change that asks for it again."""
    return _password_matches(password, _stored_hash(get_settings()))


def set_password(password: str, *, generated: bool = False) -> None:
    """
    Store a new password.

    Raises:
        ValueError: for a password `validate_password` refuses
    """
    validate_password(password)
    save_settings(
        {
            WEB_PASSWORD_SETTING: crypto.encrypt(hash_password(password)),
            WEB_PASSWORD_GENERATED_SETTING: "true" if generated else "false",
        }
    )


def set_username(username: str) -> str:
    """
    Store a new username, trimmed, and return it.

    Raises:
        ValueError: for an empty or over-long username
    """
    username = _clean_username(username)
    save_settings({WEB_USERNAME_SETTING: username})
    return username


def reset_password(*, reset_username: bool = False) -> str:
    """
    The way back from a forgotten password, run on the server: a new generated
    password, every session ended, and with `reset_username` the username set back to
    `admin`. Returns the password for the caller to show.
    """
    password = _generated_password()
    values = {
        WEB_PASSWORD_SETTING: crypto.encrypt(hash_password(password)),
        WEB_PASSWORD_GENERATED_SETTING: "true",
    }
    if reset_username or not get_settings().get(WEB_USERNAME_SETTING):
        values[WEB_USERNAME_SETTING] = DEFAULT_USERNAME
    save_settings(values)
    end_all_sessions()
    return password


# --- sessions ------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    # The same shape as database._utcnow, so stored times compare as text
    return moment.astimezone(UTC).replace(microsecond=0).isoformat()


def _session_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(*, persistent: bool, now: datetime | None = None) -> str:
    """Start a session and return the token for the cookie; only its hash is stored."""
    now = now or _now()
    delete_expired_sessions(_iso(now))
    token = secrets.token_urlsafe(32)
    lifetime = PERSISTENT_SESSION if persistent else BROWSER_SESSION
    add_session(
        _session_id(token),
        persistent=persistent,
        created_at=_iso(now),
        expires_at=_iso(now + lifetime),
    )
    return token


def check_session(token: str, *, now: datetime | None = None) -> bool:
    """
    Whether the token is a live session, recording the use.

    A browser session slides: each use (recorded at most once a `TOUCH_INTERVAL`)
    pushes its end `BROWSER_SESSION` further out. A persistent one ends where the
    cookie does, `PERSISTENT_SESSION` after the sign-in.
    """
    now = now or _now()
    session_id = _session_id(token)
    session = get_session(session_id)
    if session is None:
        return False
    if session["expires_at"] <= _iso(now):
        delete_session(session_id)
        return False
    if session["last_seen_at"] <= _iso(now - TOUCH_INTERVAL):
        expires_at = session["expires_at"] if session["persistent"] else _iso(now + BROWSER_SESSION)
        touch_session(session_id, last_seen_at=_iso(now), expires_at=expires_at)
    return True


def end_session(token: str) -> None:
    delete_session(_session_id(token))


def end_other_sessions(token: str | None) -> int:
    """End every session but this one (every session when `token` is None); return how many."""
    return delete_sessions(keep=_session_id(token) if token else None)


def end_all_sessions() -> int:
    return delete_sessions()


# --- failed sign-ins -----------------------------------------------------------


class LoginThrottle:
    """
    Sign-in attempts per client, in memory: after `max_failures` within `window`, the
    client is refused until the oldest of them falls out of the window.

    An attempt counts as a failure *before* the password is checked (`attempt`), and a
    success takes it back (`succeeded` clears the client). Checking first and counting
    after would let a burst of parallel requests all pass the check during the 50 ms
    each hash takes; counting first means at most `max_failures` are ever hashed at
    once for one client.

    Keyed by IP address. Behind a reverse proxy every client shares one address, so a
    run of failures locks out everyone for a while - acceptable for a single-user app,
    and better than trusting a forwarded header anyone can set.
    """

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window: timedelta = timedelta(minutes=15),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window = window.total_seconds()
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        # Every client, not only the one asking: an address that never comes back
        # would otherwise be kept for the life of the process
        for key in list(self._failures):
            recent = [t for t in self._failures[key] if now - t < self.window]
            if recent:
                self._failures[key] = recent
            else:
                del self._failures[key]

    def attempt(self, key: str) -> int | None:
        """
        Count an attempt by `key` and return None, or return the seconds until it may
        try again without counting anything.
        """
        with self._lock:
            now = self._clock()
            self._prune(now)
            recent = self._failures.get(key, [])
            if len(recent) >= self.max_failures:
                # Free once enough attempts have aged out to leave room for one more
                oldest = recent[-self.max_failures]
                return max(1, int(oldest + self.window - now + 0.999))
            self._failures.setdefault(key, []).append(now)
            return None

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
