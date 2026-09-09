import dataclasses
from pathlib import Path

import pytest

from src.paths import REPO_ROOT
from src.settings import Settings, validate_max_attempts, validate_max_download


def write_config(tmp_path, body: str) -> Path:
    path = tmp_path / "config.ini"
    path.write_text(body)
    return path


FULL_CONFIG = """
[general]
debug = true

[sync]
max-download = 5
max-attempts = 4
audible-auth-file = /keys/audible.json

[folders]
downloads = /var/tmp/downloads
audiobooks = /mnt/media/audiobooks

[naming]
folder = {author}/{title}
filename = {title} ({year})

[encoding]
format = oga
bitrate = 48
"""


def test_from_ini_reads_every_setting(tmp_path):
    settings = Settings.from_ini(write_config(tmp_path, FULL_CONFIG))

    assert settings.debug is True
    assert settings.max_download == 5
    assert settings.max_attempts == 4
    assert settings.auth_file == Path("/keys/audible.json")
    assert settings.download_folder == Path("/var/tmp/downloads")
    assert settings.audiobook_folder == Path("/mnt/media/audiobooks")
    assert settings.folder_template == "{author}/{title}"
    assert settings.filename_template == "{title} ({year})"
    assert settings.encoding_format == "oga"
    assert settings.bitrate == 48


def test_from_ini_falls_back_to_every_default(tmp_path):
    """A config with nothing in it must produce the same settings as no config at all."""
    settings = Settings.from_ini(write_config(tmp_path, "[general]\n"))

    assert settings == Settings()


def test_from_ini_defaults_a_missing_folders_section(tmp_path):
    """This section used to have no fallback, so an incomplete config died with a
    bare KeyError - after the folders had already been created."""
    settings = Settings.from_ini(write_config(tmp_path, "[general]\ndebug = false\n"))

    assert settings.download_folder == REPO_ROOT / "data" / "downloads"
    assert settings.audiobook_folder == REPO_ROOT / "audiobooks"


def test_from_ini_defaults_a_single_missing_folder_key(tmp_path):
    settings = Settings.from_ini(write_config(tmp_path, "[folders]\naudiobooks = /mnt/books\n"))

    assert settings.download_folder == REPO_ROOT / "data" / "downloads"
    assert settings.audiobook_folder == Path("/mnt/books")


def test_relative_folders_are_anchored_to_the_repo_root(tmp_path):
    """The shipped config uses relative paths, which used to follow the working
    directory rather than the application."""
    body = "[folders]\ndownloads = data/downloads\naudiobooks = audiobooks\n"
    settings = Settings.from_ini(write_config(tmp_path, body))

    assert settings.download_folder == REPO_ROOT / "data" / "downloads"
    assert settings.audiobook_folder == REPO_ROOT / "audiobooks"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("audible.json", REPO_ROOT / "audible.json"),
        ("/keys/audible.json", Path("/keys/audible.json")),
    ],
)
def test_auth_file_paths_are_resolved(tmp_path, configured, expected):
    body = f"[sync]\naudible-auth-file = {configured}\n"

    assert Settings.from_ini(write_config(tmp_path, body)).auth_file == expected


def test_auth_file_expands_home(tmp_path):
    body = "[sync]\naudible-auth-file = ~/keys/audible.json\n"

    assert Settings.from_ini(write_config(tmp_path, body)).auth_file == Path.home() / "keys" / "audible.json"


@pytest.mark.parametrize("body", ["[sync]\n", "[sync]\naudible-auth-file =\n"])
def test_auth_file_falls_back_to_the_audible_cli_location(tmp_path, body):
    """An empty value means unset, the same as omitting the key."""
    expected = Path.home() / ".audible" / "audible.json"

    assert Settings.from_ini(write_config(tmp_path, body)).auth_file == expected


