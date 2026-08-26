"""Google Calendar push-notification webhook."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Header, Response, status

from domains.calendar_listener.service import CalendarListenerService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


@router.post("/google-calendar", include_in_schema=False)
async def google_calendar_webhook(
    background_tasks: BackgroundTasks,
    x_goog_channel_id: str | None = Header(default=None, alias="X-Goog-Channel-ID"),
    x_goog_channel_token: str | None = Header(default=None, alias="X-Goog-Channel-Token"),
    x_goog_resource_state: str | None = Header(default=None, alias="X-Goog-Resource-State"),
    x_goog_resource_id: str | None = Header(default=None, alias="X-Goog-Resource-ID"),
) -> Response:
    """Ack Google Calendar watch pings immediately, then sync in the background."""
    if not x_goog_channel_id:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)
    background_tasks.add_task(
        _process_google_webhook,
        x_goog_channel_id,
        x_goog_channel_token or "",
        x_goog_resource_state or "",
        x_goog_resource_id,
    )
    return Response(status_code=status.HTTP_200_OK)


async def _process_google_webhook(
    channel_id: str,
    channel_token: str,
    resource_state: str,
    resource_id: str | None,
) -> None:
    try:
        await CalendarListenerService().handle_google_webhook(
            channel_id=channel_id,
            channel_token=channel_token,
            resource_state=resource_state,
            resource_id=resource_id,
        )
    except Exception:
        logger.exception("Google calendar webhook processing failed channel=%s", channel_id)
