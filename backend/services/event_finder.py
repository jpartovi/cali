"""Find the calendar event a request refers to.

Fetches every event in a time range and asks TypeSafe Jev to choose which one
the description means, with "none" as an option. Jev returns a probability per
event, so the caller can tell a clear match from an ambiguous one.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from typesafe_sdk import Choice, Noul, TypeSafeError

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


def _guests(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The event's guests other than the user (Google attendee dicts), as {display_name, email}."""
    guests = []
    for attendee in event.get("attendees") or []:
        if not isinstance(attendee, dict) or attendee.get("self") or attendee.get("resource") or not attendee.get("email"):
            continue
        guests.append({"display_name": attendee.get("displayName"), "email": attendee["email"]})
    return guests


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
        "guests": _guests(event),
    }


def snapshot_events_for_user(user_id: str, start_date: date, end_date: date, timezone_name: str) -> List[Dict[str, Any]]:
    """Events between start_date and end_date from calendar_event_snapshots, in the finder's format.

    The calendar listener keeps these rows in sync with Google (yesterday through 14
    days ahead, plus days loaded on demand), so reading them is a ~70ms database query
    instead of a Google fetch. Only visible calendars are included, matching the Google path.
    """
    from datetime import timedelta
    from db.session import get_service_client

    tz = ZoneInfo(timezone_name)
    window_start = datetime.combine(start_date, datetime.min.time(), tz)
    window_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tz)
    rows = (
        get_service_client()
        .table("calendar_event_snapshots")
        .select("google_event_id,google_calendar_id,status,title,description,location,start_at,end_at,is_all_day,timezone,self_response,attendees")
        .eq("user_id", user_id)
        .neq("status", "cancelled")
        .gte("end_at", window_start.isoformat())
        .lt("start_at", window_end.isoformat())
        .execute()
        .data
    ) or []
    return snapshot_rows_to_events(user_id, rows, timezone_name, start_date, end_date)


