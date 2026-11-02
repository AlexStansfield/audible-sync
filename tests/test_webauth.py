from datetime import UTC, datetime, timedelta

import pytest

import src.database as database
from src import crypto, webauth

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
PASSWORD = "correct horse battery"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()


@pytest.fixture
def user(db):
    webauth.ensure_admin()
    webauth.set_password(PASSWORD)


# --- passwords -------------------------------------------------------------------


def test_a_password_checks_against_its_own_hash_only():
    encoded = webauth.hash_password(PASSWORD)

    assert encoded.startswith(f"scrypt${webauth.SCRYPT_N}$")
    assert webauth.check_password(PASSWORD, encoded)
    assert not webauth.check_password(PASSWORD + "!", encoded)


def test_the_same_password_hashes_differently_each_time():
    assert webauth.hash_password(PASSWORD) != webauth.hash_password(PASSWORD)


def test_a_hash_keeps_the_parameters_it_was_made_with(monkeypatch):
    encoded = webauth.hash_password(PASSWORD)
    monkeypatch.setattr(webauth, "SCRYPT_N", 2**10)

    assert webauth.check_password(PASSWORD, encoded)


@pytest.mark.parametrize(
    "encoded", ["", "garbage", "bcrypt$1$2$3$aa$bb", "scrypt$x$8$1$aa$bb", "scrypt$16384$8$1$zz$bb"]
)
def test_a_hash_that_cannot_be_read_never_matches(encoded):
    assert not webauth.check_password(PASSWORD, encoded)


@pytest.mark.parametrize("password", ["", "short", "x" * 11, "x" * 1025])
def test_a_password_outside_the_limits_is_refused(password):
    with pytest.raises(ValueError, match="password must be"):
        webauth.validate_password(password)


def test_twelve_characters_is_enough():
    webauth.validate_password("x" * 12)


# --- the user --------------------------------------------------------------------


def test_the_first_start_creates_admin_with_a_generated_password(db):
    password = webauth.ensure_admin()

    assert password is not None and len(password) >= webauth.MIN_PASSWORD_LENGTH
    assert webauth.current_user() == webauth.User("admin", generated_password=True)
    assert webauth.verify("admin", password)


def test_a_later_start_creates_nothing(db):
    password = webauth.ensure_admin()

    assert webauth.ensure_admin() is None
    assert webauth.verify("admin", password)


def test_the_password_hash_is_stored_encrypted(db):
    webauth.ensure_admin()

    stored = database.get_settings()[database.WEB_PASSWORD_SETTING]
    assert crypto.is_encrypted(stored)
    assert crypto.decrypt(stored).startswith("scrypt$")


def test_the_user_never_shows_as_a_setting(db):
    from src.settings import Settings

    webauth.ensure_admin()

    assert not {"web_username", "web_password", "web_password_generated"} & set(Settings.from_db().to_db_values())


def test_a_hash_under_another_key_is_replaced_keeping_the_username(db, caplog):
    webauth.ensure_admin()
    webauth.set_username("alex")
    crypto.configure(crypto.generate_key())

    password = webauth.ensure_admin()

    assert password is not None
    assert "cannot be read" in caplog.text
    assert webauth.verify("alex", password)


def test_a_password_regenerated_after_a_key_change_ends_every_session(db):
    webauth.ensure_admin()
    token = webauth.create_session(persistent=True)
    crypto.configure(crypto.generate_key())

    webauth.ensure_admin()

    assert not webauth.check_session(token)


def test_the_password_is_readable_under_the_key_that_wrote_it_only(db):
    assert webauth.password_readable()  # nothing stored yet
    webauth.ensure_admin()
    assert webauth.password_readable()

    crypto.configure(crypto.generate_key())

    assert not webauth.password_readable()


def test_no_user_before_the_first_start(db):
    assert webauth.current_user() is None
    assert not webauth.verify("admin", PASSWORD)
    assert not webauth.verify("", "")


@pytest.mark.parametrize(
    ("username", "password"),
    [("admin", "wrong password!"), ("Admin", PASSWORD), ("someone", PASSWORD), ("", PASSWORD)],
)
def test_verify_refuses_either_mistake(user, username, password):
    assert not webauth.verify(username, password)


def test_verify_accepts_the_right_pair(user):
    assert webauth.verify("admin", PASSWORD)


def test_a_wrong_username_still_runs_the_hash(user, monkeypatch):
    checked = []
    real = webauth.check_password
    monkeypatch.setattr(webauth, "check_password", lambda p, e: checked.append(e) or real(p, e))

    webauth.verify("nobody", PASSWORD)

    assert len(checked) == 1


def test_setting_a_password_clears_the_generated_flag(db):
    webauth.ensure_admin()

    webauth.set_password(PASSWORD)

    assert webauth.current_user().generated_password is False
    assert webauth.check_current_password(PASSWORD)


def test_a_short_new_password_is_refused_and_the_old_one_kept(user):
    with pytest.raises(ValueError):
        webauth.set_password("short")

    assert webauth.verify("admin", PASSWORD)


def test_a_username_is_trimmed(user):
    assert webauth.set_username("  alex  ") == "alex"

    assert webauth.verify("alex", PASSWORD)
    assert not webauth.verify("admin", PASSWORD)


@pytest.mark.parametrize("username", ["", "   ", "x" * 65])
def test_a_bad_username_is_refused(user, username):
    with pytest.raises(ValueError, match="username"):
        webauth.set_username(username)


