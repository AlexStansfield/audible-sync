"""
Application settings: the one place that reads configuration and validates it.

Two sources. `config.ini` is read by `from_ini` and is where a fresh installation's
values come from; the `settings` table is read by `from_db` and is what the CLI, the
service and the API actually run on. `seed_settings_from_ini` copies the file into the
table exactly once, so after the first run the file is documentation and the table is
the truth - editable at runtime through the API, with no restart.

Every route produces the same frozen `Settings` object, so a value is parsed, anchored
and validated exactly once. Every path is anchored against the repo root (see
`src.paths`), so the process can be started from any working directory.

Note this module must never import `src.downloader`: the downloader imports
`Settings`, so the reverse edge would be a circular import.
"""

import configparser
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from src.database import get_settings, has_settings, save_settings
from src.encoding import DEFAULT_BITRATE, DEFAULT_FORMAT, validate_encoding
from src.naming import DEFAULT_FILENAME_TEMPLATE, DEFAULT_FOLDER_TEMPLATE, validate_templates
from src.paths import REPO_ROOT, resolve_path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE = REPO_ROOT / "config" / "config.ini"

# Resolved once, so the dataclass defaults below are plain constants
DEFAULT_DOWNLOAD_FOLDER = REPO_ROOT / "data" / "downloads"
DEFAULT_AUDIOBOOK_FOLDER = REPO_ROOT / "audiobooks"


def _default_auth_file() -> Path:
    """
    The location `audible quickstart` writes to.

    A factory rather than a plain default so the home directory is read when the
    settings are built, not when this module is imported.
    """
    return Path.home() / ".audible" / "audible.json"


DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_SYNC_INTERVAL_MINUTES = 6 * 60
# Below this a run would still be going when the next one was due, and every tick
# would cost Audible a library request for nothing.
MIN_SYNC_INTERVAL_MINUTES = 5


def validate_max_download(max_download: int | None) -> None:
    """
    Check the download limit up front, like the naming and encoding settings.

    Raises:
        ValueError: if the limit is set but not a positive number
    """
    if max_download is not None and max_download < 1:
        raise ValueError(f"sync max-download must be 1 or more, got {max_download}")


def validate_max_attempts(max_attempts: int) -> None:
    """
    Check the retry cap up front, like the download limit.

    Raises:
        ValueError: if the cap is not a positive number. Zero would fail every book on
            its first claim, before it had been tried even once
    """
    if max_attempts < 1:
        raise ValueError(f"sync max-attempts must be 1 or more, got {max_attempts}")


def validate_sync_interval(minutes: int) -> None:
    """
    Check the scheduler interval up front.

    Raises:
        ValueError: if the interval is shorter than `MIN_SYNC_INTERVAL_MINUTES`
    """
    if minutes < MIN_SYNC_INTERVAL_MINUTES:
        raise ValueError(f"sync interval-minutes must be {MIN_SYNC_INTERVAL_MINUTES} or more, got {minutes}")


def validate_webhook_url(url: str | None) -> None:
    """
    Check the webhook target is something an HTTP client could post to.

    Raises:
        ValueError: if the URL is set but is not http or https
    """
    if url is not None and not url.startswith(("http://", "https://")):
        raise ValueError(f"notifications webhook-url must start with http:// or https://, got {url!r}")


