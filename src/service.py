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
                               logged at every start so it can be copied from the log
    AUDIBLE_SYNC_CORS_ORIGINS  Comma-separated origins allowed to call from a browser
    AUDIBLE_SYNC_DEBUG         `true` forces DEBUG logging whatever the settings say
"""

import logging
import os
import secrets
import threading
from dataclasses import dataclass

import uvicorn

from src.accounts import ensure_account_from_auth_file
from src.api import create_app
from src.database import get_settings, init_db, save_settings
from src.main import configure_logging, run_pipeline
from src.runstate import RunState, StateProgress
from src.scheduler import Scheduler
from src.settings import Settings, seed_settings_from_ini

logger = logging.getLogger(__name__)

# Kept in the settings table under a key `Settings.from_db` does not know, so it is
# stored beside the rest but never appears in `GET /api/settings`
_API_TOKEN_KEY = "api_token"

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
# How long shutdown waits for the run in flight to hand its book back. A cancel lands
# within a chunk of a download, but the library sync and an ffmpeg pass are not
# interruptible, so this has to cover a slow re-encode. `compose.yml` sets Docker's
# stop grace period to match; a kill after that is what the stale-claim timeout is for.
STOP_TIMEOUT = 90.0


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """The process-level configuration, from the environment."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    api_token: str | None = None
    cors_origins: tuple[str, ...] = ()
    debug: bool = False

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ServiceConfig":
        env = os.environ if env is None else env
        origins = tuple(o.strip() for o in env.get("AUDIBLE_SYNC_CORS_ORIGINS", "").split(",") if o.strip())
        return cls(
            host=env.get("AUDIBLE_SYNC_HOST", DEFAULT_HOST),
            port=int(env.get("AUDIBLE_SYNC_PORT", DEFAULT_PORT)),
            api_token=env.get("AUDIBLE_SYNC_API_TOKEN") or None,
            cors_origins=origins,
            debug=env.get("AUDIBLE_SYNC_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"),
        )


def resolve_api_token(configured: str | None) -> str:
    """
    The token the API will require: the configured one, else the stored one, else a new
    one stored for next time.

    Generated once rather than on every start, so the token copied from the log keeps
    working across restarts.
    """
    if configured:
        return configured
    stored = get_settings().get(_API_TOKEN_KEY)
    if stored:
        return stored
    token = secrets.token_urlsafe(32)
    save_settings({_API_TOKEN_KEY: token})
    logger.info("Generated a new API token and stored it in the settings table")
    return token


def _run(settings: Settings, state: RunState, cancel: threading.Event) -> None:
    """The pipeline as the scheduler calls it, with progress reported into the run state."""
    run_pipeline(settings, progress=StateProgress(state), state=state, cancel=cancel)


def build(config: ServiceConfig):
    """Everything up to the application, so a test can build it without listening."""
    init_db()
    seed_settings_from_ini()
    settings = Settings.from_db()
    configure_logging(config.debug or settings.debug)
    # An installation from before there were accounts has its auth file made into one
    ensure_account_from_auth_file(settings.auth_file)

    api_token = resolve_api_token(config.api_token)
    logger.info("API token: %s", api_token)

    state = RunState()
    scheduler = Scheduler(load_settings=Settings.from_db, run=_run, state=state, stop_timeout=STOP_TIMEOUT)
    return create_app(scheduler=scheduler, state=state, api_token=api_token, cors_origins=list(config.cors_origins))


def main() -> None:
    config = ServiceConfig.from_env()
    app = build(config)
    logger.info("Listening on http://%s:%d", config.host, config.port)
    # log_config=None keeps uvicorn on the root logger configured above; the access log
    # is off because a UI polling /api/status would write a line a second
    uvicorn.run(app, host=config.host, port=config.port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
