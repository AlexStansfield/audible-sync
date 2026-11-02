import logging

import pytest

import src.database as database
from src import apitoken, crypto
from src.apitoken import resolve_api_token
from src.settings import Settings


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()


def test_configured_token_wins(db):
    assert resolve_api_token("from-env") == apitoken.ApiToken("from-env", "env")
    assert database.get_settings() == {}


def test_a_generated_token_is_kept_for_the_next_start(db):
    """The token copied from the log must keep working across restarts."""
    first = resolve_api_token(None).value

    assert len(first) >= 32
    assert resolve_api_token(None).value == first
    # Encrypted at rest, like the credentials: a copy of the database is not API access
    (stored,) = database.get_settings().values()
    assert stored.startswith("enc:") and first not in stored


def test_the_token_is_logged_in_full_only_on_the_start_that_generates_it(db, caplog):
    """`docker logs` attached to a bug report must not hand over the API on every start."""
    caplog.set_level(logging.INFO, logger="src.apitoken")
    token = resolve_api_token(None).value
    assert token in caplog.text
    assert "not logged again" in caplog.text
    caplog.clear()

    assert resolve_api_token(None).value == token

    assert token not in caplog.text
    assert f"the stored one, ending ...{token[-4:]}" in caplog.text


def test_a_configured_token_is_never_logged(db, caplog):
    caplog.set_level(logging.INFO, logger="src.apitoken")

    resolve_api_token("chosen-by-the-user")

    assert "chosen-by-the-user" not in caplog.text
    assert "from AUDIBLE_SYNC_API_TOKEN" in caplog.text


def test_a_token_stored_in_plain_text_by_an_older_version_still_works(db):
    database.save_settings({"api_token": "old-token"})

    assert resolve_api_token(None) == apitoken.ApiToken("old-token", "stored")


def test_a_token_under_another_key_is_replaced(db, caplog):
    first = resolve_api_token(None).value
    crypto.configure(crypto.generate_key())

    second = resolve_api_token(None).value

    assert second != first
    assert "cannot be read" in caplog.text
    assert resolve_api_token(None).value == second


def test_the_stored_token_never_shows_as_a_setting(db):
    resolve_api_token(None)

    assert Settings.from_db() == Settings()


def test_a_stored_token_is_regenerated_in_place_and_stored(db, caplog):
    token = resolve_api_token(None)
    old = token.value

    new = token.regenerate()

    assert new != old and token.value == new
    assert resolve_api_token(None).value == new
    assert "regenerated" in caplog.text
    assert new not in caplog.text


def test_a_token_from_the_environment_cannot_be_regenerated(db):
    token = resolve_api_token("from-env")

    with pytest.raises(apitoken.TokenFromEnv, match="AUDIBLE_SYNC_API_TOKEN"):
        token.regenerate()

    assert token.value == "from-env"
    assert database.get_settings() == {}


def test_the_token_never_shows_in_its_repr():
    token = apitoken.ApiToken("secret-value-abcd", "stored")

    assert "secret-value" not in repr(token)
    assert token.last4 == "abcd"


@pytest.mark.parametrize("value", ["abcd", "x" * 11])
def test_a_short_token_shows_none_of_itself(value):
    assert apitoken.ApiToken(value, "env").last4 == ""
