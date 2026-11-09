"""
Encryption at rest for the secrets the database holds: the Audible credentials of each
account and the API token.

The point is the database leaving the host - a backup, a copy sent along with a bug
report, a volume snapshot - not an attacker already on it. The stored blob of an
account is a refresh token and a device private key, which make whoever holds them
that device as far as Amazon is concerned, so `data/` on its own must not be enough.
The key therefore never lives under `data/`: it comes from the `AUDIBLE_SYNC_SECRET_KEY`
environment variable, or from a key file (`AUDIBLE_SYNC_SECRET_KEY_FILE`, default
`secrets/secret.key`) that is generated on the first start when there is none.
Backing up `data/` is then safe to do freely; backing up the key too restores the
logins; losing the key means logging in again, which the app already handles.

Fernet (AES-128-CBC with an HMAC, from `cryptography`) does the work: the ciphertext
is text, authenticated, and carries a version byte, so a value that does not decrypt
is reported rather than misread. An encrypted value is stored with the `enc:` prefix,
which is how `decrypt` tells it from a value written before there was a key and
leaves the migration of old rows to `database.encrypt_stored_secrets`.

Module state on purpose: one key per process, configured once at start by the entry
points (`main._prepare`, `service.build`) after logging is up, and read by
`audible_sync.database` whenever a secret is written or read. Tests configure a fresh key per
test through the autouse fixture in `tests/conftest.py`.
"""

import logging
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken

from audible_sync.paths import REPO_ROOT, resolve_path

logger = logging.getLogger(__name__)

KEY_ENV = "AUDIBLE_SYNC_SECRET_KEY"
KEY_FILE_ENV = "AUDIBLE_SYNC_SECRET_KEY_FILE"
DEFAULT_KEY_FILE = REPO_ROOT / "secrets" / "secret.key"

# What an encrypted value is stored as, so a plaintext one from before there was a key
# can be told apart (and migrated) rather than fed to the cipher
PREFIX = "enc:"


class SecretKeyError(ValueError):
    """The key is missing, malformed, or not the one the stored secrets were encrypted with."""


class WrongKey(SecretKeyError):
    """A stored secret does not decrypt with the configured key."""


_cipher: Fernet | None = None


def _cipher_for(key: bytes | str, source: str) -> Fernet:
    try:
        return Fernet(key)
    except (ValueError, TypeError) as error:
        raise SecretKeyError(
            f"The secret key from {source} is not a valid key (44 characters of URL-safe base64, "
            f"as `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` "
            f"prints): {error}"
        ) from None


def generate_key() -> bytes:
    """A fresh key, as the key file or the environment variable holds it."""
    return Fernet.generate_key()


def load_secret_key(env: dict[str, str] | None = None) -> bytes:
    """
    The key this installation encrypts its secrets with.

    `AUDIBLE_SYNC_SECRET_KEY` wins when set. Otherwise the key file - the path in
    `AUDIBLE_SYNC_SECRET_KEY_FILE`, else `secrets/secret.key` - is read, or generated
    with owner-only permissions when there is none yet, which is what a first start does.

    Raises:
        SecretKeyError: for a key that is not a Fernet key, or a key file that cannot be read
    """
    env = os.environ if env is None else env
    configured = (env.get(KEY_ENV) or "").strip()
    if configured:
        _cipher_for(configured, KEY_ENV)
        return configured.encode()

    path = _key_file(env)
    if path.is_file():
        key = path.read_bytes().strip()
        _cipher_for(key, str(path))
        return key
    if path.exists():
        raise SecretKeyError(f"The secret key file {path} is not a file")
    return _generate_key_file(path)


def _key_file(env: Mapping[str, str]) -> Path:
    return resolve_path(env[KEY_FILE_ENV]) if env.get(KEY_FILE_ENV) else DEFAULT_KEY_FILE


@dataclass(frozen=True)
class KeySource:
    """Where `load_secret_key` takes the key from: the environment, or a file and its path."""

    kind: Literal["env", "file"]
    path: Path | None = None


def key_source(env: dict[str, str] | None = None) -> KeySource:
    """
    Where the key comes from, by the same rules as `load_secret_key`, reading nothing.

    For the Settings screen's reminder to back the key up: the file is what to copy.
    """
    env = os.environ if env is None else env
    if (env.get(KEY_ENV) or "").strip():
        return KeySource("env")
    return KeySource("file", _key_file(env))


def _generate_key_file(path: Path) -> bytes:
    key = generate_key()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(stat.S_IRWXU)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key + b"\n")
    except OSError as error:
        raise SecretKeyError(f"Could not create the secret key file {path}: {error}") from None
    logger.warning(
        "Generated a new secret key at %s. Back it up separately from data/: the stored Audible "
        "credentials cannot be read without it, and would need a new login",
        path,
    )
    return key


def configure(key: bytes | str | None) -> None:
    """Set the key the process encrypts and decrypts with; `None` forgets it."""
    global _cipher
    _cipher = None if key is None else _cipher_for(key, "configure()")


def is_configured() -> bool:
    return _cipher is not None


def _require_cipher() -> Fernet:
    if _cipher is None:
        raise SecretKeyError("No secret key is configured; the entry point must call configure() first")
    return _cipher


def is_encrypted(stored: str) -> bool:
    return stored.startswith(PREFIX)


def encrypt(text: str) -> str:
    """The stored form of a secret: `enc:` and the Fernet token of `text`."""
    return PREFIX + _require_cipher().encrypt(text.encode()).decode()


def decrypt(stored: str) -> str:
    """
    The secret back out of its stored form.

    A value without the prefix is one written before there was a key and is returned as
    it is; `encrypt_stored_secrets` rewrites those on the next start.

    Raises:
        WrongKey: when the value was encrypted with a different key
        SecretKeyError: when no key is configured
    """
    if not is_encrypted(stored):
        return stored
    try:
        return _require_cipher().decrypt(stored[len(PREFIX) :].encode()).decode()
    except InvalidToken:
        raise WrongKey(
            f"A stored secret does not decrypt with the configured key: has {KEY_ENV} or the key file changed? "
            "Restore the key that was in use, or log the account in again"
        ) from None
