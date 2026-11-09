import logging
import sqlite3
import subprocess
import sys
from importlib.metadata import entry_points

import pytest

from audible_sync import cli as main_module
from audible_sync.audible_api.accounts import LoginAlreadyHeld
from audible_sync.audible_api.login import LoginFailed
from audible_sync.cli import configure_logging, main
from audible_sync.model import StoredLogin
from tests.conftest import make_account, make_settings


def test_configure_logging_honours_the_debug_flag(monkeypatch):
    """The debug config flag was read but never applied to the log level."""
    levels = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: levels.append(kw["level"]))

    configure_logging(debug=True)
    configure_logging(debug=False)

    assert levels == [logging.DEBUG, logging.INFO]


def test_main_reads_settings_before_configuring_logging(monkeypatch):
    """The database holds the settings, so it comes first; the config file is seeded into
    it once; the settings are validated before logging or any folder exists; then the
    auth file of an installation from before accounts is brought across."""
    order = []
    settings = make_settings(debug=True)

    monkeypatch.setattr(main_module, "init_db", lambda: order.append("init_db"))
    monkeypatch.setattr(main_module, "seed_settings_from_ini", lambda: order.append("seed"))
    monkeypatch.setattr(main_module.Settings, "from_db", classmethod(lambda cls: order.append("settings") or settings))
    monkeypatch.setattr(main_module, "configure_logging", lambda debug: order.append(("logging", debug)))
    monkeypatch.setattr(main_module, "load_secret_key", lambda: order.append("key") or "k")
    monkeypatch.setattr(main_module.crypto, "configure", lambda key: order.append(("configure", key)))
    monkeypatch.setattr(main_module, "encrypt_stored_secrets", lambda: order.append("encrypt"))
    monkeypatch.setattr(main_module, "backfill_customer_ids", lambda: order.append("customers"))
    monkeypatch.setattr(main_module, "count_unreadable_secrets", lambda: order.append("check key") or 0)
    monkeypatch.setattr(
        main_module, "run_pipeline", lambda s, progress=None, full=False: order.append(("pipeline", s, progress))
    )

    main([])

    # The key after logging, so where it came from is reported
    assert order[:9] == [
        "init_db",
        "seed",
        "settings",
        ("logging", True),
        "key",
        ("configure", "k"),
        "encrypt",
        "customers",
        "check key",
    ]
    step, passed_settings, progress = order[9]
    assert (step, passed_settings) == ("pipeline", settings)
    # The bar is a terminal concern the CLI injects, like the logging config above
    assert isinstance(progress, main_module.TqdmProgress)


def _patch_prepare(monkeypatch, order):
    settings = make_settings()
    monkeypatch.setattr(main_module, "init_db", lambda: order.append("init_db"))
    monkeypatch.setattr(main_module, "seed_settings_from_ini", lambda: order.append("seed"))
    monkeypatch.setattr(main_module.Settings, "from_db", classmethod(lambda cls: settings))
    monkeypatch.setattr(main_module, "configure_logging", lambda debug: order.append("logging"))
    monkeypatch.setattr(main_module, "encrypt_stored_secrets", lambda: order.append("encrypt"))
    monkeypatch.setattr(main_module, "backfill_customer_ids", lambda: order.append("customers"))
    monkeypatch.setattr(main_module, "count_unreadable_secrets", lambda: 0)
    return settings


def test_a_command_warns_when_the_stored_secrets_were_written_under_another_key(monkeypatch, caplog):
    """A CLI on the host next to a service in Docker: a login stored now would be lost on the service."""
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "count_unreadable_secrets", lambda: 2)
    monkeypatch.setattr(main_module, "run_pipeline", lambda *a, **kw: None)

    main(["run"])

    assert "2 stored secret(s) were encrypted with another key" in caplog.text
    assert "AUDIBLE_SYNC_SECRET_KEY" in caplog.text


def test_a_command_says_nothing_when_the_key_reads_the_stored_secrets(monkeypatch, caplog):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "run_pipeline", lambda *a, **kw: None)

    main(["run"])

    assert "another key" not in caplog.text


def test_run_is_the_default_command(monkeypatch):
    order = []
    settings = _patch_prepare(monkeypatch, order)
    monkeypatch.setattr(
        main_module, "run_pipeline", lambda s, progress=None, full=False: order.append(("pipeline", s, full))
    )

    main(["run"])
    main([])

    assert order.count(("pipeline", settings, False)) == 2


@pytest.mark.parametrize("argv", [["--full"], ["run", "--full"], ["--full", "run"]])
def test_full_asks_the_run_for_a_full_refresh(monkeypatch, argv):
    order = []
    settings = _patch_prepare(monkeypatch, order)
    monkeypatch.setattr(
        main_module, "run_pipeline", lambda s, progress=None, full=False: order.append(("pipeline", s, full))
    )

    main(argv)

    assert ("pipeline", settings, True) in order


