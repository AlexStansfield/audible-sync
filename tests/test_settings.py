import dataclasses
from pathlib import Path

import pytest

import src.database as database
from src.paths import REPO_ROOT
from src.settings import (
    DB_SETTING_KEYS,
    Settings,
    seed_settings_from_ini,
    validate_max_attempts,
    validate_max_download,
    validate_sync_interval,
    validate_webhook_url,
)
from tests.conftest import make_settings


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the database module at a fresh temporary file and create the schema."""
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()


def write_config(tmp_path, body: str) -> Path:
    path = tmp_path / "config.ini"
    path.write_text(body)
    return path


FULL_CONFIG = """
[general]
debug = true

[sync]
enabled = false
interval-minutes = 90
max-download = 5
max-attempts = 4
auto-monitor-new = false
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

[notifications]
webhook-url = https://hooks.example/audible
"""


def test_from_ini_reads_every_setting(tmp_path):
    settings = Settings.from_ini(write_config(tmp_path, FULL_CONFIG))

    assert settings.debug is True
    assert settings.sync_enabled is False
    assert settings.sync_interval_minutes == 90
    assert settings.max_download == 5
    assert settings.max_attempts == 4
    assert settings.auto_monitor_new is False
    assert settings.auth_file == Path("/keys/audible.json")
    assert settings.download_folder == Path("/var/tmp/downloads")
    assert settings.audiobook_folder == Path("/mnt/media/audiobooks")
    assert settings.folder_template == "{author}/{title}"
    assert settings.filename_template == "{title} ({year})"
    assert settings.encoding_format == "oga"
    assert settings.bitrate == 48
    assert settings.webhook_url == "https://hooks.example/audible"


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
        ("[sync]\ninterval-minutes = 4\n", "interval-minutes must be 5 or more"),
        ("[notifications]\nwebhook-url = ftp://hooks\n", "webhook-url must start with http"),
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


@pytest.mark.parametrize("body", ["[notifications]\n", "[notifications]\nwebhook-url =\n"])
def test_webhook_url_empty_means_unset(tmp_path, body):
    assert Settings.from_ini(write_config(tmp_path, body)).webhook_url is None


@pytest.mark.parametrize("value", [5, 60, 1440])
def test_validate_sync_interval_accepts_five_minutes_and_up(value):
    validate_sync_interval(value)


@pytest.mark.parametrize("value", [0, 4, -1])
def test_validate_sync_interval_rejects_intervals_too_short_to_finish_a_run(value):
    with pytest.raises(ValueError, match="interval-minutes must be 5 or more"):
        validate_sync_interval(value)


@pytest.mark.parametrize("value", [None, "http://hooks.local/x", "https://hooks.example/audible"])
def test_validate_webhook_url_accepts_unset_and_http_targets(value):
    validate_webhook_url(value)


@pytest.mark.parametrize("value", ["hooks.example", "ftp://hooks.example", ""])
def test_validate_webhook_url_rejects_anything_that_is_not_http(value):
    with pytest.raises(ValueError, match="webhook-url must start with http"):
        validate_webhook_url(value)


# --- the settings table ---------------------------------------------------------

# Every runtime setting moved off its default, so a round trip that drops one shows
_CHANGED = {
    "debug": True,
    "sync_enabled": False,
    "sync_interval_minutes": 45,
    "max_download": 7,
    "max_attempts": 5,
    "auto_monitor_new": False,
    "download_folder": Path("/var/tmp/dl"),
    "audiobook_folder": Path("/mnt/books"),
    "folder_template": "{author}/{title}",
    "filename_template": "{title} ({year})",
    "encoding_format": "oga",
    "bitrate": 40,
    "webhook_url": "https://hooks.example/audible",
}


def test_db_setting_keys_are_every_field_but_the_auth_file():
    """The auth file is where a login is imported from, not something a run reads."""
    expected = {f.name for f in dataclasses.fields(Settings)} - {"auth_file"}

    assert set(DB_SETTING_KEYS) == expected
    assert set(_CHANGED) == expected


def test_to_db_values_writes_text_the_ini_file_would_accept():
    values = make_settings(**_CHANGED).to_db_values()

    assert values["debug"] == "true"
    assert values["sync_enabled"] == "false"
    assert values["sync_interval_minutes"] == "45"
    assert values["download_folder"] == "/var/tmp/dl"
    assert values["webhook_url"] == "https://hooks.example/audible"
    assert "auth_file" not in values


def test_to_db_values_writes_unset_as_an_empty_string():
    values = Settings().to_db_values()

    assert values["max_download"] == ""
    assert values["webhook_url"] == ""


def test_from_db_round_trips_every_runtime_setting(db):
    settings = make_settings(**_CHANGED)

    database.save_settings(settings.to_db_values())

    assert Settings.from_db() == settings


def test_from_db_on_an_empty_table_is_the_defaults(db):
    assert Settings.from_db() == Settings()


def test_from_db_reads_an_empty_value_as_unset(db):
    database.save_settings({"max_download": "", "webhook_url": ""})

    settings = Settings.from_db()

    assert settings.max_download is None
    assert settings.webhook_url is None


def test_from_db_accepts_the_ini_spellings_of_a_flag(db):
    database.save_settings({"debug": "yes", "sync_enabled": "0"})

    settings = Settings.from_db()

    assert settings.debug is True
    assert settings.sync_enabled is False


def test_from_db_anchors_a_relative_folder_to_the_repo_root(db):
    database.save_settings({"audiobook_folder": "audiobooks"})

    assert Settings.from_db().audiobook_folder == REPO_ROOT / "audiobooks"


def test_from_db_ignores_a_key_this_build_does_not_know(db):
    """A database written by a newer version must still read."""
    database.save_settings({"future_setting": "x", "bitrate": "32"})

    assert Settings.from_db().bitrate == 32


@pytest.mark.parametrize(
    ("key", "text", "message"),
    [
        ("bitrate", "lots", "bitrate must be a whole number"),
        ("max_download", "ten", "max_download must be a whole number"),
        ("debug", "maybe", "debug must be true or false"),
    ],
)
def test_from_db_names_the_setting_it_cannot_parse(db, key, text, message):
    database.save_settings({key: text})

    with pytest.raises(ValueError, match=message):
        Settings.from_db()


def test_from_db_validates_like_every_other_route(db):
    database.save_settings({"bitrate": "0"})

    with pytest.raises(ValueError, match="bitrate"):
        Settings.from_db()


def test_with_changes_revalidates():
    with pytest.raises(ValueError, match="max-download must be 1 or more"):
        Settings().with_changes(max_download=0)


def test_with_changes_replaces_only_what_it_is_given():
    settings = Settings().with_changes(bitrate=32, encoding_format="oga")

    assert (settings.bitrate, settings.encoding_format) == (32, "oga")
    assert settings.folder_template == Settings().folder_template


@pytest.mark.parametrize("key", ["auth_file", "nope"])
def test_with_changes_refuses_a_key_that_is_not_a_runtime_setting(key):
    """A typo must not pass silently, and the auth file is import-only."""
    with pytest.raises(ValueError, match=f"not a runtime setting: {key}"):
        Settings().with_changes(**{key: "x"})


def test_with_changes_anchors_a_folder_given_as_text():
    settings = Settings().with_changes(download_folder="data/dl", audiobook_folder="/mnt/books")

    assert settings.download_folder == REPO_ROOT / "data" / "dl"
    assert settings.audiobook_folder == Path("/mnt/books")


def test_with_changes_reads_an_empty_webhook_url_as_unset():
    settings = Settings().with_changes(webhook_url="https://x").with_changes(webhook_url="")

    assert settings.webhook_url is None


def test_seed_settings_from_ini_copies_the_file_into_an_empty_table(db, tmp_path):
    path = write_config(tmp_path, FULL_CONFIG)

    assert seed_settings_from_ini(path) is True
    # The auth file is import-only and never stored, so compare what the table holds
    assert Settings.from_db().to_db_values() == Settings.from_ini(path).to_db_values()


def test_seed_settings_from_ini_seeds_only_once(db, tmp_path):
    """After the first run the table is what the user has changed; the file must not
    overwrite it on every start."""
    path = write_config(tmp_path, FULL_CONFIG)
    seed_settings_from_ini(path)
    database.save_settings({"bitrate": "24"})

    assert seed_settings_from_ini(path) is False
    assert Settings.from_db().bitrate == 24


def test_seed_settings_from_ini_leaves_a_saved_setting_alone(db, tmp_path):
    database.save_settings({"bitrate": "24"})

    assert seed_settings_from_ini(write_config(tmp_path, FULL_CONFIG)) is False
    assert database.get_settings() == {"bitrate": "24"}


def test_seed_settings_from_ini_skips_a_missing_file(db, tmp_path):
    """The service needs no file at all; the defaults apply."""
    assert seed_settings_from_ini(tmp_path / "nope.ini") is False
    assert database.has_settings() is False