@dataclass(frozen=True, slots=True)
class Settings:
    """
    Validated application settings.

    Fields follow the sections of `config.ini`. The validators run in `__post_init__`,
    so no route into this class - `from_ini`, `from_db`, `with_changes` or a direct
    call - can produce settings that would fail part way through a run. Paths are
    normalised by the builders, not here, so the field types stay honest.
    """

    # [general]
    debug: bool = False

    # [sync]
    sync_enabled: bool = True
    sync_interval_minutes: int = DEFAULT_SYNC_INTERVAL_MINUTES
    max_download: int | None = None
    # Plain `int`, not `int | None`: unlike `max_download` there is no "unlimited"
    # reading here, because retrying forever is the bug this cap exists to fix.
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    # Whether a purchase seen for the first time joins the download queue
    auto_monitor_new: bool = True
    # Not a runtime setting: this is where an existing audible-cli login is imported from
    auth_file: Path = field(default_factory=_default_auth_file)

    # [folders]
    download_folder: Path = DEFAULT_DOWNLOAD_FOLDER
    audiobook_folder: Path = DEFAULT_AUDIOBOOK_FOLDER

    # [naming]
    folder_template: str = DEFAULT_FOLDER_TEMPLATE
    filename_template: str = DEFAULT_FILENAME_TEMPLATE

    # [encoding]
    encoding_format: str = DEFAULT_FORMAT
    bitrate: int = DEFAULT_BITRATE

    # [notifications]
    webhook_url: str | None = None

    def __post_init__(self) -> None:
        validate_templates(self.folder_template, self.filename_template)
        validate_encoding(self.encoding_format, self.bitrate)
        validate_max_download(self.max_download)
        validate_max_attempts(self.max_attempts)
        validate_sync_interval(self.sync_interval_minutes)
        validate_webhook_url(self.webhook_url)

    @classmethod
    def from_ini(cls, path: str | Path = DEFAULT_CONFIG_FILE) -> "Settings":
        """
        Build settings from an INI file, anchoring every relative path to the repo root.

        Args:
            path: Config file, defaulting to `config/config.ini` beside the code

        Raises:
            FileNotFoundError: if the config file does not exist. `configparser`
                ignores a missing file, which with a full set of fallbacks would
                mean a mistyped path silently ran on defaults
            ValueError: from the validators, on a bad template, format, bitrate or limit
        """
        path = resolve_path(path)
        if not path.is_file():
            raise FileNotFoundError(f"config file not found: {path}")

        config = configparser.ConfigParser()
        config.read(path, encoding="utf-8")

        # An empty `audible-auth-file =` means "unset", same as omitting the key
        auth_file = config.get("sync", "audible-auth-file", fallback="")

        return cls(
            debug=config.getboolean("general", "debug", fallback=False),
            sync_enabled=config.getboolean("sync", "enabled", fallback=True),
            sync_interval_minutes=config.getint("sync", "interval-minutes", fallback=DEFAULT_SYNC_INTERVAL_MINUTES),
            max_download=config.getint("sync", "max-download", fallback=None),
            max_attempts=config.getint("sync", "max-attempts", fallback=DEFAULT_MAX_ATTEMPTS),
            auto_monitor_new=config.getboolean("sync", "auto-monitor-new", fallback=True),
            auth_file=resolve_path(auth_file) if auth_file else _default_auth_file(),
            download_folder=resolve_path(config.get("folders", "downloads", fallback=DEFAULT_DOWNLOAD_FOLDER)),
            audiobook_folder=resolve_path(config.get("folders", "audiobooks", fallback=DEFAULT_AUDIOBOOK_FOLDER)),
            folder_template=config.get("naming", "folder", fallback=DEFAULT_FOLDER_TEMPLATE),
            filename_template=config.get("naming", "filename", fallback=DEFAULT_FILENAME_TEMPLATE),
            encoding_format=config.get("encoding", "format", fallback=DEFAULT_FORMAT),
            bitrate=config.getint("encoding", "bitrate", fallback=DEFAULT_BITRATE),
            webhook_url=config.get("notifications", "webhook-url", fallback="") or None,
        )

    @classmethod
    def from_db(cls) -> "Settings":
        """
        Build settings from the `settings` table, defaults filling in whatever it lacks.

        The table stores text, so each value goes through the parser for its key, which
        accepts the same spellings the INI file does. A key the table holds that this
        build does not know is ignored rather than fatal, so a database written by a
        newer version still reads. `init_db` must have run.

        Raises:
            ValueError: on a value that will not parse, naming the setting, or from the
                validators
        """
        stored = get_settings()
        values = {key: _PARSERS[key](key, text) for key, text in stored.items() if key in _PARSERS}
        return cls(**values)

    def to_db_values(self) -> dict[str, str]:
        """
        Every runtime setting as the text the `settings` table stores.

        `None` is written as an empty string, the same "unset" the INI file uses, and
        flags as `true`/`false`. `from_db` reverses this exactly.
        """
        return {key: _format(getattr(self, key)) for key in DB_SETTING_KEYS}

    def with_changes(self, **changes: Any) -> "Settings":
        """
        A copy with some runtime settings replaced, validated like any other build.

        This is what a settings update goes through, so it refuses a key that is not a
        runtime setting - a typo, or `auth_file`, which is import-only - and normalises
        the shapes a caller is likely to send: a folder given as text is anchored like
        one read from the file, and an empty webhook URL means "unset".

        Raises:
            ValueError: for an unknown key, or from the validators
        """
        unknown = sorted(set(changes) - set(DB_SETTING_KEYS))
        if unknown:
            raise ValueError(f"not a runtime setting: {', '.join(unknown)}")

        for key in ("download_folder", "audiobook_folder"):
            if key in changes:
                changes[key] = resolve_path(changes[key])
        if changes.get("webhook_url") == "":
            changes["webhook_url"] = None

        return replace(self, **changes)

    def create_folders(self) -> None:
        """Create the download and audiobook folders if they do not already exist."""
        self.download_folder.mkdir(parents=True, exist_ok=True)
        self.audiobook_folder.mkdir(parents=True, exist_ok=True)


