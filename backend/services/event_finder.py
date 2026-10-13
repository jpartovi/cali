"""Find the calendar event a request refers to.

Fetches every event in a time range and asks TypeSafe Jev to choose which one
the description means, with "none" as an option. Jev returns a probability per
event, so the caller can tell a clear match from an ambiguous one.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from typesafe_sdk import Choice, TypeSafeError

from core.config import get_settings
from core.timing_logger import log_step
from services.agent_runner import relative_dates_sheet
from services.jev import get_jev_client
from domains.calendars.service import CalendarService

logger = logging.getLogger(__name__)

# Jev's Choice question takes at most 255 options; one is reserved for "none"
MAX_CANDIDATES = 254
NONE_KEY = "none"

# A match needs a confident winner that clearly beats the runner-up
MATCH_MIN_PROBABILITY = 0.6
MATCH_MIN_MARGIN = 0.25
# Events at or above this probability are offered back when the pick is ambiguous
CANDIDATE_MIN_PROBABILITY = 0.1
MAX_AMBIGUOUS_CANDIDATES = 3
# Runner-ups always reported (in "top"), so callers can see when Jev was torn
TOP_MIN_PROBABILITY = 0.05
MAX_TOP_EVENTS = 3

JEV_TIMEOUT_SECONDS = 10.0


class EventFinderNotConfigured(RuntimeError):
    """TYPESAFE_API_KEY is not set."""


def _event_time(value: Any, tz: ZoneInfo) -> Optional[datetime | date]:
    """Parse a Google start/end ({dateTime}, {date} or an ISO string) in the user's timezone."""
    if isinstance(value, dict):
        if value.get("dateTime"):
            value = value["dateTime"]
        elif value.get("date"):
            return date.fromisoformat(value["date"])
        else:
            return None
    if not isinstance(value, str) or not value:
        return None
    if len(value) == 10:
        return date.fromisoformat(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz)


def _describe_when(start: Any, end: Any, tz: ZoneInfo) -> str:
    """Human-readable time span, e.g. 'Tue Oct 13, 4:45 PM – 5:45 PM'."""
    start_t = _event_time(start, tz)
    end_t = _event_time(end, tz)
    if isinstance(start_t, datetime):
        text = start_t.strftime("%a %b %-d, %-I:%M %p")
        if isinstance(end_t, datetime):
            same_day = end_t.date() == start_t.date()
            text += " – " + end_t.strftime("%-I:%M %p" if same_day else "%a %b %-d, %-I:%M %p")
        return text
    if isinstance(start_t, date):
        return start_t.strftime("%a %b %-d") + " (all day)"
    return "unknown time"


def _option_for(event: Dict[str, Any], tz: ZoneInfo) -> Dict[str, Any]:
    """The structured option Jev sees for one event."""
    option = {
        "title": event.get("summary") or "(no title)",
        "when": _describe_when(event.get("start"), event.get("end"), tz),
        "calendar": event.get("calendar_name"),
    }
    location = event.get("location") or (event.get("raw") or {}).get("location")
    if location:
        option["location"] = location
    description = (event.get("description") or "").strip()
    if description:
        option["notes"] = description[:200]
    return option


