"""
The background service: `python -m src.service`.

Reads the host-level configuration from the environment (things that are about the
process rather than about syncing - where to listen, the API token, CORS), prepares the
database and the settings, wires the scheduler and the run state to the API, and hands
the application to uvicorn. Everything about *what* to sync lives in the settings table
and is changed through the API; nothing here needs a restart to change except the port.

Environment:
    AUDIBLE_SYNC_HOST          Interface to listen on, default 0.0.0.0
    AUDIBLE_SYNC_PORT          Port, default 8080
    AUDIBLE_SYNC_API_TOKEN     Bearer token the API requires. If unset, one is generated
                               on the first start, kept in the settings table, and
                               logged in full that once; later starts log only its end
    AUDIBLE_SYNC_CORS_ORIGINS  Comma-separated origins allowed to call from a browser
    AUDIBLE_SYNC_DEBUG         `true` forces DEBUG logging whatever the settings say
    AUDIBLE_SYNC_WEB_DIR       The built web app to serve; default `web/dist`. Nothing is
                               served outside /api when it holds no index.html
    AUDIBLE_SYNC_SECURE_COOKIE `true` always marks the web app's session cookie Secure;
                               set it behind a reverse proxy that serves https, where
                               the service itself only ever sees http
    AUDIBLE_SYNC_SECRET_KEY    The key the stored credentials and API token are
                               encrypted with (see `src/crypto.py`). If unset, the
                               key file is used, generated on the first start
    AUDIBLE_SYNC_SECRET_KEY_FILE  Where that key file is; default `secrets/secret.key`
"""

import logging
import os
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from src import crypto, webauth
from src.api import create_app
from src.crypto import load_secret_key
from src.database import API_TOKEN_SETTING, encrypt_stored_secrets, get_settings, init_db, save_settings
from src.logbuffer import RingBufferHandler
from src.main import configure_logging, run_pipeline
from src.paths import REPO_ROOT, resolve_path
from src.runstate import RunState, StateProgress
from src.scheduler import Scheduler
from src.settings import Settings, seed_settings_from_ini

# Run as `python -m`, this module is `__main__`; name the logger after the module anyway
logger = logging.getLogger("src.service" if __name__ == "__main__" else __name__)

# Kept in the settings table under a key `Settings.from_db` does not know, so it is
# stored beside the rest but never appears in `GET /api/settings`

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
# How long shutdown waits for the run in flight to hand its book back. A cancel lands
# within a chunk of a download, but the library sync and an ffmpeg pass are not
# interruptible, so this has to cover a slow re-encode. `compose.yml` sets Docker's
# stop grace period to match; a kill after that is what the stale-claim timeout is for.
STOP_TIMEOUT = 90.0
# How many log lines GET /api/logs can look back over
LOG_BUFFER_LINES = 1000
# Where the image puts the web app's build (`npm run build` in web/)
DEFAULT_WEB_DIR = REPO_ROOT / "web" / "dist"


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """The process-level configuration, from the environment."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    api_token: str | None = None
    cors_origins: tuple[str, ...] = ()
    debug: bool = False
    secure_cookie: bool = False
    web_dir: Path = DEFAULT_WEB_DIR

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ServiceConfig":
        env = os.environ if env is None else env
        origins = tuple(o.strip() for o in env.get("AUDIBLE_SYNC_CORS_ORIGINS", "").split(",") if o.strip())
        return cls(
            host=env.get("AUDIBLE_SYNC_HOST", DEFAULT_HOST),
            port=int(env.get("AUDIBLE_SYNC_PORT", DEFAULT_PORT)),
            api_token=env.get("AUDIBLE_SYNC_API_TOKEN") or None,
            cors_origins=origins,
            debug=_flag(env.get("AUDIBLE_SYNC_DEBUG")),
            secure_cookie=_flag(env.get("AUDIBLE_SYNC_SECURE_COOKIE")),
            web_dir=resolve_path(env["AUDIBLE_SYNC_WEB_DIR"]) if env.get("AUDIBLE_SYNC_WEB_DIR") else DEFAULT_WEB_DIR,
        )


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def resolve_api_token(configured: str | None) -> str:
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
        return configured
    stored = get_settings().get(API_TOKEN_SETTING)
    if stored:
        try:
            token = crypto.decrypt(stored)
        except crypto.WrongKey as error:
            logger.error("The stored API token cannot be read, generating a new one: %s", error)
        else:
            logger.info("API token: the stored one, ending ...%s", token[-4:])
            return token
    token = secrets.token_urlsafe(32)
    save_settings({API_TOKEN_SETTING: crypto.encrypt(token)})
    logger.warning(
        "Generated a new API token: %s - copy it now, it is not logged again "
        "(set AUDIBLE_SYNC_API_TOKEN to choose your own)",
        token,
    )
    return token


def ensure_web_user() -> None:
    """
    Create the web app's user on the first start and log its password, that once.

    Like the API token, the password is only ever logged by the start that generates
    it; a forgotten one is replaced with the CLI's `reset-password`.
    """
    password = webauth.ensure_admin()
    if password is None:
        return
    user = webauth.current_user()
    logger.warning(
        "Web app sign-in: user %s, password %s - sign in and choose your own; it is not logged again "
        "(forgotten, it is reset with: python -m src.main reset-password)",
        user.username if user else webauth.DEFAULT_USERNAME,
        password,
    )


def _run(settings: Settings, state: RunState, cancel: threading.Event) -> None:
    """The pipeline as the scheduler calls it, with progress reported into the run state."""
    run_pipeline(settings, progress=StateProgress(state), state=state, cancel=cancel)


def build(config: ServiceConfig):
    """Everything up to the application, so a test can build it without listening."""
    init_db()
    seed_settings_from_ini()
    settings = Settings.from_db()
    configure_logging(config.debug or settings.debug)
    # The secret key before anything that stores or reads credentials: the API token
    # lives encrypted in the settings table
    crypto.configure(load_secret_key())
    encrypt_stored_secrets()

    api_token = resolve_api_token(config.api_token)
    ensure_web_user()
    # Everything logged from here on is also readable through GET /api/logs - and only
    # from here on, so a generated token or password logged above goes to the console
    # alone and cannot be read back, or copied into a bug report, from the Logs screen
    log_buffer = RingBufferHandler(LOG_BUFFER_LINES)
    logging.getLogger().addHandler(log_buffer)

    if (config.web_dir / "index.html").is_file():
        logger.info("Serving the web app from %s", config.web_dir)
    else:
        logger.info("No web app build in %s; serving the API only", config.web_dir)

    state = RunState()
    scheduler = Scheduler(load_settings=Settings.from_db, run=_run, state=state, stop_timeout=STOP_TIMEOUT)
    return create_app(
        scheduler=scheduler,
        state=state,
        api_token=api_token,
        cors_origins=list(config.cors_origins),
        log_buffer=log_buffer,
        secure_cookie=config.secure_cookie,
        web_dir=config.web_dir,
    )


def main() -> None:
    config = ServiceConfig.from_env()
    app = build(config)
    logger.info("Listening on http://%s:%d", config.host, config.port)
    # log_config=None keeps uvicorn on the root logger configured above; the access log
    # is off because a UI polling /api/status would write a line a second
    uvicorn.run(app, host=config.host, port=config.port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