def test_auth_file_default_reads_home_when_the_settings_are_built(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert Settings().auth_file == tmp_path / ".audible" / "audible.json"


@pytest.mark.parametrize(
    ("body", "expected"),
    [("[general]\ndebug = true\n", True), ("[general]\ndebug = false\n", False), ("[general]\n", False)],
)
def test_debug_flag(tmp_path, body, expected):
    assert Settings.from_ini(write_config(tmp_path, body)).debug is expected


@pytest.mark.parametrize(
    ("body", "expected"), [("[sync]\nmax-download = 10\n", 10), ("[sync]\n", None), ("[general]\n", None)]
)
def test_max_download(tmp_path, body, expected):
    assert Settings.from_ini(write_config(tmp_path, body)).max_download == expected


@pytest.mark.parametrize(("body", "expected"), [("[sync]\nmax-attempts = 5\n", 5), ("[sync]\n", 3), ("[general]\n", 3)])
def test_max_attempts(tmp_path, body, expected):
    assert Settings.from_ini(write_config(tmp_path, body)).max_attempts == expected


def test_from_ini_raises_on_a_missing_config_file(tmp_path):
    """configparser ignores a path that does not exist, so a typo would otherwise
    run silently on defaults."""
    with pytest.raises(FileNotFoundError, match="config file not found"):
        Settings.from_ini(tmp_path / "nope.ini")


def test_from_ini_resolves_a_relative_config_path(tmp_path):
    with pytest.raises(FileNotFoundError, match=str(REPO_ROOT)):
        Settings.from_ini("config/does-not-exist.ini")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[naming]\nfolder = {author}/{nope}\n", "nope"),
        ("[naming]\nfilename = {author}/{title}\n", "must not contain"),
        ("[encoding]\nformat = mp3\n", "mp3"),
        ("[encoding]\nbitrate = 0\n", "bitrate"),
        ("[encoding]\nbitrate = 257\n", "bitrate"),
        ("[sync]\nmax-download = 0\n", "max-download must be 1 or more"),
        ("[sync]\nmax-download = -1\n", "max-download must be 1 or more"),
        ("[sync]\nmax-attempts = 0\n", "max-attempts must be 1 or more"),
        ("[sync]\nmax-attempts = -1\n", "max-attempts must be 1 or more"),
    ],
)
def test_from_ini_validates_up_front(tmp_path, body, message):
    """A bad setting must fail before anything is downloaded or created on disk."""
    with pytest.raises(ValueError, match=message):
        Settings.from_ini(write_config(tmp_path, body))


def test_settings_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Settings().encoding_format = "oga"


def test_replace_revalidates():
    """The test factory builds settings this way, so a bad override must still raise."""
    with pytest.raises(ValueError, match="max-download must be 1 or more"):
        dataclasses.replace(Settings(), max_download=0)


def test_create_folders_creates_both_with_parents(tmp_path):
    settings = dataclasses.replace(
        Settings(), download_folder=tmp_path / "a" / "downloads", audiobook_folder=tmp_path / "b" / "books"
    )

    settings.create_folders()
    settings.create_folders()  # idempotent

    assert settings.download_folder.is_dir()
    assert settings.audiobook_folder.is_dir()


@pytest.mark.parametrize("value", [None, 1, 10, 1000])
def test_validate_max_download_accepts_unset_and_positive_limits(value):
    validate_max_download(value)


@pytest.mark.parametrize("value", [0, -1, -100])
def test_validate_max_download_rejects_zero_and_negative_limits(value):
    """A negative limit used to slice the newest book off the queue on every run."""
    with pytest.raises(ValueError, match="max-download must be 1 or more"):
        validate_max_download(value)


@pytest.mark.parametrize("value", [1, 3, 10])
def test_validate_max_attempts_accepts_positive_caps(value):
    validate_max_attempts(value)


@pytest.mark.parametrize("value", [0, -1])
def test_validate_max_attempts_rejects_zero_and_negative_caps(value):
    """Zero would fail every book on its first claim, before it had been tried once."""
    with pytest.raises(ValueError, match="max-attempts must be 1 or more"):
        validate_max_attempts(value)
