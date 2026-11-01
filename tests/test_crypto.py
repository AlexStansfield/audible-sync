import stat

import pytest

from src import crypto
from src.crypto import (
    KEY_ENV,
    KEY_FILE_ENV,
    SecretKeyError,
    WrongKey,
    configure,
    decrypt,
    encrypt,
    generate_key,
    is_encrypted,
    load_secret_key,
)


def test_a_secret_round_trips_and_is_stored_with_the_prefix():
    stored = encrypt('{"access_token": "Atna|x"}')

    assert stored.startswith("enc:")
    assert is_encrypted(stored)
    assert "Atna" not in stored
    assert decrypt(stored) == '{"access_token": "Atna|x"}'


def test_the_same_secret_encrypts_differently_each_time():
    assert encrypt("x") != encrypt("x")


def test_a_value_from_before_there_was_a_key_is_returned_as_it_is():
    """Plain text written by an older version reads until the migration rewrites it."""
    assert not is_encrypted('{"a": 1}')
    assert decrypt('{"a": 1}') == '{"a": 1}'


def test_the_wrong_key_is_reported_not_misread():
    stored = encrypt("secret")
    configure(generate_key())

    with pytest.raises(WrongKey, match=KEY_ENV):
        decrypt(stored)


def test_nothing_works_without_a_configured_key():
    configure(None)

    assert crypto.is_configured() is False
    with pytest.raises(SecretKeyError, match="No secret key"):
        encrypt("x")
    with pytest.raises(SecretKeyError):
        decrypt("enc:whatever")
    # ...except reading a plain text value, which needs no key
    assert decrypt("plain") == "plain"


def test_configure_refuses_a_malformed_key():
    with pytest.raises(SecretKeyError, match="not a valid key"):
        configure("not-a-key")


def test_the_environment_variable_wins(tmp_path):
    key = generate_key()
    (tmp_path / "secret.key").write_bytes(generate_key())

    loaded = load_secret_key({KEY_ENV: key.decode(), KEY_FILE_ENV: str(tmp_path / "secret.key")})

    assert loaded == key


def test_a_malformed_environment_key_is_an_error():
    with pytest.raises(SecretKeyError, match=KEY_ENV):
        load_secret_key({KEY_ENV: "short"})


def test_the_key_file_is_read_when_there_is_no_variable(tmp_path):
    key = generate_key()
    path = tmp_path / "k" / "secret.key"
    path.parent.mkdir()
    path.write_bytes(key + b"\n")

    assert load_secret_key({KEY_FILE_ENV: str(path)}) == key
    assert load_secret_key({KEY_ENV: "", KEY_FILE_ENV: str(path)}) == key


def test_a_missing_key_file_is_generated_owner_only(tmp_path, caplog):
    """A first start: the key is made, kept for the next start, and the log says where."""
    path = tmp_path / "secrets" / "secret.key"

    key = load_secret_key({KEY_FILE_ENV: str(path)})

    assert path.read_bytes().strip() == key
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert "Generated a new secret key" in caplog.text
    assert load_secret_key({KEY_FILE_ENV: str(path)}) == key


def test_the_default_key_file_is_used_with_nothing_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto, "DEFAULT_KEY_FILE", tmp_path / "secret.key")

    key = load_secret_key({})

    assert (tmp_path / "secret.key").read_bytes().strip() == key


def test_a_key_file_that_is_a_directory_is_an_error(tmp_path):
    with pytest.raises(SecretKeyError, match="not a file"):
        load_secret_key({KEY_FILE_ENV: str(tmp_path)})


def test_a_corrupt_key_file_is_an_error(tmp_path):
    path = tmp_path / "secret.key"
    path.write_text("garbage")

    with pytest.raises(SecretKeyError, match=str(path)):
        load_secret_key({KEY_FILE_ENV: str(path)})
