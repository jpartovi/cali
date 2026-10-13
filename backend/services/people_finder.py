"""Work out which people a request is talking about (people pre-pass).

Supabase finds the candidates: contacts and people the user has had events with whose
names appear in the request (the people_candidates function, trigram matching, no LLM),
with how often and how recently the user has been on events with or invited each one
(calendar_event_people). Jev then answers one yes/no question per candidate, so a
request can name several people ("lunch with Henry and Sam") and Jev can tell two
Henrys apart by which one the user actually sees. The agent gets the result as context and decides what to do with it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from typesafe_sdk import Noul, TypeSafeError

from core.config import get_settings
from core.timing_logger import log_step
from domains.contacts.repository import ContactsRepository
from services.jev import get_jev_client

logger = logging.getLogger(__name__)

# How many name matches Jev weighs at most (the RPC sorts by match strength, then recent events together)
MAX_CANDIDATES = 15
# A candidate at or above this probability is someone the request is talking about
MENTION_MIN_PROBABILITY = 0.7
# Below MENTION_MIN_PROBABILITY but at or above this, a candidate is reported as a maybe
MAYBE_MIN_PROBABILITY = 0.3

JEV_TIMEOUT_SECONDS = 10.0


class PeopleFinderNotConfigured(RuntimeError):
    """TYPESAFE_API_KEY is not set."""


def _first_name(person: Dict[str, Any]) -> str:
    name = (person.get("display_name") or person.get("email") or "").strip().lower()
    return name.split()[0] if name else ""


def _day(value: Any, tz: ZoneInfo) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(tz).strftime("%b %-d")
    except ValueError:
        return None


def _details(person: Dict[str, Any], tz: ZoneInfo) -> str:
    """Where the candidate comes from and how the user knows them (shown to Jev and the agent)."""
    parts = []
    if person.get("nickname"):
        parts.append(f"nickname \"{person['nickname']}\"")
    parts.append("in contacts" if person.get("in_contacts") else "not in contacts (from calendar events)")
    together = person.get("events_together") or 0
    invited = person.get("invited_count") or 0
    if together:
        detail = f"{together} event{'s' if together != 1 else ''} together in the past year"
        if invited:
            detail += f" ({invited} of them invited by the user)"
        parts.append(detail)
    last, upcoming = _day(person.get("last_together_at"), tz), _day(person.get("next_together_at"), tz)
    if last:
        parts.append(f"last together {last}")
    if upcoming:
        parts.append(f"next together {upcoming}")
    if not together:
        parts.append("no events together")
    return "; ".join(parts)


def _describe(person: Dict[str, Any], tz: ZoneInfo) -> str:
    """The candidate as Jev sees it: name, then details."""
    return f"{person.get('display_name') or person.get('email') or 'Unknown'} ({_details(person, tz)})"


def _public_person(person: Dict[str, Any], tz: ZoneInfo, probability: float) -> Dict[str, Any]:
    return {
        "contact_id": person.get("contact_id"),
        "display_name": person.get("display_name") or person.get("email"),
        "email": person.get("email"),
        "in_contacts": bool(person.get("in_contacts")),
        "events_together": person.get("events_together") or 0,
        "invited_count": person.get("invited_count") or 0,
        "affinity": round(person.get("affinity") or 0, 2),
        "details": _details(person, tz),
        "probability": round(probability, 3),
    }


async def find_people_for_user(user_id: str, text: str, timezone_name: str) -> Dict[str, Any]:
    """Resolve the people `text` talks about.

    Returns a dict with:
      status: "none" (no names matched, or Jev said nobody) | "found"
      people: people the request is talking about (probability >= MENTION_MIN_PROBABILITY)
      ambiguous: groups of same-first-name candidates where Jev couldn't pick one
      maybe: other candidates Jev half-believed
      candidate_count, db_ms, jev_ms
    """
    settings = get_settings()
    if not settings.typesafe_api_key:
        raise PeopleFinderNotConfigured("TYPESAFE_API_KEY is not set")

    tz = ZoneInfo(timezone_name)
    db_start = time.time()
    candidates = await asyncio.to_thread(ContactsRepository().people_candidates, user_id, text, MAX_CANDIDATES)
    db_ms = round((time.time() - db_start) * 1000)
    log_step("backend.people_finder.candidates", db_ms / 1000, details=f"candidates={len(candidates)}")
    base: Dict[str, Any] = {"candidate_count": len(candidates), "db_ms": db_ms, "jev_ms": 0}
    if not candidates:
        return {"status": "none", "people": [], "ambiguous": [], "maybe": [], **base}

    keyed = {f"p{i}": person for i, person in enumerate(candidates)}
    descriptions = {key: _describe(person, tz) for key, person in keyed.items()}
    questions = {
        key: Noul(
            instructions=(
                f"Is the request talking about this person: {description}? "
                "If the request names one person who could be several of the candidates, answer yes only for "
                "the one they most likely mean: usually the one they've had events with or invited most recently "
                "and most often, especially if they have an event together coming up."
            )
        )
        for key, description in descriptions.items()
    }

    jev_start = time.time()
    try:
        response = await get_jev_client().system_one(
            # Every candidate is in the state, so each question can weigh the others
            state={"request": text, "candidates": list(descriptions.values())},
            questions=questions,
            timeout=JEV_TIMEOUT_SECONDS,
        )
    except TypeSafeError:
        logger.exception("Jev people resolution failed user_id=%s", user_id)
        raise
    jev_ms = round((time.time() - jev_start) * 1000)
    log_step("backend.people_finder.jev", jev_ms / 1000, details=f"candidates={len(candidates)}")
    base["jev_ms"] = jev_ms

    scored = sorted(
        ((keyed[key], response.nouls[key].noul) for key in keyed),
        key=lambda pair: pair[1],
        reverse=True,
    )
    people = [_public_person(p, tz, prob) for p, prob in scored if prob >= MENTION_MIN_PROBABILITY]

    # Unsure candidates: same first name and nobody by that name picked -> ambiguous; otherwise a maybe
    picked_first_names = {_first_name(p) for p, prob in scored if prob >= MENTION_MIN_PROBABILITY}
    unsure: Dict[str, List[Dict[str, Any]]] = {}
    for person, prob in scored:
        if MAYBE_MIN_PROBABILITY <= prob < MENTION_MIN_PROBABILITY:
            unsure.setdefault(_first_name(person), []).append(_public_person(person, tz, prob))
    ambiguous: List[List[Dict[str, Any]]] = []
    maybe: List[Dict[str, Any]] = []
    for first_name, group in unsure.items():
        if first_name in picked_first_names:
            continue  # Jev already picked someone by this name; the rest are runner-ups
        if len(group) > 1:
            ambiguous.append(group)
        else:
            maybe.extend(group)

    status = "found" if people or ambiguous or maybe else "none"
    return {"status": status, "people": people, "ambiguous": ambiguous, "maybe": maybe, **base}


def people_summary(result: Optional[Dict[str, Any]]) -> str:
    """One log line: who was found, with probabilities."""
    if not result:
        return "none"
    found = [(p["display_name"], p["probability"]) for p in result.get("people", [])]
    ambiguous = [[(p["display_name"], p["probability"]) for p in g] for g in result.get("ambiguous", [])]
    maybe = [(p["display_name"], p["probability"]) for p in result.get("maybe", [])]
    return f"people={found} ambiguous={ambiguous} maybe={maybe}"
