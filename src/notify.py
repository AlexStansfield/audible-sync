"""
Telling something else that a run finished: one JSON POST to a configured URL.

Enough to make Audiobookshelf rescan or to ping a notifier. A webhook that fails must
never fail the run - it is logged and forgotten - so `notify_run_finished` swallows
everything; `send_webhook` underneath raises, which is what the API's test endpoint
wants so it can report the problem.
"""

import logging
from dataclasses import asdict
from typing import Any

import httpx

from src.model import Account, SyncRun

logger = logging.getLogger(__name__)

# A webhook is a courtesy; a slow receiver must not hold the pipeline
WEBHOOK_TIMEOUT = 10.0


def run_finished_payload(run: SyncRun, account: Account) -> dict[str, Any]:
    return {
        "event": "sync.finished",
        "run": asdict(run),
        "account": {"id": account.id, "name": account.name, "country_code": account.country_code},
    }


def send_webhook(url: str, payload: dict[str, Any], *, client: httpx.Client | None = None) -> None:
    """
    POST the payload as JSON.

    Raises:
        httpx.HTTPError: on a connection problem or a non-2xx answer
    """
    with client or httpx.Client(timeout=WEBHOOK_TIMEOUT) as http:
        response = http.post(url, json=payload)
        response.raise_for_status()


def notify_run_finished(url: str | None, run: SyncRun, account: Account) -> bool:
    """Send the run-finished event if a URL is configured. Never raises."""
    if not url:
        return False
    try:
        send_webhook(url, run_finished_payload(run, account))
    except Exception:
        logger.warning("Webhook to %s failed for run %d", url, run.id, exc_info=True)
        return False
    logger.info("Webhook sent for run %d", run.id)
    return True
