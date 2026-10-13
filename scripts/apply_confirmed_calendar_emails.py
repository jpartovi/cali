#!/usr/bin/env python3
"""Apply confirmed calendar emails onto Apple Contacts, then list last-month misses."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from core.config import get_settings  # noqa: E402
from domains.calendars.providers.google import refresh_access_token  # noqa: E402
from fill_apple_contacts_from_calendar import (  # noqa: E402
    collect_attendees,
    is_skippable_calendar,
    list_calendars,
    list_events,
    load_account,
    match_attendee,
    split_display_name,
)
from merge_google_csv_into_apple_contacts import (  # noqa: E402
    add_email,
    display_name,
    index_apple,
    load_apple_contacts,
    normalize_email,
)

CONFIRMED: list[tuple[str, str]] = [
    ("Myles Nelson", "510mylesn@gmail.com"),
    ("Reza Partovi", "reza@partovi.org"),
    ("Soli Partovi", "soli@partovi.org"),
    ("Marcus Lee", "marcus_lee@college.harvard.edu"),
    ("Jonathan Lei", "jonathanlei@college.harvard.edu"),
    ("Melissa Partovi", "melissa@partovi.org"),
    ("Darius Partovi", "dariuspartovi@gmail.com"),
    ("Alisona Le", "alisonale@college.harvard.edu"),
    ("Anika Somaia", "anika.somaia@columbia.edu"),
    ("Beamlak Birhane", "beamlak_birhane@college.harvard.edu"),
    ("Hadi Partovi", "hadipartovi@code.org"),
    ("Robert O'Shaughnessy", "robert.os@outlook.com"),
    ("Eli Olcott", "eliolcott@college.harvard.edu"),
    ("Sophia Rascoff", "sophiamrascoff@gmail.com"),
    ("Prisha Sheth", "prishasheth@college.harvard.edu"),
    ("Andrew Milich", "milichab@gmail.com"),
    ("Kathryn Harper", "kharper@college.harvard.edu"),
    ("Haleh Partovi", "halehpartovi@gmail.com"),
    ("Sophia Khan", "sophiakhan@college.harvard.edu"),
    ("Lavik Jain", "lavikjain@college.harvard.edu"),
    ("Darius Partovi", "darius@partovi.org"),
    ("Ben Davis", "budavis@college.harvard.edu"),
    ("Eli Olcott", "eliolcott@gmail.com"),
    ("Kitty Wang", "lin.kitty.wang@gmail.com"),
    ("Lucas Chu", "lucaschu@college.harvard.edu"),
    ("Dashiell Bhattacharyya", "dashiellb100@gmail.com"),
    ("Pranav Ravella", "pranav@sapien.team"),
    ("Sidd Tiwari", "stiwari@college.harvard.edu"),
    ("Ryan Lian", "ryanlian@stanford.edu"),
    ("Dad", "ali@neo.com"),
    ("Dad", "ali@partovi.org"),
    ("Dad", "apartovi@gmail.com"),
    ("Lulu", "ginatega@gmail.com"),
]


def find_contact(people: list[dict[str, Any]], query: str) -> dict[str, Any]:
    needle = query.strip().lower()
    matches: list[dict[str, Any]] = []
    for person in people:
        first = (person.get("first") or "").strip()
        last = (person.get("last") or "").strip()
        nick = (person.get("nickname") or "").strip()
        labels = {
            display_name(first, last, nick).lower(),
            nick.lower(),
            first.lower(),
            f"{first} {last}".strip().lower(),
        }
        if needle in labels:
            matches.append(person)
    unique = {person["id"]: person for person in matches}
    if len(unique) != 1:
        names = [
            display_name(p.get("first") or "", p.get("last") or "", p.get("nickname") or "")
            for p in unique.values()
        ]
        raise RuntimeError(f"{query!r} matched {len(unique)} contacts: {names}")
    return next(iter(unique.values()))


def given_family_for_match(display: str) -> tuple[str, str]:
    given, family = split_display_name(display)
    if given and family and " " in given:
        given = given.split()[0]
    return given, family


def main() -> int:
    print("Reading Apple Contacts…", flush=True)
    apple = load_apple_contacts()
    print(f"Apple contacts: {len(apple)}", flush=True)

    failures = 0
    added = 0
    skipped_present = 0
    for query, email in CONFIRMED:
        email = normalize_email(email)
        try:
            person = find_contact(apple, query)
        except RuntimeError as exc:
            print(f"LOOKUP FAIL {query}: {exc}")
            failures += 1
            continue
        existing = {normalize_email(value) for value in person.get("emails") or []}
        person_name = display_name(
            person.get("first") or "",
            person.get("last") or "",
            person.get("nickname") or "",
        )
        if email in existing:
            print(f"Already present: {person_name} has {email}")
            skipped_present += 1
            continue
        try:
            add_email(person["id"], email, "work")
            person["emails"] = list(person.get("emails") or []) + [email]
            print(f"Added {email} to {person_name} ({query})")
            added += 1
        except RuntimeError as exc:
            print(f"WRITE FAIL {email} on {person_name}: {exc}")
            failures += 1

    print(f"\nApple write: added {added}, already present {skipped_present}, failed {failures}", flush=True)

    get_settings.cache_clear()
    account = load_account("jude@partovi.org")
    tokens = asyncio.run(refresh_access_token(account["refresh_token"]))
    access_token = tokens.access_token
    time_min = datetime.now(timezone.utc) - timedelta(days=31)
    calendars = [
        calendar
        for calendar in list_calendars(access_token)
        if calendar.get("id")
        and is_skippable_calendar(str(calendar["id"])) is False
        and calendar.get("deleted") is not True
    ]
    events: list[dict[str, Any]] = []
    for calendar in calendars:
        events.extend(list_events(access_token, str(calendar["id"]), time_min))
    attendees = collect_attendees(events, {normalize_email("jude@partovi.org")})
    index = index_apple(apple)

    print(f"\nLast 31 days: {len(events)} events, {len(attendees)} unique guests", flush=True)
    print("Would NOT be added (named guests only):\n")
    misses = 0
    for email, payload in sorted(attendees.items(), key=lambda item: (-item[1]["events"], item[0])):
        if payload["accepted"] == 0:
            continue
        best_name = payload["names"].most_common(1)[0][0] if payload["names"] else ""
        given, family = given_family_for_match(best_name)
        contact_id, reason = match_attendee(email, given, family, index)
        if reason == "already-present" or contact_id is not None:
            continue
        if not best_name:
            continue
        misses += 1
        print(f"  {best_name}  {email}  ({payload['events']} events, {reason})")
    print(f"\nNamed misses last month: {misses}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
