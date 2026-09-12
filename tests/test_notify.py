import json
import logging

import httpx
import pytest

from src import notify
from src.model import SyncOutcome, SyncRun
from src.notify import notify_run_finished, run_finished_payload, send_webhook
from tests.conftest import make_account

RUN = SyncRun(
    id=7,
    started_at="2026-09-12T10:00:00+00:00",
    account_id=1,
    finished_at="2026-09-12T10:05:00+00:00",
    outcome=SyncOutcome.SUCCESS,
    books_downloaded=2,
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_payload_carries_the_run_and_the_account():
    payload = run_finished_payload(RUN, make_account(id=1, name="Alex (UK)"))

    assert payload["event"] == "sync.finished"
    assert payload["run"]["id"] == 7
    assert payload["run"]["outcome"] == "success"
    assert payload["run"]["books_downloaded"] == 2
    assert payload["account"] == {"id": 1, "name": "Alex (UK)", "country_code": "uk"}
    # JSON-able, credentials nowhere near it
    assert "auth" not in json.dumps(payload)


def test_send_webhook_posts_json():
    received = {}

    def handler(request):
        received["url"] = str(request.url)
        received["body"] = json.loads(request.content)
        received["type"] = request.headers["content-type"]
        return httpx.Response(204)

    send_webhook("https://hooks.local/x", {"event": "test"}, client=_client(handler))

    assert received == {"url": "https://hooks.local/x", "body": {"event": "test"}, "type": "application/json"}


def test_send_webhook_raises_on_a_bad_answer():
    with pytest.raises(httpx.HTTPStatusError):
        send_webhook("https://hooks.local/x", {}, client=_client(lambda request: httpx.Response(500)))


def test_notify_run_finished_does_nothing_without_a_url(monkeypatch):
    monkeypatch.setattr(notify, "send_webhook", lambda *a, **kw: pytest.fail("must not send"))

    assert notify_run_finished(None, RUN, make_account()) is False
    assert notify_run_finished("", RUN, make_account()) is False


def test_notify_run_finished_sends_and_reports(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    sent = []
    monkeypatch.setattr(notify, "send_webhook", lambda url, payload: sent.append((url, payload["event"])))

    assert notify_run_finished("https://hooks.local/x", RUN, make_account()) is True
    assert sent == [("https://hooks.local/x", "sync.finished")]
    assert "Webhook sent for run 7" in caplog.text


def test_notify_run_finished_never_raises(monkeypatch, caplog):
    """A webhook is a courtesy; the run must not fail because a receiver is down."""

    def down(url, payload):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(notify, "send_webhook", down)

    assert notify_run_finished("https://hooks.local/x", RUN, make_account()) is False
    assert "Webhook to https://hooks.local/x failed" in caplog.text