def _public_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Event fields returned to the agent (same shape as the search endpoint)."""
    return {
        "id": event.get("id"),
        "event_id": event.get("id"),
        "calendar_id": event.get("calendar_id"),
        "calendar_name": event.get("calendar_name"),
        "summary": event.get("summary"),
        "start": event.get("start"),
        "end": event.get("end"),
        "location": event.get("location") or (event.get("raw") or {}).get("location"),
    }


def snapshot_events_for_user(user_id: str, start_date: date, end_date: date, timezone_name: str) -> List[Dict[str, Any]]:
    """Events between start_date and end_date from calendar_event_snapshots, in the finder's format.

    The calendar listener keeps these rows in sync with Google (yesterday through 14
    days ahead), so reading them is a ~70ms database query instead of a Google fetch.
    Only visible calendars are included, matching the Google path.
    """
    from datetime import timedelta
    from db.session import get_service_client
    from domains.calendars.repository import CalendarRepository

    tz = ZoneInfo(timezone_name)
    window_start = datetime.combine(start_date, datetime.min.time(), tz)
    window_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tz)
    calendars = {cal["google_calendar_id"]: cal.get("name") for cal in CalendarRepository().get_calendars(user_id)}
    rows = (
        get_service_client()
        .table("calendar_event_snapshots")
        .select("google_event_id,google_calendar_id,status,title,location,start_at,end_at,is_all_day,timezone,self_response")
        .eq("user_id", user_id)
        .neq("status", "cancelled")
        .gte("end_at", window_start.isoformat())
        .lt("start_at", window_end.isoformat())
        .execute()
        .data
    ) or []

    events = []
    for row in rows:
        calendar_id = row.get("google_calendar_id")
        if calendar_id not in calendars or not row.get("start_at") or not row.get("end_at"):
            continue
        start_at = datetime.fromisoformat(row["start_at"])
        end_at = datetime.fromisoformat(row["end_at"])
        if row.get("is_all_day"):
            # Stored as midnight in the event's timezone; convert back to calendar dates
            event_tz = ZoneInfo(row["timezone"]) if row.get("timezone") else tz
            start = {"date": start_at.astimezone(event_tz).date().isoformat()}
            end = {"date": end_at.astimezone(event_tz).date().isoformat()}
        else:
            start = {"dateTime": start_at.astimezone(tz).isoformat()}
            end = {"dateTime": end_at.astimezone(tz).isoformat()}
        events.append({
            "id": row["google_event_id"],
            "calendar_id": calendar_id,
            "calendar_name": calendars[calendar_id],
            "summary": row.get("title"),
            "location": row.get("location"),
            "start": start,
            "end": end,
            "self_response": row.get("self_response"),
        })
    events.sort(key=lambda e: e["start"].get("dateTime") or e["start"].get("date"))
    return events


async def find_event_for_user(
    user_id: str,
    description: str,
    start_date: date,
    end_date: date,
    timezone_name: str,
    events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Pick the event `description` refers to among events between start_date and end_date.

    Pass `events` (already in the finder's format) to skip fetching them from Google.

    Returns a dict with:
      status: "match" | "ambiguous" | "none" | "too_many"
      event: the matched event (status "match" only)
      candidates: likely events with their probability (status "ambiguous")
      top: the most likely events with their probability, whatever the status
      confidence, event_count, jev_ms
    """
    settings = get_settings()
    if not settings.typesafe_api_key:
        raise EventFinderNotConfigured("TYPESAFE_API_KEY is not set")

    tz = ZoneInfo(timezone_name)
    if events is None:
        fetch_start = time.time()
        result = await CalendarService().events_for_date_range(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            timezone_name=timezone_name,
        )
        events = [e for e in result.get("events", []) if e.get("id") and e.get("calendar_id")]
        log_step("backend.event_finder.fetch_events", time.time() - fetch_start, details=f"events={len(events)}")

    if not events:
        return {"status": "none", "event": None, "candidates": [], "confidence": 1.0, "event_count": 0, "jev_ms": 0, "top": []}
    if len(events) > MAX_CANDIDATES:
        return {"status": "too_many", "event": None, "candidates": [], "confidence": 0.0, "event_count": len(events), "jev_ms": 0, "top": []}

    # Short keys keep the request small; map them back to events afterwards
    keyed = {f"e{i}": event for i, event in enumerate(events)}
    criteria: Dict[str, Any] = {key: _option_for(event, tz) for key, event in keyed.items()}
    criteria[NONE_KEY] = "None of these events is the one the request refers to"

    now = datetime.now(tz)
    state = {
        "request": description,
        "now": now.strftime("%a %b %-d %Y, %-I:%M %p"),
        "timezone": timezone_name,
        # Same date rules as the agent, e.g. "next Wednesday" is next week's, not the coming one
        "relative_dates": relative_dates_sheet(now, timezone_name),
    }

    jev_start = time.time()
    try:
        response = await get_jev_client().system_one(
            state=state,
            questions={
                "event": Choice(
                    instructions="Which calendar event is this request about?",
                    criteria=criteria,
                ),
            },
            timeout=JEV_TIMEOUT_SECONDS,
        )
    except TypeSafeError:
        logger.exception("Jev event selection failed user_id=%s", user_id)
        raise
    jev_ms = round((time.time() - jev_start) * 1000)
    log_step("backend.event_finder.jev", jev_ms / 1000, details=f"options={len(criteria)}")

    answer = response.choices["event"]
    ranked = sorted(answer.probabilities.items(), key=lambda kv: kv[1], reverse=True)
    top_key, top_p = ranked[0]
    runner_up_p = ranked[1][1] if len(ranked) > 1 else 0.0

    top = [
        {
            **_public_event(keyed[key]),
            "when": _describe_when(keyed[key].get("start"), keyed[key].get("end"), tz),
            "probability": round(p, 3),
        }
        for key, p in ranked
        if key != NONE_KEY and p >= TOP_MIN_PROBABILITY
    ][:MAX_TOP_EVENTS]
    base = {"confidence": answer.confidence, "event_count": len(events), "jev_ms": jev_ms, "top": top}

    if top_key == NONE_KEY and top_p >= MATCH_MIN_PROBABILITY:
        return {"status": "none", "event": None, "candidates": [], **base}

    if top_key != NONE_KEY and top_p >= MATCH_MIN_PROBABILITY and top_p - runner_up_p >= MATCH_MIN_MARGIN:
        return {
            "status": "match",
            "event": {**_public_event(keyed[top_key]), "probability": round(top_p, 3)},
            "candidates": [],
            **base,
        }

    candidates: List[Dict[str, Any]] = [
        {**_public_event(keyed[key]), "probability": round(p, 3)}
        for key, p in ranked
        if key != NONE_KEY and p >= CANDIDATE_MIN_PROBABILITY
    ][:MAX_AMBIGUOUS_CANDIDATES]
    return {"status": "ambiguous" if candidates else "none", "event": None, "candidates": candidates, **base}
