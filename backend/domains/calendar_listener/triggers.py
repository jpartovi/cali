"""Internal calendar change trigger bus. Consumers register handlers later."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

TriggerHandler = Callable[["CalendarChangeEvent"], Awaitable[None]]


@dataclass(frozen=True)
class CalendarChangeEvent:
    """A classified calendar change ready for later consumers."""

    user_id: str
    google_calendar_id: str
    google_event_id: str
    kind: str
    snapshot: Dict[str, Any]
    previous: Optional[Dict[str, Any]] = None


_handlers: List[TriggerHandler] = []


def register_handler(handler: TriggerHandler) -> None:
    """Register a consumer. Used by later push / Live Activity code."""
    _handlers.append(handler)


async def emit_change(event: CalendarChangeEvent) -> None:
    """Emit a change to all handlers. Logs even when no consumers are registered."""
    logger.info(
        "calendar_change kind=%s user=%s calendar=%s event=%s title=%s",
        event.kind,
        event.user_id,
        event.google_calendar_id,
        event.google_event_id,
        (event.snapshot.get("title") or "")[:80],
    )
    for handler in _handlers:
        try:
            await handler(event)
        except Exception:
            logger.exception(
                "calendar_change handler failed kind=%s event=%s",
                event.kind,
                event.google_event_id,
            )
