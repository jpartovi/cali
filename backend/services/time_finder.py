"""Pick when a new event should go (pre-pass for create requests without an exact time).

Two TypeSafe Jev calls with code in between:
  1. Read the request: does it give an exact start time? Which of the next 14 days could
     the event go on? How long is it, and how much buffer does it want around it?
     (Jev chooses between options; it can't extract free text, so each is a question.)
  2. Rank candidate start times, made from the free blocks on those days, against the
     request (so "in the afternoon" or "after class" pick the right slot).
The agent gets the top slots and can propose the event in one model call.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from typesafe_sdk import Choice, Noul

from core.timing_logger import log_step
from services.agent_runner import relative_dates_sheet
from services.free_time import find_free_time_for_user
from services.jev import get_jev_client

logger = logging.getLogger(__name__)

JEV_TIMEOUT_SECONDS = 10.0

# Days the event could go on, starting today (the snapshot table's horizon)
SEARCH_DAYS = 14
# A day counts as allowed at this probability or higher
DAY_MIN_PROBABILITY = 0.7
# Below this, the request is treated as giving an exact time (the agent needs no help)
EXACT_TIME_MAX_PROBABILITY = 0.5

DURATIONS = {
    "15": "15 minutes", "30": "30 minutes", "45": "45 minutes", "60": "1 hour",
    "90": "1.5 hours", "120": "2 hours", "180": "3 hours", "240": "4 hours",
}
BUFFERS = {
    "0": "None mentioned", "5": "5 minutes", "10": "10 minutes",
    "15": "15 minutes", "30": "30 minutes", "60": "1 hour",
}

# Hours each part of the day covers. With no time of day in the request, candidates come from
# normal waking hours; earlier or later hours are used only if nothing fits there.
TIMES_OF_DAY = {
    "any": ("No particular time of day", dtime(8, 0), dtime(21, 0)),
    "early_morning": ("Early morning, before 8 AM", dtime(5, 0), dtime(8, 0)),
    "morning": ("Morning", dtime(7, 0), dtime(12, 0)),
    "midday": ("Around midday or lunchtime", dtime(11, 0), dtime(14, 0)),
    "afternoon": ("Afternoon", dtime(12, 0), dtime(17, 0)),
    "evening": ("Evening", dtime(17, 0), dtime(22, 0)),
    "night": ("Late at night, after 10 PM", dtime(21, 0), None),
}

# Candidate start times fall on these minute marks; one Jev Choice holds at most 255 options
SLOT_STEP_MINUTES = 30
MAX_SLOTS = 254
TOP_MIN_PROBABILITY = 0.05
MAX_TOP_SLOTS = 3


def _clock(t: datetime) -> str:
    return t.strftime("%-I:%M %p")


def _slot_label(start: datetime, end: datetime) -> str:
    return f"{start.strftime('%a %b %-d')}, {_clock(start)} – {_clock(end)}"


def _neighbor_text(neighbor: Optional[Dict[str, Any]], tz: ZoneInfo, ends: bool) -> str:
    if not neighbor:
        return "start of the day" if ends else "end of the day"
    at = datetime.fromisoformat(neighbor["time"]).astimezone(tz)
    return f"{neighbor['summary']} ({'ends' if ends else 'starts'} {_clock(at)})"


def _candidate_slots(blocks: List[Dict[str, Any]], duration: timedelta, tz: ZoneInfo) -> List[Dict[str, Any]]:
    """Start times on the half hour inside each block, coarser if there would be too many."""
    for step in (SLOT_STEP_MINUTES, 60, 120, 240):
        slots = []
        for block in blocks:
            earliest = datetime.fromisoformat(block["earliest_start"]).astimezone(tz)
            latest_end = datetime.fromisoformat(block["latest_end"]).astimezone(tz)
            # First mark at or after earliest_start
            minutes = earliest.hour * 60 + earliest.minute
            start = earliest.replace(second=0, microsecond=0) + timedelta(minutes=(-minutes) % step)
            while start + duration <= latest_end:
                slots.append({"start": start, "end": start + duration, "block": block})
                start += timedelta(minutes=step)
        if len(slots) <= MAX_SLOTS:
            return slots
    return slots[:MAX_SLOTS]


async def _read_request(text: str, now: datetime, timezone_name: str) -> Dict[str, Any]:
    """Jev call 1: exact time?, allowed days, duration, buffer."""
    days = [now.date() + timedelta(days=i) for i in range(SEARCH_DAYS)]
    questions: Dict[str, Any] = {
        "exact_time": Noul(instructions="Does the request give a specific start time (e.g. 'at 3pm', 'at noon')?"),
        "duration": Choice(
            instructions="How long should the event be? If the request doesn't say, the usual length for that kind of event.",
            criteria=DURATIONS,
        ),
        "buffer": Choice(
            instructions="How much free time does the request want kept before and after the event?",
            criteria=BUFFERS,
        ),
        "time_of_day": Choice(
            instructions="What time of day does the request ask for?",
            criteria={key: label for key, (label, _, _) in TIMES_OF_DAY.items()},
        ),
    }
    for i, day in enumerate(days):
        label = day.strftime("%a %b %-d") + (" (today)" if day == now.date() else "")
        questions[f"d{i}"] = Noul(instructions=f"Could the event go on {label}?")

    response = await get_jev_client().system_one(
        state={
            "request": text,
            "now": now.strftime("%a %b %-d %Y, %-I:%M %p"),
            "relative_dates": relative_dates_sheet(now, timezone_name),
        },
        questions=questions,
        timeout=JEV_TIMEOUT_SECONDS,
    )
    return {
        "exact_time": response.nouls["exact_time"].noul,
        "days": [day for i, day in enumerate(days) if response.nouls[f"d{i}"].noul >= DAY_MIN_PROBABILITY],
        "duration_minutes": int(response.choices["duration"].choice),
        "buffer_minutes": int(response.choices["buffer"].choice),
        "time_of_day": response.choices["time_of_day"].choice,
    }


async def _rank_slots(text: str, now: datetime, timezone_name: str, slots: List[Dict[str, Any]], tz: ZoneInfo) -> Dict[str, float]:
    """Jev call 2: how well each candidate start time fits the request."""
    criteria: Dict[str, Any] = {
        f"s{i}": {
            "when": _slot_label(slot["start"], slot["end"]),
            # The free block around the slot
            "free_since": _neighbor_text(slot["block"].get("before"), tz, ends=True),
            "free_until": _neighbor_text(slot["block"].get("after"), tz, ends=False),
        }
        for i, slot in enumerate(slots)
    }
    criteria["none"] = "None of these times fits the request"
    response = await get_jev_client().system_one(
        state={
            "request": text,
            "now": now.strftime("%a %b %-d %Y, %-I:%M %p"),
            "relative_dates": relative_dates_sheet(now, timezone_name),
        },
        questions={
            "slot": Choice(
                instructions=(
                    "Which time would this person most likely choose for this event? Follow anything the request "
                    "says about timing (afternoon, after class, early). Otherwise pick a normal time of day for "
                    "that kind of event (appointments during business hours, meals at mealtimes, workouts in the "
                    "morning or evening), and sooner days over later ones."
                ),
                criteria=criteria,
            ),
        },
        timeout=JEV_TIMEOUT_SECONDS,
    )
    return response.choices["slot"].probabilities


async def find_time_for_new_event(user_id: str, text: str, timezone_name: str) -> Dict[str, Any]:
    """Suggest start times for the event a create request describes.

    Returns a dict with status:
      "exact_time": the request already says when; nothing to suggest
      "no_days": Jev wasn't sure which days the request means
      "none": nothing free fits (searched days, duration and buffer included)
      "slots": "top" holds up to 3 suggested slots with their probability
    plus duration_minutes, buffer_minutes, days and jev_ms.
    """
    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)

    jev_start = time.time()
    request = await _read_request(text, now, timezone_name)
    read_ms = round((time.time() - jev_start) * 1000)
    log_step("backend.time_finder.read_request", read_ms / 1000)
    base: Dict[str, Any] = {
        "duration_minutes": request["duration_minutes"],
        "buffer_minutes": request["buffer_minutes"],
        "days": [d.isoformat() for d in request["days"]],
        "time_of_day": request["time_of_day"],
        "jev_ms": read_ms,
    }
    if request["exact_time"] >= EXACT_TIME_MAX_PROBABILITY:
        return {"status": "exact_time", **base}
    if not request["days"]:
        return {"status": "no_days", **base}

    # Free time across the allowed days, but only from now on for today
    days: List[date] = request["days"]
    start_time = max(now, datetime.combine(days[0], datetime.min.time(), tz))
    end_time = datetime.combine(days[-1] + timedelta(days=1), datetime.min.time(), tz)
    duration = timedelta(minutes=request["duration_minutes"])
    allowed = set(days)
    _, earliest, latest = TIMES_OF_DAY[request["time_of_day"]]
    # The asked-for hours first; the whole day (free_time's defaults) only if nothing fits there
    for hours in ({"earliest": earliest, "latest": latest}, {}):
        free = await find_free_time_for_user(
            user_id,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=request["duration_minutes"],
            buffer_minutes=request["buffer_minutes"],
            timezone_name=timezone_name,
            **hours,
        )
        blocks = [b for b in free["blocks"] if datetime.fromisoformat(b["start"]).astimezone(tz).date() in allowed]
        slots = _candidate_slots(blocks, duration, tz)
        if slots:
            break
    if not slots:
        return {"status": "none", **base}

    rank_start = time.time()
    probabilities = await _rank_slots(text, now, timezone_name, slots, tz)
    rank_ms = round((time.time() - rank_start) * 1000)
    log_step("backend.time_finder.rank_slots", rank_ms / 1000, details=f"slots={len(slots)}")
    base["jev_ms"] = read_ms + rank_ms
    base["slot_count"] = len(slots)

    ranked = sorted(probabilities.items(), key=lambda kv: kv[1], reverse=True)
    top = []
    for key, p in ranked:
        if key == "none" or p < TOP_MIN_PROBABILITY or len(top) >= MAX_TOP_SLOTS:
            continue
        slot = slots[int(key[1:])]
        top.append({
            "start": slot["start"].isoformat(),
            "end": slot["end"].isoformat(),
            "when": _slot_label(slot["start"], slot["end"]),
            "before": slot["block"].get("before"),
            "after": slot["block"].get("after"),
            "probability": round(p, 3),
        })
    if not top:
        return {"status": "none", **base}
    return {"status": "slots", "top": top, **base}
