"""Parse Google Calendar events into compact snapshot rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo


HORIZON_PAST = timedelta(days=1)
HORIZON_FUTURE = timedelta(days=14)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_event_bounds(event: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[datetime], bool, Optional[str]]:
    start_payload = event.get("start") or {}
    end_payload = event.get("end") or {}
    if not isinstance(start_payload, dict) or not isinstance(end_payload, dict):
        return None, None, False, None
    tz_name = start_payload.get("timeZone") or end_payload.get("timeZone")
    if not isinstance(tz_name, str) or not tz_name:
        tz_name = None

    if start_payload.get("dateTime") or end_payload.get("dateTime"):
        start_at = _parse_datetime(start_payload.get("dateTime"))
        end_at = _parse_datetime(end_payload.get("dateTime"))
        return start_at, end_at, False, tz_name

    start_date = start_payload.get("date")
    end_date = end_payload.get("date")
    if isinstance(start_date, str) and start_date:
        try:
            tz = ZoneInfo(tz_name) if tz_name else timezone.utc
        except Exception:
            tz = timezone.utc
        start_at = datetime.fromisoformat(start_date).replace(tzinfo=tz).astimezone(timezone.utc)
        if isinstance(end_date, str) and end_date:
            end_at = datetime.fromisoformat(end_date).replace(tzinfo=tz).astimezone(timezone.utc)
        else:
            end_at = start_at + timedelta(days=1)
        return start_at, end_at, True, tz_name
    return None, None, False, tz_name


def _attendee_rows(event: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
    attendees_raw = event.get("attendees") or []
    rows: List[Dict[str, Any]] = []
    self_response: Optional[str] = None
    organizer_self = False
    if isinstance(attendees_raw, list):
        for attendee in attendees_raw:
            if not isinstance(attendee, dict):
                continue
            email = attendee.get("email")
            if not isinstance(email, str) or not email:
                continue
            is_self = bool(attendee.get("self"))
            response = attendee.get("responseStatus")
            row = {
                "email": email,
                "responseStatus": response if isinstance(response, str) else None,
                "self": is_self,
            }
            rows.append(row)
            if is_self and isinstance(response, str):
                self_response = response
    organizer = event.get("organizer") or {}
    if isinstance(organizer, dict):
        organizer_self = bool(organizer.get("self"))
        if self_response is None and organizer_self:
            self_response = "accepted"
    return rows, self_response, organizer_self


def snapshot_from_google_event(
    *,
    user_id: str,
    google_account_id: str,
    google_calendar_id: str,
    event: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id:
        return None
    status = event.get("status") or "confirmed"
    if status not in {"confirmed", "tentative", "cancelled"}:
        status = "confirmed"
    start_at, end_at, is_all_day, tz_name = _parse_event_bounds(event)
    attendees, self_response, organizer_self = _attendee_rows(event)
    organizer = event.get("organizer") or {}
    organizer_email = organizer.get("email") if isinstance(organizer, dict) else None
    title = event.get("summary")
    location = event.get("location")
    etag = event.get("etag")
    ical_uid = event.get("iCalUID")
    recurring_event_id = event.get("recurringEventId")
    updated = _parse_datetime(event.get("updated"))
    return {
        "user_id": user_id,
        "google_account_id": google_account_id,
        "google_calendar_id": google_calendar_id,
        "google_event_id": event_id,
        "ical_uid": ical_uid if isinstance(ical_uid, str) else None,
        "etag": etag if isinstance(etag, str) else None,
        "status": status,
        "google_updated_at": updated.isoformat() if updated else None,
        "recurring_event_id": recurring_event_id if isinstance(recurring_event_id, str) else None,
        "title": title.strip() if isinstance(title, str) else None,
        "location": location if isinstance(location, str) else None,
        "start_at": start_at.isoformat() if start_at else None,
        "end_at": end_at.isoformat() if end_at else None,
        "is_all_day": is_all_day,
        "timezone": tz_name,
        "organizer_email": organizer_email if isinstance(organizer_email, str) else None,
        "organizer_self": organizer_self,
        "self_response": self_response,
        "attendees": attendees,
    }


def is_recurring_master(event: Dict[str, Any]) -> bool:
    recurrence = event.get("recurrence")
    return isinstance(recurrence, list) and len(recurrence) > 0 and not event.get("recurringEventId")


def in_horizon(snapshot: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    start_at = _parse_datetime(snapshot.get("start_at"))
    end_at = _parse_datetime(snapshot.get("end_at"))
    if snapshot.get("status") == "cancelled":
        updated = _parse_datetime(snapshot.get("google_updated_at")) or now
        return updated >= now - HORIZON_PAST
    if end_at and end_at < now - HORIZON_PAST:
        return False
    if start_at and start_at > now + HORIZON_FUTURE:
        return True
    return True


def parse_iso(value: Any) -> Optional[datetime]:
    return _parse_datetime(value)