def test_reset_gives_a_new_generated_password_and_ends_every_session(user):
    webauth.set_username("alex")
    token = webauth.create_session(persistent=True)

    password = webauth.reset_password()

    assert webauth.verify("alex", password)
    assert not webauth.verify("alex", PASSWORD)
    assert webauth.current_user().generated_password is True
    assert not webauth.check_session(token)


def test_reset_can_put_the_username_back(user):
    webauth.set_username("alex")

    password = webauth.reset_password(reset_username=True)

    assert webauth.verify("admin", password)


def test_reset_before_the_first_start_creates_admin(db):
    password = webauth.reset_password()

    assert webauth.verify("admin", password)


# --- sessions --------------------------------------------------------------------


def test_a_session_is_live_until_it_is_ended(db):
    token = webauth.create_session(persistent=False, now=NOW)

    assert webauth.check_session(token, now=NOW)
    webauth.end_session(token)
    assert not webauth.check_session(token, now=NOW)


def test_only_the_hash_of_the_token_is_stored(db):
    token = webauth.create_session(persistent=False, now=NOW)

    assert database.get_session(token) is None
    assert database.get_session(webauth._session_id(token)) is not None


def test_an_unknown_token_is_refused(db):
    assert not webauth.check_session("made up", now=NOW)


def test_a_browser_session_ends_after_twelve_hours_unused(db):
    token = webauth.create_session(persistent=False, now=NOW)

    assert not webauth.check_session(token, now=NOW + webauth.BROWSER_SESSION)
    # ... and is gone, not just refused
    assert database.get_session(webauth._session_id(token)) is None


def test_using_a_browser_session_keeps_it_alive(db):
    token = webauth.create_session(persistent=False, now=NOW)
    later = NOW + timedelta(hours=11)

    assert webauth.check_session(token, now=later)
    assert webauth.check_session(token, now=later + timedelta(hours=11))


def test_a_use_is_recorded_at_most_once_a_minute(db, monkeypatch):
    token = webauth.create_session(persistent=False, now=NOW)
    touched = []
    monkeypatch.setattr(webauth, "touch_session", lambda *a, **k: touched.append(k))

    webauth.check_session(token, now=NOW + timedelta(seconds=30))
    webauth.check_session(token, now=NOW + timedelta(seconds=61))

    assert len(touched) == 1


def test_a_persistent_session_lasts_thirty_days_from_the_sign_in_however_used(db):
    token = webauth.create_session(persistent=True, now=NOW)

    assert webauth.check_session(token, now=NOW + timedelta(days=29))
    assert not webauth.check_session(token, now=NOW + timedelta(days=30))


def test_ending_the_other_sessions_keeps_this_one(db):
    mine = webauth.create_session(persistent=False, now=NOW)
    other = webauth.create_session(persistent=True, now=NOW)

    assert webauth.end_other_sessions(mine) == 1

    assert webauth.check_session(mine, now=NOW)
    assert not webauth.check_session(other, now=NOW)


def test_ending_the_other_sessions_without_one_ends_them_all(db):
    token = webauth.create_session(persistent=False, now=NOW)

    webauth.end_other_sessions(None)

    assert not webauth.check_session(token, now=NOW)


def test_a_new_session_clears_out_expired_ones(db):
    old = webauth.create_session(persistent=False, now=NOW)

    webauth.create_session(persistent=False, now=NOW + timedelta(days=1))

    assert database.get_session(webauth._session_id(old)) is None


# --- the throttle ----------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_throttle_counts_an_attempt_before_its_outcome_is_known():
    """Counting after the check let a parallel burst all pass it during the hashing."""
    throttle = webauth.LoginThrottle(clock=Clock())

    allowed = [throttle.attempt("1.2.3.4") for _ in range(5)]

    assert allowed == [None] * 5
    assert throttle.attempt("1.2.3.4") == 15 * 60
    # Another client is not affected
    assert throttle.attempt("5.6.7.8") is None


def test_a_refused_attempt_is_not_counted():
    clock = Clock()
    throttle = webauth.LoginThrottle(clock=clock)
    for _ in range(5):
        throttle.attempt("ip")

    for _ in range(10):
        throttle.attempt("ip")
    clock.now += 15 * 60

    assert throttle.attempt("ip") is None


def test_the_throttle_lets_the_client_back_once_the_oldest_attempt_ages_out():
    clock = Clock()
    throttle = webauth.LoginThrottle(clock=clock)
    for _ in range(5):
        throttle.attempt("ip")
        clock.now += 60

    assert throttle.attempt("ip") == 15 * 60 - 5 * 60
    clock.now += 10 * 60
    assert throttle.attempt("ip") is None


def test_a_success_takes_the_attempts_back():
    throttle = webauth.LoginThrottle(clock=Clock())
    for _ in range(4):
        throttle.attempt("ip")

    throttle.succeeded("ip")

    assert [throttle.attempt("ip") for _ in range(5)] == [None] * 5


def test_addresses_that_never_come_back_are_forgotten():
    clock = Clock()
    throttle = webauth.LoginThrottle(clock=clock)
    for n in range(100):
        throttle.attempt(f"10.0.0.{n}")

    clock.now += 15 * 60
    throttle.attempt("1.2.3.4")

    assert list(throttle._failures) == ["1.2.3.4"]
