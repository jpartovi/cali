#!/usr/bin/env python3
"""Add attendee emails from a linked Google Calendar onto matching Apple Contacts."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from core.config import get_settings  # noqa: E402
from db.session import get_service_client  # noqa: E402
from domains.calendars.providers.google import refresh_access_token  # noqa: E402
from merge_google_csv_into_apple_contacts import (  # noqa: E402
    add_email,
    display_name,
    index_apple,
    load_apple_contacts,
    name_key,
    normalize_email,
)

SKIP_EMAIL_SUFFIXES = (
    "resource.calendar.google.com",
    "group.calendar.google.com",
    "calendar.google.com",
    "googlegroups.com",
)
SKIP_CALENDAR_MARKERS = (
    "#holiday@",
    "#contacts@",
    "#group.v.calendar.google.com",
)


def split_display_name(value: str) -> tuple[str, str]:
    text = " ".join(value.split())
    if not text or "@" in text:
        return "", ""
    if "," in text:
        last, first = text.split(",", 1)
        return first.strip(), last.strip()
    parts = text.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def is_skippable_email(email: str, self_emails: set[str]) -> bool:
    if email in self_emails:
        return True
    return any(email.endswith(suffix) for suffix in SKIP_EMAIL_SUFFIXES)


def is_skippable_calendar(calendar_id: str) -> bool:
    lowered = calendar_id.lower()
    return any(marker in lowered for marker in SKIP_CALENDAR_MARKERS)


def load_account(email: str) -> dict[str, Any]:
    client = get_service_client()
    result = (
        client.table("google_accounts")
        .select("id,email,refresh_token,user_id")
        .eq("email", email)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise RuntimeError(f"No Google account found for {email}")
    account = rows[0]
    if not account.get("refresh_token"):
        raise RuntimeError(f"{email} has no refresh token. Re-link Google Calendar.")
    return account


def list_calendars(access_token: str) -> list[dict[str, Any]]:
    calendars: list[dict[str, Any]] = []
    page_token: str | None = None
    with httpx.Client(timeout=30.0) as client:
        while True:
            params: dict[str, Any] = {"maxResults": 250}
            if page_token:
                params["pageToken"] = page_token
            response = client.get(
                "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            payload = response.json()
            calendars.extend(payload.get("items") or [])
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
    return calendars


def list_events(access_token: str, calendar_id: str, time_min: datetime) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    page_token: str | None = None
    encoded = quote(calendar_id, safe="@")
    with httpx.Client(timeout=60.0) as client:
        while True:
            params: dict[str, Any] = {
                "maxResults": 2500,
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeMin": time_min.isoformat().replace("+00:00", "Z"),
                "fields": "nextPageToken,items(attendees(email,displayName,self,resource,responseStatus),organizer(email,displayName,self))",
            }
            if page_token:
                params["pageToken"] = page_token
            response = client.get(
                f"https://www.googleapis.com/calendar/v3/calendars/{encoded}/events",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code == 404:
                return events
            response.raise_for_status()
            payload = response.json()
            events.extend(payload.get("items") or [])
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
    return events


def collect_attendees(
    events: list[dict[str, Any]], self_emails: set[str]
) -> dict[str, dict[str, Any]]:
    people: dict[str, dict[str, Any]] = {}
    for event in events:
        attendees = list(event.get("attendees") or [])
        organizer = event.get("organizer") or {}
        if organizer.get("email") and not any(
            attendee.get("email") == organizer.get("email") for attendee in attendees
        ):
            attendees.append(organizer)
        for attendee in attendees:
            if not isinstance(attendee, dict) or attendee.get("resource"):
                continue
            if attendee.get("self"):
                continue
            email = normalize_email(str(attendee.get("email") or ""))
            if not email or is_skippable_email(email, self_emails):
                continue
            status = attendee.get("responseStatus")
            bucket = people.setdefault(
                email,
                {"email": email, "names": Counter(), "accepted": 0, "events": 0},
            )
            bucket["events"] += 1
            if status != "declined":
                bucket["accepted"] += 1
            name = (attendee.get("displayName") or "").strip()
            if name:
                bucket["names"][name] += 1
    return people


def match_attendee(email: str, given: str, family: str, index: dict[str, Any]) -> tuple[str | None, str]:
    existing = index["by_email"].get(email, set())
    if len(existing) == 1:
        return next(iter(existing)), "already-present"
    if len(existing) > 1:
        return None, "ambiguous-email"

    key = name_key(given, family)
    if key:
        name_ids = index["by_name"].get(key, set())
        if len(name_ids) == 1:
            return next(iter(name_ids)), "name"
        if len(name_ids) > 1:
            return None, "ambiguous-name"

    if given and family == "":
        first = given.strip().lower()
        matches = [
            person["id"]
            for person in index["by_id"].values()
            if (person.get("first") or "").strip().lower() == first
        ]
        if len(matches) == 1:
            return matches[0], "unique-first-name"
        if len(matches) > 1:
            return None, "ambiguous-first-name"
    return None, "unmatched"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default="jude@partovi.org")
    parser.add_argument("--months", type=int, default=18)
    parser.add_argument("--label", default="work")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    get_settings.cache_clear()
    account = load_account(args.account)
    tokens = asyncio.run(refresh_access_token(account["refresh_token"]))
    access_token = tokens.access_token
    self_emails = {normalize_email(args.account)}

    calendars = list_calendars(access_token)
    usable = [
        calendar
        for calendar in calendars
        if calendar.get("id")
        and is_skippable_calendar(str(calendar["id"])) is False
        and calendar.get("deleted") is not True
    ]
    time_min = datetime.now(timezone.utc) - timedelta(days=30 * args.months)
    print(f"Account {args.account}")
    print(f"Calendars: {len(usable)}  since {time_min.date().isoformat()}")

    events: list[dict[str, Any]] = []
    for calendar in usable:
        calendar_events = list_events(access_token, str(calendar["id"]), time_min)
        events.extend(calendar_events)
        print(f"  {calendar.get('summary') or calendar['id']}: {len(calendar_events)} events")

    attendees = collect_attendees(events, self_emails)
    print(f"Unique guest emails: {len(attendees)}")
    print("Reading Apple Contacts…")
    apple = load_apple_contacts()
    index = index_apple(apple)
    print(f"Apple contacts: {len(apple)}")

    planned: list[tuple[str, str, str, str, int]] = []
    skipped: dict[str, int] = defaultdict(int)
    unmatched_named: list[str] = []

    for email, payload in sorted(attendees.items(), key=lambda item: (-item[1]["accepted"], item[0])):
        if payload["accepted"] == 0:
            skipped["declined-only"] += 1
            continue
        best_name = payload["names"].most_common(1)[0][0] if payload["names"] else ""
        given, family = split_display_name(best_name)
        contact_id, reason = match_attendee(email, given, family, index)
        if reason == "already-present":
            skipped["already-present"] += 1
            continue
        if contact_id is None:
            skipped[reason] += 1
            if reason == "unmatched" and best_name:
                unmatched_named.append(f"{best_name} <{email}> ({payload['events']} events)")
            continue
        person = index["by_id"][contact_id]
        existing = {normalize_email(value) for value in person.get("emails") or []}
        if email in existing:
            skipped["already-present"] += 1
            continue
        person_name = display_name(
            person.get("first") or "",
            person.get("last") or "",
            person.get("nickname") or "",
        )
        planned.append((contact_id, person_name, email, reason, payload["events"]))

    print()
    print(f"Will add {len(planned)} email(s):")
    for _contact_id, person_name, email, reason, events_count in planned:
        print(f"  {person_name} += {email}  [{reason}, {events_count} events]")
    print()
    print("Skipped:", dict(skipped))
    if unmatched_named[:25]:
        print("Unmatched named guests (first 25):")
        for line in unmatched_named[:25]:
            print(f"  {line}")

    if args.apply is False:
        print("\nDry run. Re-run with --apply to write into Contacts.app.")
        return 0

    failures = 0
    for contact_id, person_name, email, _reason, _events_count in planned:
        try:
            add_email(contact_id, email, args.label)
            print(f"Added {email} to {person_name}")
        except RuntimeError as exc:
            failures += 1
            print(f"Failed {email} on {person_name}: {exc}", file=sys.stderr)
    print(f"Done. Added {len(planned) - failures}, failed {failures}.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