def test_login_command_adds_an_account_from_a_code_login(monkeypatch, capsys):
    order = []
    _patch_prepare(monkeypatch, order)
    monkeypatch.setattr(
        main_module, "login_with_code", lambda marketplace: order.append(("login", marketplace)) or "auth"
    )
    monkeypatch.setattr(
        main_module, "login_interactively", lambda marketplace: pytest.fail("the code flow is the default")
    )
    added = []

    def fake_add(auth, *, name, monitor_existing):
        added.append((auth, name, monitor_existing))
        return StoredLogin(3)

    monkeypatch.setattr(main_module, "store_login", fake_add)
    monkeypatch.setattr(main_module, "run_pipeline", lambda *a, **kw: pytest.fail("login must not sync"))

    main(["login", "--marketplace", "uk", "--name", "Main", "--no-download-existing"])

    # The database and settings come first, like every command, then the login
    assert order[:5] == ["init_db", "seed", "logging", "encrypt", "customers"]
    assert order[5] == ("login", "uk")
    assert added == [("auth", "Main", False)]
    assert "Account 3 added" in capsys.readouterr().out


def test_reset_password_prints_a_new_password_after_the_usual_preparation(monkeypatch, capsys):
    order = []
    _patch_prepare(monkeypatch, order)
    monkeypatch.setattr(main_module.webauth, "password_readable", lambda: True)
    resets = []
    monkeypatch.setattr(
        main_module.webauth,
        "reset_password",
        lambda *, reset_username: order.append("reset") or resets.append(reset_username) or "new-password-123",
    )
    monkeypatch.setattr(main_module.webauth, "current_user", lambda: main_module.webauth.User("alex", True))

    main(["reset-password"])

    assert order[:5] == ["init_db", "seed", "logging", "encrypt", "customers"]
    assert order[-1] == "reset"
    assert resets == [False]
    out = capsys.readouterr().out
    assert "Sign in as alex with the password: new-password-123" in out
    assert "signed out" in out


def test_reset_password_can_put_the_username_back(monkeypatch):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module.webauth, "password_readable", lambda: True)
    resets = []
    monkeypatch.setattr(
        main_module.webauth, "reset_password", lambda *, reset_username: resets.append(reset_username) or "pw"
    )
    monkeypatch.setattr(main_module.webauth, "current_user", lambda: None)

    main(["reset-password", "--username"])

    assert resets == [True]


def test_reset_password_refuses_under_another_key_than_the_service(monkeypatch):
    """Stored under this key, the password would be unreadable to the service: a lockout."""
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module.webauth, "password_readable", lambda: False)
    monkeypatch.setattr(main_module.webauth, "reset_password", lambda **kw: pytest.fail("must not reset"))

    with pytest.raises(SystemExit, match="another key"):
        main(["reset-password"])


def test_login_command_defaults(monkeypatch):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "login_with_code", lambda marketplace: "auth")
    added = []
    monkeypatch.setattr(
        main_module,
        "store_login",
        lambda auth, *, name, monitor_existing: added.append((name, monitor_existing)) or StoredLogin(1),
    )

    main(["login", "--marketplace", "us"])

    assert added == [(None, True)]


def test_login_command_with_browser_uses_the_browser_flow(monkeypatch):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "login_with_code", lambda marketplace: pytest.fail("asked for the browser"))
    monkeypatch.setattr(main_module, "login_interactively", lambda marketplace: f"browser-auth-{marketplace}")
    added = []
    monkeypatch.setattr(main_module, "store_login", lambda auth, **kw: added.append(auth) or StoredLogin(1))

    main(["login", "--marketplace", "us", "--browser"])

    assert added == ["browser-auth-us"]


def test_login_command_says_when_an_account_already_held_the_login(monkeypatch, capsys):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "login_with_code", lambda marketplace: "auth")
    monkeypatch.setattr(main_module, "store_login", lambda auth, **kw: StoredLogin(4, replaced=True))
    monkeypatch.setattr(main_module, "get_account", lambda account_id: make_account(id=account_id, name="Main"))

    main(["login", "--marketplace", "uk"])

    output = capsys.readouterr().out
    assert "Account 4 (Main) already held this login" in output
    assert "added" not in output
    assert "only apply to a new account" not in output
    assert "paused" not in output


def test_login_command_says_what_it_did_not_use_and_that_the_account_is_paused(monkeypatch, capsys):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "login_with_code", lambda marketplace: "auth")
    monkeypatch.setattr(main_module, "store_login", lambda auth, **kw: StoredLogin(4, replaced=True))
    monkeypatch.setattr(
        main_module, "get_account", lambda account_id: make_account(id=account_id, name="Main", enabled=False)
    )

    main(["login", "--marketplace", "uk", "--name", "Partner"])

    output = capsys.readouterr().out
    assert "only apply to a new account" in output
    assert "It is paused, so runs skip it" in output


def test_login_command_reports_a_failed_login(monkeypatch):
    _patch_prepare(monkeypatch, [])

    def expired(marketplace):
        raise LoginFailed("the code expired before it was confirmed; start again")

    monkeypatch.setattr(main_module, "login_with_code", expired)
    monkeypatch.setattr(main_module, "store_login", lambda *a, **kw: pytest.fail("must not add"))

    with pytest.raises(SystemExit, match="Login failed: the code expired"):
        main(["login", "--marketplace", "uk"])


