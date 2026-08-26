"""Classify snapshot diffs into internal calendar change events."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional


CHANGE_CREATED = "created"
CHANGE_UPDATED = "updated"
CHANGE_CANCELLED = "cancelled"
CHANGE_ATTENDEES = "attendees_changed"
CHANGE_EVENT_START = "event_start"
CHANGE_EVENT_END = "event_end"


def _normalize_attendees(raw: Any) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not isinstance(raw, list):
        return result
    for row in raw:
        if not isinstance(row, dict):
            continue
        email = row.get("email")
        if not isinstance(email, str) or not email:
            continue
        if row.get("self"):
            continue
        status = row.get("responseStatus")
        result[email.lower()] = status if isinstance(status, str) else ""
    return result


def classify_snapshot_change(
    previous: Optional[Dict[str, Any]],
    current: Dict[str, Any],
) -> List[str]:
    """Return change kinds for a snapshot upsert."""
    status = current.get("status")
    if previous is None:
        if status == "cancelled":
            return [CHANGE_CANCELLED]
        return [CHANGE_CREATED]

    kinds: List[str] = []
    if status == "cancelled" and previous.get("status") != "cancelled":
        kinds.append(CHANGE_CANCELLED)
        return kinds

    if status == "cancelled":
        return []

    material_fields = ("title", "start_at", "end_at", "location")
    if any(previous.get(field) != current.get(field) for field in material_fields):
        kinds.append(CHANGE_UPDATED)

    prev_attendees = _normalize_attendees(previous.get("attendees"))
    next_attendees = _normalize_attendees(current.get("attendees"))
    if prev_attendees != next_attendees:
        kinds.append(CHANGE_ATTENDEES)
    return kinds


def change_fingerprint(kind: str, snapshot: Dict[str, Any]) -> str:
    if kind == CHANGE_UPDATED:
        raw = "|".join(
            [
                kind,
                str(snapshot.get("etag") or ""),
                str(snapshot.get("title") or ""),
                str(snapshot.get("start_at") or ""),
                str(snapshot.get("end_at") or ""),
                str(snapshot.get("location") or ""),
            ]
        )
    elif kind == CHANGE_ATTENDEES:
        attendees = _normalize_attendees(snapshot.get("attendees"))
        attendee_blob = ",".join(f"{email}:{status}" for email, status in sorted(attendees.items()))
        raw = f"{kind}|{snapshot.get('etag') or ''}|{attendee_blob}"
    else:
        raw = f"{kind}|{snapshot.get('etag') or ''}|{snapshot.get('google_updated_at') or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