def snapshot_rows_to_events(
    user_id: str,
    rows: List[Dict[str, Any]],
    timezone_name: str,
    first_day: Optional[date] = None,
    last_day: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """calendar_event_snapshots rows as events in the finder's format (visible calendars only).

    With first_day/last_day, all-day events outside those dates are left out.
    """
    from domains.calendars.repository import CalendarRepository

    tz = ZoneInfo(timezone_name)
    calendars = {cal["google_calendar_id"]: cal.get("name") for cal in CalendarRepository().get_calendars(user_id)}
    events = []
    for row in rows:
        calendar_id = row.get("google_calendar_id")
        if row.get("status") == "cancelled" or calendar_id not in calendars:
            continue
        if not row.get("start_at") or not row.get("end_at"):
            continue
        start_at = datetime.fromisoformat(row["start_at"])
        end_at = datetime.fromisoformat(row["end_at"])
        if row.get("is_all_day"):
            # Stored as midnight in the event's timezone, or UTC when it has none (see
            # calendar_listener/snapshots.py); convert back to calendar dates
            event_tz = ZoneInfo(row["timezone"]) if row.get("timezone") else timezone.utc
            start_day = start_at.astimezone(event_tz).date()
            end_day = end_at.astimezone(event_tz).date()
            # The UTC row query reaches into the neighboring local days; keep only overlaps
            if (first_day and end_day <= first_day) or (last_day and start_day > last_day):
                continue
            start = {"date": start_day.isoformat()}
            end = {"date": end_day.isoformat()}
        else:
            start = {"dateTime": start_at.astimezone(tz).isoformat()}
            end = {"dateTime": end_at.astimezone(tz).isoformat()}
        events.append({
            "id": row["google_event_id"],
            "calendar_id": calendar_id,
            "calendar_name": calendars[calendar_id],
            "summary": row.get("title"),
            "description": row.get("description"),
            "location": row.get("location"),
            "status": row.get("status"),
            "start": start,
            "end": end,
            "self_response": row.get("self_response"),
            "attendees": row.get("attendees") or [],
        })
    # Same order as CalendarService: timed events by start, then all-day events
    events.sort(key=lambda e: (0, e["start"]["dateTime"]) if "dateTime" in e["start"] else (1, e["start"]["date"]))
    return events


async def cached_events_for_user(
    user_id: str, start_date: date, end_date: date, timezone_name: str
) -> Optional[List[Dict[str, Any]]]:
    """Events in the range from snapshots, loading any days not stored yet from Google first.

    Loaded days stay in sync through the calendar listener, so the next request for them
    (from the agent or the app) is a database read. None when the range is too long or a
    visible calendar isn't synced, so the caller should read Google instead.
    """
    from domains.calendar_listener.service import MAX_FILL_DAYS, CalendarListenerService, run_off_event_loop

    if (end_date - start_date).days + 1 > MAX_FILL_DAYS:
        return None
    result = await run_off_event_loop(
        lambda: CalendarListenerService().fill_range(user_id, start_date, end_date, timezone_name)
    )
    if not result["complete"]:
        return None
    import asyncio

    return await asyncio.to_thread(
        snapshot_rows_to_events, user_id, result["snapshots"], timezone_name, start_date, end_date
    )


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
        source = "snapshots"
        try:
            events = await cached_events_for_user(user_id, start_date, end_date, timezone_name)
        except Exception:
            logger.exception("Snapshot read failed for find_event user=%s", user_id)
            events = None
        if events is None:
            source = "google"
            result = await CalendarService().events_for_date_range(
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
                timezone_name=timezone_name,
            )
            events = [e for e in result.get("events", []) if e.get("id") and e.get("calendar_id")]
        log_step("backend.event_finder.fetch_events", time.time() - fetch_start, details=f"source={source} events={len(events)}")

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


# A request "mentions" an event at this probability or higher (pre-pass)
MENTION_MIN_PROBABILITY = 0.5
# One yes/no question per event; past this many events only the main-event question is asked
MAX_MENTION_QUESTIONS = 120


async def find_request_events(
    user_id: str,
    text: str,
    events: List[Dict[str, Any]],
    timezone_name: str,
) -> Dict[str, Any]:
    """Every event the request refers to, and which one it most likely acts on (pre-pass).

    One Jev call: a choice of the event the request acts on (shows, changes, cancels or
    invites people to), plus a yes/no per event for whether the request refers to it at
    all, so "move my haircut to right after lunch" brings in the haircut and lunch.
    `events` are in the finder's format (e.g. from snapshot_events_for_user).

    Returns a dict with:
      status: "match" | "ambiguous" | "none" | "too_many" for the event acted on (as find_event_for_user)
      top: likely events acted on, with probability
      mentioned: every event the request refers to (probability >= MENTION_MIN_PROBABILITY), highest first
      event_count, jev_ms
    """
    settings = get_settings()
    if not settings.typesafe_api_key:
        raise EventFinderNotConfigured("TYPESAFE_API_KEY is not set")
    base: Dict[str, Any] = {"event_count": len(events), "jev_ms": 0, "top": [], "mentioned": []}
    if not events:
        return {"status": "none", **base}
    if len(events) > MAX_CANDIDATES:
        return {"status": "too_many", **base}

    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)
    keyed = {f"e{i}": event for i, event in enumerate(events)}
    options = {key: _option_for(event, tz) for key, event in keyed.items()}
    questions: Dict[str, Any] = {
        "event": Choice(
            instructions=(
                "Which existing calendar event is this request acting on: showing, changing, cancelling, or inviting, "
                "adding or removing people? If the request could be about an existing event (e.g. 'invite Henry to "
                "dinner tomorrow' and there is a dinner tomorrow), pick it. 'none' only if it clearly needs a new event."
            ),
            criteria={**options, NONE_KEY: "No existing event: the request doesn't act on one of these"},
        ),
    }
    ask_mentions = len(events) <= MAX_MENTION_QUESTIONS
    if ask_mentions:
        for key in keyed:
            questions[f"m_{key}"] = Noul(
                instructions=(
                    f"Could the request be referring to event {key}? Yes if the request names it, describes it "
                    "(e.g. 'dinner tomorrow', 'my next lunch with Henry') or uses it as a reference point "
                    "(e.g. 'right after lunch'), even if the request might instead mean a new event."
                )
            )

    jev_start = time.time()
    try:
        response = await get_jev_client().system_one(
            state={
                "request": text,
                "now": now.strftime("%a %b %-d %Y, %-I:%M %p"),
                "relative_dates": relative_dates_sheet(now, timezone_name),
                # Questions refer to events by key; one copy of the list keeps the request small
                "events": {key: f"{o['title']} ({o['when']})" for key, o in options.items()},
            },
            questions=questions,
            timeout=JEV_TIMEOUT_SECONDS,
        )
    except TypeSafeError:
        logger.exception("Jev request events failed user_id=%s", user_id)
        raise
    jev_ms = round((time.time() - jev_start) * 1000)
    log_step("backend.event_finder.request_events_jev", jev_ms / 1000, details=f"events={len(events)}")
    base["jev_ms"] = jev_ms

    def described(key: str, p: float) -> Dict[str, Any]:
        event = keyed[key]
        return {
            **_public_event(event),
            "when": _describe_when(event.get("start"), event.get("end"), tz),
            "probability": round(p, 3),
        }

    ranked = sorted(response.choices["event"].probabilities.items(), key=lambda kv: kv[1], reverse=True)
    top_key, top_p = ranked[0]
    runner_up_p = ranked[1][1] if len(ranked) > 1 else 0.0
    base["top"] = [described(k, p) for k, p in ranked if k != NONE_KEY and p >= TOP_MIN_PROBABILITY][:MAX_TOP_EVENTS]
    if ask_mentions:
        mentions = sorted(((k, response.nouls[f"m_{k}"].noul) for k in keyed), key=lambda kv: kv[1], reverse=True)
        base["mentioned"] = [described(k, p) for k, p in mentions if p >= MENTION_MIN_PROBABILITY]

    if top_key == NONE_KEY:
        status = "none"
    elif top_p >= MATCH_MIN_PROBABILITY and top_p - runner_up_p >= MATCH_MIN_MARGIN:
        status = "match"
    else:
        status = "ambiguous"
    return {"status": status, **base}