def test_login_command_refuses_an_unknown_marketplace(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        main(["login", "--marketplace", "xx"])

    assert "invalid choice" in capsys.readouterr().err


def test_login_command_can_replace_an_existing_accounts_credentials(monkeypatch, capsys):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "login_with_code", lambda marketplace: "auth")
    monkeypatch.setattr(main_module, "get_account", lambda account_id: make_account(id=account_id, name="Main"))
    replaced = []
    monkeypatch.setattr(
        main_module,
        "replace_account_auth",
        lambda account, auth: replaced.append((account.id, auth)) or StoredLogin(account.id, replaced=True),
    )
    monkeypatch.setattr(main_module, "store_login", lambda *a, **kw: pytest.fail("must not add"))

    main(["login", "--marketplace", "uk", "--account", "3"])

    assert replaced == [(3, "auth")]
    output = capsys.readouterr().out
    assert "Account 3 (Main) has new credentials" in output
    assert "refused" not in output


@pytest.mark.parametrize(
    ("requeued", "said"),
    [(1, "1 refused book is waiting to download again."), (2, "2 refused books are waiting to download again.")],
)
@pytest.mark.parametrize("args", [[], ["--account", "3"]])
def test_login_command_says_how_many_refused_books_it_queued(monkeypatch, capsys, args, requeued, said):
    _patch_prepare(monkeypatch, [])
    stored = StoredLogin(3, replaced=True, requeued=requeued)
    monkeypatch.setattr(main_module, "login_with_code", lambda marketplace: "auth")
    monkeypatch.setattr(main_module, "get_account", lambda account_id: make_account(id=account_id, name="Main"))
    monkeypatch.setattr(main_module, "replace_account_auth", lambda account, auth: stored)
    monkeypatch.setattr(main_module, "store_login", lambda auth, **kw: stored)

    main(["login", "--marketplace", "uk", *args])

    assert said in capsys.readouterr().out


def test_login_command_with_an_account_says_it_is_paused(monkeypatch, capsys):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "login_with_code", lambda marketplace: "auth")
    monkeypatch.setattr(
        main_module, "get_account", lambda account_id: make_account(id=account_id, name="Main", enabled=False)
    )
    monkeypatch.setattr(main_module, "replace_account_auth", lambda account, auth: StoredLogin(3, replaced=True))

    main(["login", "--marketplace", "uk", "--account", "3"])

    assert "It is paused, so runs skip it" in capsys.readouterr().out


def test_login_command_deregisters_the_device_when_the_account_cannot_be_stored(monkeypatch):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "login_with_code", lambda marketplace: "auth")
    discarded = []
    monkeypatch.setattr(main_module, "discard_device", lambda auth: discarded.append(auth) or True)

    def locked(auth, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(main_module, "store_login", locked)

    with pytest.raises(SystemExit, match="could not be stored: database is locked"):
        main(["login", "--marketplace", "uk"])

    assert discarded == ["auth"]


def test_login_command_refuses_a_login_another_account_holds_and_discards_the_device(monkeypatch):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "login_with_code", lambda marketplace: "auth")
    monkeypatch.setattr(main_module, "get_account", lambda account_id: make_account(id=account_id, name="Main"))
    discarded = []
    monkeypatch.setattr(main_module, "discard_device", lambda auth: discarded.append(auth) or True)

    def held(account, auth):
        raise LoginAlreadyHeld("Other already holds this Audible login")

    monkeypatch.setattr(main_module, "replace_account_auth", held)

    with pytest.raises(SystemExit, match="Other already holds this Audible login"):
        main(["login", "--marketplace", "uk", "--account", "1"])

    assert discarded == ["auth"]


def test_login_command_refuses_an_unknown_account_before_amazon_is_asked(monkeypatch):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "get_account", lambda account_id: None)
    monkeypatch.setattr(main_module, "login_with_code", lambda marketplace: pytest.fail("must not start a login"))

    with pytest.raises(SystemExit, match="No account with id 9"):
        main(["login", "--marketplace", "uk", "--account", "9"])


def test_login_command_refuses_another_marketplaces_account_before_amazon_is_asked(monkeypatch):
    _patch_prepare(monkeypatch, [])
    monkeypatch.setattr(main_module, "get_account", lambda account_id: make_account(id=account_id, country_code="us"))
    monkeypatch.setattr(main_module, "login_with_code", lambda marketplace: pytest.fail("must not start a login"))

    with pytest.raises(SystemExit, match="US account"):
        main(["login", "--marketplace", "uk", "--account", "9"])


def test_the_audible_sync_script_is_this_cli():
    """`audible-sync` (pyproject's [project.scripts]) is what the docs tell people to run."""
    (script,) = entry_points(group="console_scripts", name="audible-sync")

    assert script.load() is main


def test_python_dash_m_audible_sync_runs_the_cli():
    result = subprocess.run(
        [sys.executable, "-m", "audible_sync", "--help"], capture_output=True, text=True, check=True, timeout=60
    )

    assert result.stdout.startswith("usage: audible-sync")
    assert "reset-password" in result.stdout
