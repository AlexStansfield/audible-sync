"""
Application settings: the one place that reads configuration and validates it.

The CLI, and later the API service and the scheduler, all work from the same frozen
`Settings` object rather than from loose keyword arguments, so a value is parsed,
anchored and validated exactly once. `from_ini` is the only source today;
`from_db` follows with the settings table.

Every path is anchored against the repo root (see `src.paths`), so the process can
be started from any working directory.

Note this module must never import `src.downloader`: the downloader imports
`Settings`, so the reverse edge would be a circular import.
"""

import configparser
from dataclasses import dataclass, field
from pathlib import Path

from src.encoding import DEFAULT_BITRATE, DEFAULT_FORMAT, validate_encoding
from src.naming import DEFAULT_FILENAME_TEMPLATE, DEFAULT_FOLDER_TEMPLATE, validate_templates
from src.paths import REPO_ROOT, resolve_path

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


@dataclass(frozen=True, slots=True)
class Settings:
    """
    Validated application settings.

    Fields follow the sections of `config.ini`. The three validators run in
    `__post_init__`, so no route into this class - `from_ini`, `dataclasses.replace`
    or a direct call - can produce settings that would fail part way through a run.
    Paths are normalised by the builders, not here, so the field types stay honest.
    """

    # [general]
    debug: bool = False

    # [sync]
    max_download: int | None = None
    # Plain `int`, not `int | None`: unlike `max_download` there is no "unlimited"
    # reading here, because retrying forever is the bug this cap exists to fix.
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
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

    def __post_init__(self) -> None:
        validate_templates(self.folder_template, self.filename_template)
        validate_encoding(self.encoding_format, self.bitrate)
        validate_max_download(self.max_download)
        validate_max_attempts(self.max_attempts)

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
            max_download=config.getint("sync", "max-download", fallback=None),
            max_attempts=config.getint("sync", "max-attempts", fallback=DEFAULT_MAX_ATTEMPTS),
            auth_file=resolve_path(auth_file) if auth_file else _default_auth_file(),
            download_folder=resolve_path(config.get("folders", "downloads", fallback=DEFAULT_DOWNLOAD_FOLDER)),
            audiobook_folder=resolve_path(config.get("folders", "audiobooks", fallback=DEFAULT_AUDIOBOOK_FOLDER)),
            folder_template=config.get("naming", "folder", fallback=DEFAULT_FOLDER_TEMPLATE),
            filename_template=config.get("naming", "filename", fallback=DEFAULT_FILENAME_TEMPLATE),
            encoding_format=config.get("encoding", "format", fallback=DEFAULT_FORMAT),
            bitrate=config.getint("encoding", "bitrate", fallback=DEFAULT_BITRATE),
        )

    def create_folders(self) -> None:
        """Create the download and audiobook folders if they do not already exist."""
        self.download_folder.mkdir(parents=True, exist_ok=True)
        self.audiobook_folder.mkdir(parents=True, exist_ok=True)
