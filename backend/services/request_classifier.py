"""Classify what kind of calendar request a message is (stage 1 of the Jev pre-pass).

One TypeSafe Jev Choice question over a fixed set of request kinds. The answer
carries a probability per kind, so callers can act only when Jev is sure and
otherwise let the agent work the request out as it does today.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from typesafe_sdk import Choice, TypeSafeError

from core.config import get_settings
from core.timing_logger import log_step
from services.jev import get_jev_client

logger = logging.getLogger(__name__)

# What Jev chooses between. The descriptions are what it matches the request against.
REQUEST_KINDS: Dict[str, str] = {
    "show_schedule": "See what's on the calendar for a day or a span of time",
    "find_event": "Find out when, where or what a specific existing event is",
    "create_event": "Put something new on the calendar: schedule, add, book or block off an event, birthday or reminder",
    "update_event": "Change one event that is already on the calendar: move or reschedule it, rename it, or change its length, location or notes",
    "delete_event": "Remove or cancel one event that is already on the calendar",
    "other": "Anything else: not about the calendar, unclear, or affecting several events at once (e.g. clear my afternoon)",
}

# Below this, the kind is treated as unsure and the pre-pass adds nothing
KIND_MIN_PROBABILITY = 0.6

JEV_TIMEOUT_SECONDS = 10.0


class RequestClassifierNotConfigured(RuntimeError):
    """TYPESAFE_API_KEY is not set."""


async def classify_request(text: str) -> Dict[str, Any]:
    """Return which kind of request `text` is.

    Returns a dict with:
      kind: the most likely kind (one of REQUEST_KINDS)
      sure: True when that kind's probability is at least KIND_MIN_PROBABILITY
      probability: the chosen kind's probability
      probabilities: every kind's probability, highest first
      confidence: Jev's overall confidence for the answer
      jev_ms: time spent waiting on Jev
    """
    settings = get_settings()
    if not settings.typesafe_api_key:
        raise RequestClassifierNotConfigured("TYPESAFE_API_KEY is not set")

    jev_start = time.time()
    try:
        response = await get_jev_client().system_one(
            state={"request": text},
            questions={
                "kind": Choice(
                    instructions="What is this calendar assistant request asking for?",
                    criteria=REQUEST_KINDS,
                ),
            },
            timeout=JEV_TIMEOUT_SECONDS,
        )
    except TypeSafeError:
        logger.exception("Jev request classification failed")
        raise
    jev_ms = round((time.time() - jev_start) * 1000)
    log_step("backend.request_classifier.jev", jev_ms / 1000)

    answer = response.choices["kind"]
    ranked = sorted(answer.probabilities.items(), key=lambda kv: kv[1], reverse=True)
    kind, probability = ranked[0]
    return {
        "kind": kind,
        "sure": probability >= KIND_MIN_PROBABILITY,
        "probability": round(probability, 3),
        "probabilities": {k: round(p, 3) for k, p in ranked},
        "confidence": round(answer.confidence, 3),
        "jev_ms": jev_ms,
    }
