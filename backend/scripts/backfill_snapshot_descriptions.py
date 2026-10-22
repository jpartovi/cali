"""Fill calendar_event_snapshots.description for rows stored before the column existed.

The listener skips events whose etag hasn't changed, so existing rows keep a null
description until the event is edited. Run this once after migration 0022: it lists
each calendar's stored span from Google and writes only the description column.

    cd backend && .venv/bin/python scripts/backfill_snapshot_descriptions.py [--user-id ID] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.session import get_service_client  # noqa: E402
from domains.calendar_listener.service import CalendarListenerService  # noqa: E402
from domains.calendar_listener.snapshots import snapshot_from_google_event  # noqa: E402
from domains.calendars.providers.google import GoogleCalendarHttpClient  # noqa: E402
from domains.calendars.repository import CalendarRepository  # noqa: E402

PAGE = 1000


def _stored_rows(user_id: str | None) -> List[Dict[str, Any]]:
    db = get_service_client()
    rows: List[Dict[str, Any]] = []
    while True:
        query = (
            db.table("calendar_event_snapshots")
            .select("id,user_id,google_account_id,google_calendar_id,google_event_id,description,start_at,end_at")
            .neq("status", "cancelled")
            .order("id")
            .range(len(rows), len(rows) + PAGE - 1)
        )
        if user_id:
            query = query.eq("user_id", user_id)
        page = query.execute().data or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows


async def backfill(user_id: str | None, dry_run: bool) -> None:
    listener = CalendarListenerService()
    calendar_repo = CalendarRepository()
    db = get_service_client()
    groups: Dict[tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in _stored_rows(user_id):
        if row.get("start_at") and row.get("end_at"):
            groups[(row["user_id"], row["google_account_id"], row["google_calendar_id"])].append(row)
    tokens: Dict[str, str] = {}

    async with GoogleCalendarHttpClient() as client:
        for (owner, account_id, calendar_id), rows in groups.items():
            account = calendar_repo.get_account_by_id(owner, account_id)
            if not account:
                continue
            if account_id not in tokens:
                tokens[account_id] = await listener._token_for_account(account)
            time_min = min(row["start_at"] for row in rows)
            time_max = max(row["end_at"] for row in rows)
            try:
                events = await client.list_events(
                    access_token=tokens[account_id], calendar_id=calendar_id, time_min=time_min, time_max=time_max
                )
            except Exception as exc:
                print(f"user={owner[:8]} calendar={calendar_id[:40]} listing failed: {exc}")
                continue
            descriptions = {}
            for event in events:
                snapshot = snapshot_from_google_event(
                    user_id=owner, google_account_id=account_id, google_calendar_id=calendar_id, event=event
                )
                if snapshot:
                    descriptions[snapshot["google_event_id"]] = snapshot["description"]
            changed = [
                row for row in rows
                if row["google_event_id"] in descriptions and descriptions[row["google_event_id"]] != row.get("description")
            ]
            print(f"user={owner[:8]} calendar={calendar_id[:40]} rows={len(rows)} listed={len(events)} updates={len(changed)}")
            if dry_run:
                continue
            for row in changed:
                db.table("calendar_event_snapshots").update(
                    {"description": descriptions[row["google_event_id"]]}
                ).eq("id", row["id"]).execute()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", help="Only this user (default: every user with snapshots)")
    parser.add_argument("--dry-run", action="store_true", help="Count what would be written without writing")
    args = parser.parse_args()
    asyncio.run(backfill(args.user_id, args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
