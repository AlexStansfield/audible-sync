"""
The settings, the webhook test and the API token.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from audible_sync import webauth
from audible_sync.apitoken import ApiToken, TokenFromEnv
from audible_sync.database import (
    save_settings,
)
from audible_sync.notify import send_webhook
from audible_sync.scheduler import Scheduler
from audible_sync.schemas import (
    ApiTokenInfo,
    Message,
    NewApiToken,
    SettingsOut,
    SettingsUpdate,
    WebhookTest,
)
from audible_sync.settings import Settings
from audible_sync.webauth import SESSION_COOKIE


def router(*, scheduler: Scheduler, api_token: ApiToken) -> APIRouter:
    router = APIRouter()

    @router.get("/settings", response_model=SettingsOut)
    def get_settings() -> SettingsOut:
        return SettingsOut.model_validate(Settings.from_db())

    @router.put("/settings", response_model=SettingsOut)
    def update_settings(update: SettingsUpdate) -> SettingsOut:
        changes = update.model_dump(exclude_unset=True)
        # `with_changes` validates; `create_app` makes a ValueError a 422
        settings = Settings.from_db().with_changes(**changes)
        save_settings(settings.to_db_values())
        if "debug" in changes:
            logging.getLogger().setLevel(logging.DEBUG if settings.debug else logging.INFO)
        # A changed interval or a toggled schedule applies to the wait already in progress
        scheduler.wake()
        return SettingsOut.model_validate(settings)

    @router.post("/settings/webhook/test", response_model=Message)
    def test_webhook(body: WebhookTest) -> Message:
        """Send a sample event to `url`, or the configured webhook; 502 if it fails."""
        url = body.url or Settings.from_db().webhook_url
        if not url:
            raise HTTPException(status_code=400, detail="No webhook URL configured")
        try:
            send_webhook(url, {"event": "test", "message": "Audible Sync webhook test"})
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"Webhook failed: {error}") from None
        return Message(status="sent")

    @router.get("/settings/api-token", response_model=ApiTokenInfo)
    def get_api_token() -> ApiTokenInfo:
        """The token masked to its last four characters, and whether it is the env's or stored."""
        return ApiTokenInfo(last4=api_token.last4, source=api_token.source)

    @router.post("/settings/api-token/regenerate", response_model=NewApiToken)
    def regenerate_api_token(request: Request) -> NewApiToken:
        """
        Replace the token; the old one stops working at once. The new one is returned
        this once and never logged. 409 when `AUDIBLE_SYNC_API_TOKEN` sets it.

        Only from a signed-in web app (403 under the bearer token alone): a leaked token
        must not be able to lock its owner's scripts out by rotating itself.
        """
        session = request.cookies.get(SESSION_COOKIE)
        if not (session and webauth.check_session(session)):
            raise HTTPException(status_code=403, detail="Regenerate the API token from the signed-in web app")
        try:
            return NewApiToken(token=api_token.regenerate())
        except TokenFromEnv as error:
            raise HTTPException(status_code=409, detail=str(error)) from None

    return router