# Past events the request refers back to: most this many are reported, highest first
MAX_PAST_EVENTS = 8


async def find_past_events(
    user_id: str,
    text: str,
    events: List[Dict[str, Any]],
    timezone_name: str,
) -> Dict[str, Any]:
    """The past events a request refers back to ("the people from last week's dinner", "same place as last time").

    One yes/no question per event, asked in batches of MAX_MENTION_QUESTIONS side by side, so
    weeks of past events can be searched at once. Unlike find_request_events' mention
    question, it counts earlier events of the same kind under another title ("Barry's" for
    "my last workout"). `events` are in the finder's format.

    Returns a dict with:
      mentioned: the past events the request likely means (probability >= MENTION_MIN_PROBABILITY),
        the MAX_PAST_EVENTS likeliest, newest first
      event_count, jev_ms
    """
    import asyncio

    settings = get_settings()
    if not settings.typesafe_api_key:
        raise EventFinderNotConfigured("TYPESAFE_API_KEY is not set")
    base: Dict[str, Any] = {"event_count": len(events), "jev_ms": 0, "mentioned": []}
    if not events:
        return base

    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)
    keyed = {f"e{i}": event for i, event in enumerate(events)}
    options = {key: _option_for(event, tz) for key, event in keyed.items()}
    keys = list(keyed)
    batches = [keys[i:i + MAX_MENTION_QUESTIONS] for i in range(0, len(keys), MAX_MENTION_QUESTIONS)]

    async def ask(batch: List[str]) -> Dict[str, float]:
        response = await get_jev_client().system_one(
            state={
                "request": text,
                "now": now.strftime("%a %b %-d %Y, %-I:%M %p"),
                "relative_dates": relative_dates_sheet(now, timezone_name),
                "past_events": {key: f"{options[key]['title']} ({options[key]['when']})" for key in batch},
            },
            questions={
                key: Noul(
                    instructions=(
                        f"The request refers back to something that already happened. Is past event {key} one "
                        "it could mean? Yes if it names or describes the event, or is about an earlier event of "
                        "the same kind and this is one, even under a different title (\"Barry's\" for \"my last "
                        "workout\", \"Dinner @ Pammy's\" for \"the last dinner with Sam\"). When it means the last one (\"last time\", "
                        "\"the previous one\"), the most recent of that kind is the likeliest."
                    )
                )
                for key in batch
            },
            timeout=JEV_TIMEOUT_SECONDS,
        )
        return {key: response.nouls[key].noul for key in batch}

    jev_start = time.time()
    try:
        answers = await asyncio.gather(*(ask(batch) for batch in batches))
    except TypeSafeError:
        logger.exception("Jev past events failed user_id=%s", user_id)
        raise
    jev_ms = round((time.time() - jev_start) * 1000)
    log_step("backend.event_finder.past_events_jev", jev_ms / 1000, details=f"events={len(events)} batches={len(batches)}")
    base["jev_ms"] = jev_ms

    scores = sorted(((k, p) for answer in answers for k, p in answer.items()), key=lambda kv: kv[1], reverse=True)
    likely = [(key, p) for key, p in scores if p >= MENTION_MIN_PROBABILITY][:MAX_PAST_EVENTS]
    # Newest first: "the previous one" / "last time" usually means the most recent
    likely.sort(key=lambda kv: _event_sort_key(keyed[kv[0]]), reverse=True)
    base["mentioned"] = [
        {
            **_public_event(keyed[key]),
            "when": _describe_when(keyed[key].get("start"), keyed[key].get("end"), tz),
            "probability": round(p, 3),
        }
        for key, p in likely
    ]
    return base


def _event_sort_key(event: Dict[str, Any]) -> str:
    start = event.get("start") or {}
    return start.get("dateTime") or start.get("date") or ""
