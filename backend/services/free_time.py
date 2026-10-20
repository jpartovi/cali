"""Find free blocks of time on the user's calendars.

The agent used to read a whole schedule and work out the gaps itself, which cost a
model call and was easy to get wrong. This does it in code: busy time is every timed
event on a visible calendar (all-day events and declined invitations don't count),
merged where events overlap. Each free block is a gap between busy times, clipped to
the hours of the day the user would plausibly be scheduled (5 AM to midnight unless the
caller says otherwise), and it qualifies when `duration` fits with `buffer` kept clear
of the events on either side.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.timing_logger import log_step

logger = logging.getLogger(__name__)

# Most blocks returned; the agent only needs enough to pick from
MAX_BLOCKS = 20

DEFAULT_EARLIEST = dtime(5, 0)
DEFAULT_LATEST: Optional[dtime] = None  # None = midnight at the end of the day


def parse_time_of_day(text: str) -> Optional[dtime]:
    """"HH:MM" to a time; "24:00" means midnight at the end of the day (None)."""
    if text.strip() in ("24:00", "24:00:00"):
        return None
    return dtime.fromisoformat(text.strip())


def _day_edge(day, at: Optional[dtime], tz: ZoneInfo) -> datetime:
    """`at` on `day`, with None meaning the midnight that ends it."""
    if at is None:
        return datetime.combine(day + timedelta(days=1), dtime(0, 0), tz)
    return datetime.combine(day, at, tz)

@dataclass
class _Busy:
    start: datetime
    end: datetime
    first: Dict[str, Any]  # event that starts this busy stretch
    last: Dict[str, Any]   # event that ends it


def _timed_span(event: Dict[str, Any], tz: ZoneInfo) -> Optional[tuple[datetime, datetime]]:
    start = (event.get("start") or {}).get("dateTime")
    end = (event.get("end") or {}).get("dateTime")
    if not start or not end:
        return None  # all-day events don't block time
    return (
        datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(tz),
        datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(tz),
    )


def _blocks_time(event: Dict[str, Any]) -> bool:
    """False for events the user isn't really busy for: declined, or marked free in Google."""
    if event.get("self_response") == "declined":
        return False
    raw = event.get("raw") or {}
    if raw.get("transparency") == "transparent":
        return False
    for attendee in event.get("attendees") or raw.get("attendees") or []:
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return False
    return True


def _busy_stretches(events: List[Dict[str, Any]], tz: ZoneInfo) -> List[_Busy]:
    spans = []
    for event in events:
        span = _timed_span(event, tz) if _blocks_time(event) else None
        if span and span[1] > span[0]:
            spans.append((span[0], span[1], event))
    spans.sort(key=lambda s: s[0])

    merged: List[_Busy] = []
    for start, end, event in spans:
        if merged and start <= merged[-1].end:
            if end > merged[-1].end:
                merged[-1].end = end
                merged[-1].last = event
        else:
            merged.append(_Busy(start, end, event, event))
    return merged


def _neighbor(event: Dict[str, Any], at: datetime) -> Dict[str, Any]:
    return {"summary": event.get("summary") or "(no title)", "time": at.isoformat()}