# The settings the table holds and the API may change. `auth_file` is the one field left
# out: it is where an existing login is imported from, not something a run reads.
DB_SETTING_KEYS: tuple[str, ...] = tuple(f.name for f in fields(Settings) if f.name != "auth_file")


def _format(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parse_bool(key: str, text: str) -> bool:
    # The same spellings configparser accepts (yes/no, on/off, 1/0, true/false)
    try:
        return configparser.ConfigParser.BOOLEAN_STATES[text.strip().lower()]
    except KeyError:
        raise ValueError(f"{key} must be true or false, got {text!r}") from None


def _parse_int(key: str, text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"{key} must be a whole number, got {text!r}") from None


def _optional(parse: Callable[[str, str], Any]) -> Callable[[str, str], Any]:
    """An empty string reads as None, the same "unset" the INI file uses."""
    return lambda key, text: None if text == "" else parse(key, text)


def _parse_text(_key: str, text: str) -> str:
    return text


def _parse_path(_key: str, text: str) -> Path:
    return resolve_path(text)


# One parser per runtime setting; `from_db` looks the key up here. A test checks the keys
# match `DB_SETTING_KEYS` so a new field cannot be added without saying how it reads.
_PARSERS: dict[str, Callable[[str, str], Any]] = {
    "debug": _parse_bool,
    "sync_enabled": _parse_bool,
    "sync_interval_minutes": _parse_int,
    "max_download": _optional(_parse_int),
    "max_attempts": _parse_int,
    "auto_monitor_new": _parse_bool,
    "download_folder": _parse_path,
    "audiobook_folder": _parse_path,
    "folder_template": _parse_text,
    "filename_template": _parse_text,
    "encoding_format": _parse_text,
    "bitrate": _parse_int,
    "webhook_url": _optional(_parse_text),
}


def seed_settings_from_ini(path: str | Path = DEFAULT_CONFIG_FILE) -> bool:
    """
    Copy `config.ini` into the `settings` table, once.

    Only an empty table is seeded: after that the table is what the user has changed
    through the API, and the file must not overwrite it on every start. A missing
    file is not an error here - the defaults simply apply - because the service does
    not need a file at all; `from_ini` itself still raises, so the CLI path that reads
    the file directly cannot run on defaults by mistake.

    Returns:
        Whether the table was seeded on this call
    """
    if has_settings():
        return False

    path = resolve_path(path)
    if not path.is_file():
        logger.info("No config file at %s, starting from the default settings", path)
        return False

    save_settings(Settings.from_ini(path).to_db_values())
    logger.info("Seeded the settings table from %s", path)
    return True
