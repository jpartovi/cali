"""Cron endpoints for the calendar listener."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException, status

from core.config import get_settings
from domains.calendar_listener.service import CalendarListenerService

router = APIRouter(prefix="/calendar-listener", tags=["calendar-listener"])
logger = logging.getLogger(__name__)


def require_cron_secret(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> None:
    settings = get_settings()
    if not settings.cron_secret or x_cron_secret != settings.cron_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid cron secret")


@router.post("/tick")
async def tick_calendar_listener(
    _: None = Depends(require_cron_secret),
) -> Dict[str, Any]:
    """Renew watches, incremental-sync stale calendars, and run due jobs."""
    try:
        stats = await CalendarListenerService().tick()
    except Exception:
        logger.exception("Calendar listener tick failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Calendar listener tick failed",
        )
    return {"status": "ok", **stats}