def free_blocks(
    events: List[Dict[str, Any]],
    *,
    start_time: datetime,
    end_time: datetime,
    duration: timedelta,
    buffer: timedelta,
    timezone_name: str,
    earliest: dtime = DEFAULT_EARLIEST,
    latest: Optional[dtime] = DEFAULT_LATEST,
) -> Dict[str, Any]:
    """Free blocks between start_time and end_time where `duration` fits with `buffer` around it.

    Each block has:
      start, end, minutes: the free gap (clipped to earliest-latest each day)
      earliest_start, latest_end: where the new event can go once the buffer is kept clear
      before / after: the event right before or after the gap ({summary, time}), or None
        when the gap runs to the edge of the day's hours
    """
    tz = ZoneInfo(timezone_name)
    start_time = start_time.astimezone(tz)
    end_time = end_time.astimezone(tz)
    busy = _busy_stretches(events, tz)

    # Gaps between busy stretches: (gap start, gap end, busy before, busy after)
    far_past = start_time - timedelta(days=365)
    far_future = end_time + timedelta(days=365)
    gaps = []
    cursor, previous = far_past, None
    for stretch in busy:
        if stretch.start > cursor:
            gaps.append((cursor, stretch.start, previous, stretch))
        if stretch.end > cursor:
            cursor, previous = stretch.end, stretch
    gaps.append((cursor, far_future, previous, None))

    blocks: List[Dict[str, Any]] = []
    total = 0
    day = start_time.date()
    while day <= end_time.date():
        window_start = max(start_time, _day_edge(day, earliest, tz))
        window_end = min(end_time, _day_edge(day, latest, tz))
        day += timedelta(days=1)
        if window_end <= window_start:
            continue
        for gap_start, gap_end, before, after in gaps:
            block_start = max(gap_start, window_start)
            block_end = min(gap_end, window_end)
            if block_end <= block_start:
                continue
            earliest_start = max(block_start, gap_start + buffer) if before else block_start
            latest_end = min(block_end, gap_end - buffer) if after else block_end
            if latest_end - earliest_start < duration:
                continue
            total += 1
            if len(blocks) >= MAX_BLOCKS:
                continue
            blocks.append({
                "start": block_start.isoformat(),
                "end": block_end.isoformat(),
                "minutes": int((block_end - block_start).total_seconds() // 60),
                "earliest_start": earliest_start.isoformat(),
                "latest_end": latest_end.isoformat(),
                # Only an event that actually borders the block, not one from an earlier day
                "before": _neighbor(before.last, before.end) if before and gap_start >= window_start else None,
                "after": _neighbor(after.first, after.start) if after and gap_end <= window_end else None,
            })

    result: Dict[str, Any] = {"blocks": blocks, "block_count": total}
    if total > len(blocks):
        result["note"] = f"Showing the first {len(blocks)} of {total} blocks; narrow the range to see the rest."
    return result


async def find_free_time_for_user(
    user_id: str,
    *,
    start_time: datetime,
    end_time: datetime,
    duration_minutes: int,
    buffer_minutes: int,
    timezone_name: str,
    earliest: dtime = DEFAULT_EARLIEST,
    latest: Optional[dtime] = DEFAULT_LATEST,
) -> Dict[str, Any]:
    """Load the user's events around the range and return its free blocks (see free_blocks)."""
    from domains.calendars.service import CalendarService
    from services.event_finder import cached_events_for_user

    tz = ZoneInfo(timezone_name)
    # A day either side, so events just outside the range still count toward the buffer
    fetch_from = start_time.astimezone(tz).date() - timedelta(days=1)
    fetch_to = end_time.astimezone(tz).date() + timedelta(days=1)

    fetch_start = time.time()
    source = "snapshots"
    try:
        events = await cached_events_for_user(user_id, fetch_from, fetch_to, timezone_name)
    except Exception:
        logger.exception("Snapshot read failed for free time user=%s", user_id)
        events = None
    if events is None:
        source = "google"
        result = await CalendarService().events_for_date_range(
            user_id=user_id, start_date=fetch_from, end_date=fetch_to, timezone_name=timezone_name,
        )
        events = result.get("events", [])
    log_step("backend.free_time.fetch_events", time.time() - fetch_start, details=f"source={source} events={len(events)}")

    return {
        **free_blocks(
            events,
            start_time=start_time,
            end_time=end_time,
            duration=timedelta(minutes=duration_minutes),
            buffer=timedelta(minutes=buffer_minutes),
            timezone_name=timezone_name,
            earliest=earliest,
            latest=latest,
        ),
        "duration_minutes": duration_minutes,
        "buffer_minutes": buffer_minutes,
        "daily_hours": f"{earliest.strftime('%H:%M')}-{latest.strftime('%H:%M') if latest else '24:00'}",
    }
