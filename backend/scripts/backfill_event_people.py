"""Fill calendar_event_people from Google for past events.

The trigger on calendar_event_snapshots only sees the listener's window (yesterday to
two weeks out), so run this once per user to give the people pre-pass a history of who
they've been on events with and invited.

    cd backend && .venv/bin/python scripts/backfill_event_people.py [--user-id ID] [--days 180] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.session import get_service_client  # noqa: E402
from domains.calendar_listener.repository import CalendarListenerRepository  # noqa: E402
from domains.calendar_listener.service import CalendarListenerService  # noqa: E402
from domains.calendar_listener.snapshots import HORIZON_FUTURE, snapshot_from_google_event  # noqa: E402
from domains.calendars.providers.google import GoogleCalendarHttpClient  # noqa: E402
from domains.calendars.repository import CalendarRepository  # noqa: E402

# Same filter as public.event_people_from_attendees: no rooms or group calendars
SKIP_EMAIL_SUFFIXES = ("@resource.calendar.google.com", "@group.calendar.google.com", "@group.v.calendar.google.com")
CHUNK = 500


def _people_rows(user_id: str, google_calendar_id: str, event: Dict[str, Any], account_id: str) -> List[Dict[str, Any]]:
    snapshot = snapshot_from_google_event(
        user_id=user_id, google_account_id=account_id, google_calendar_id=google_calendar_id, event=event
    )
    if not snapshot or snapshot["status"] == "cancelled":
        return []
    rows: Dict[str, Dict[str, Any]] = {}
    for attendee in snapshot["attendees"]:
        email = attendee["email"].lower()
        if attendee.get("self") or email.endswith(SKIP_EMAIL_SUFFIXES):
            continue
        rows[email] = {
            "user_id": user_id,
            "google_calendar_id": google_calendar_id,
            "google_event_id": snapshot["google_event_id"],
            "email": email,
            "display_name": attendee.get("displayName"),
            "start_at": snapshot["start_at"],
            "organizer_self": snapshot["organizer_self"],
        }
    return list(rows.values())


async def backfill(user_id: str | None, days: int, dry_run: bool) -> None:
    listener = CalendarListenerService()
    calendar_repo = CalendarRepository()
    watches = CalendarListenerRepository().list_watches(user_id=user_id, kind="events")
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    time_max = (now + HORIZON_FUTURE).isoformat().replace("+00:00", "Z")
    db = get_service_client()
    tokens: Dict[str, str] = {}

    async with GoogleCalendarHttpClient() as client:
        for watch in watches:
            calendar_id = watch.get("google_calendar_id")
            account = calendar_repo.get_account_by_id(watch["user_id"], watch["google_account_id"])
            if not calendar_id or not account:
                continue
            if account["id"] not in tokens:
                tokens[account["id"]] = await listener._token_for_account(account)
            events = await client.list_events(
                access_token=tokens[account["id"]], calendar_id=calendar_id, time_min=time_min, time_max=time_max
            )
            rows = [row for event in events for row in _people_rows(watch["user_id"], calendar_id, event, account["id"])]
            people = len({row["email"] for row in rows})
            print(f"user={watch['user_id'][:8]} calendar={calendar_id[:40]} events={len(events)} rows={len(rows)} people={people}")
            if dry_run:
                continue
            for i in range(0, len(rows), CHUNK):
                db.table("calendar_event_people").upsert(
                    rows[i : i + CHUNK], on_conflict="user_id,google_calendar_id,google_event_id,email"
                ).execute()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", help="Only this user (default: every user with calendar watches)")
    parser.add_argument("--days", type=int, default=180, help="How far back to read (default 180)")
    parser.add_argument("--dry-run", action="store_true", help="Count what would be written without writing")
    args = parser.parse_args()
    asyncio.run(backfill(args.user_id, args.days, args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
