"""Record Cali-originated Google writes so the listener can skip self-triggers."""

from __future__ import annotations

import logging
from typing import Optional

from domains.calendar_listener.repository import CalendarListenerRepository

logger = logging.getLogger(__name__)


def record_self_write(
    user_id: str,
    google_calendar_id: str,
    google_event_id: str,
    etag: Optional[str] = None,
) -> None:
    """Remember a write that originated in Cali so webhook diffs can skip it."""
    if not user_id or not google_calendar_id or not google_event_id:
        return
    try:
        CalendarListenerRepository().insert_suppression(
            user_id, google_calendar_id, google_event_id, etag
        )
    except Exception:
        logger.exception(
            "Failed to record calendar self-write user=%s event=%s",
            user_id,
            google_event_id,
        )
