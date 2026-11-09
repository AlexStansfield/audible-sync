"""
The API's bearer token: where it comes from, and replacing it while the service runs.

The token is one of `AUDIBLE_SYNC_API_TOKEN` (`env`) or a token generated once and
stored encrypted in the settings table (`stored`). The app holds it in an `ApiToken`
rather than a plain string so a regeneration takes effect on the very next request,
without a restart. Imports `database` and `crypto` only.
"""

import logging
import secrets
import threading
from dataclasses import dataclass, field
from typing import Literal

from audible_sync import crypto
from audible_sync.database import API_TOKEN_SETTING, get_settings, save_settings

logger = logging.getLogger(__name__)

TokenSource = Literal["env", "stored"]

# A token shorter than this shows no characters at all: four of a short token is most of it
MIN_MASKED_LENGTH = 12


class TokenFromEnv(Exception):
    """The token is set by `AUDIBLE_SYNC_API_TOKEN`; it is changed there."""


@dataclass
class ApiToken:
    """The token the API requires, and where it came from. Shared, and replaced in place."""

    value: str
    source: TokenSource
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __repr__(self) -> str:
        # Never the value: a token in a log line is the API handed over
        return f"ApiToken(source={self.source!r}, last4={self.last4!r})"

    @property
    def last4(self) -> str:
        """The last four characters, or "" for a token too short to show any of."""
        return self.value[-4:] if len(self.value) >= MIN_MASKED_LENGTH else ""

    def regenerate(self) -> str:
        """
        Store a new token and require it from now on; the old one stops working at once.

        Returns the new token, which the caller shows once. It is not logged.

        Raises:
            TokenFromEnv: when the token comes from the environment - a stored one
                would be overridden again by the next start
        """
        if self.source == "env":
            raise TokenFromEnv("The API token is set by AUDIBLE_SYNC_API_TOKEN; change it there")
        with self._lock:
            token = _new_token()
            self.value = token
        logger.warning("The API token was regenerated; the previous one no longer works")
        return token


def _new_token() -> str:
    token = secrets.token_urlsafe(32)
    save_settings({API_TOKEN_SETTING: crypto.encrypt(token)})
    return token


def resolve_api_token(configured: str | None) -> ApiToken:
    """
    The token the API will require: the configured one, else the stored one, else a new
    one stored for next time.

    Generated once rather than on every start, so the token copied from the log keeps
    working across restarts. It is logged in full only on the start that generates it:
    a log handed over with a bug report must not hand over the API. Later starts log
    where it came from and its last four characters, enough to tell which one is in use.
    """
    if configured:
        logger.info("API token: from AUDIBLE_SYNC_API_TOKEN")
        return ApiToken(configured, "env")
    stored = get_settings().get(API_TOKEN_SETTING)
    if stored:
        try:
            token = crypto.decrypt(stored)
        except crypto.WrongKey as error:
            logger.error("The stored API token cannot be read, generating a new one: %s", error)
        else:
            logger.info("API token: the stored one, ending ...%s", token[-4:])
            return ApiToken(token, "stored")
    token = _new_token()
    logger.warning(
        "Generated a new API token: %s - copy it now, it is not logged again "
        "(set AUDIBLE_SYNC_API_TOKEN to choose your own)",
        token,
    )
    return ApiToken(token, "stored")
