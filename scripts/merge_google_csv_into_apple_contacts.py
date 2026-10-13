#!/usr/bin/env python3
"""One-time: add Google-exported emails onto matching macOS Apple Contacts.

Does not create people. Dry-run unless you pass --apply.

  1. On contacts.google.com (college account): Export Contacts and Other contacts
     as Google CSV.
  2. python3 scripts/merge_google_csv_into_apple_contacts.py export.csv --domains your.edu
  3. Review the plan, then add --apply.
  4. Confirm Contacts are syncing with iCloud so the phone picks them up.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

DUMP_CONTACTS_JS = r"""
const Contacts = Application("Contacts");
const people = Contacts.people();
const out = [];
for (const person of people) {
  const emails = [];
  const phones = [];
  try {
    for (const email of person.emails()) {
      emails.push(String(email.value() || ""));
    }
  } catch (e) {}
  try {
    for (const phone of person.phones()) {
      phones.push(String(phone.value() || ""));
    }
  } catch (e) {}
  out.push({
    id: String(person.id()),
    first: String(person.firstName() || ""),
    last: String(person.lastName() || ""),
    nickname: String(person.nickname() || ""),
    emails,
    phones,
  });
}
JSON.stringify(out);
"""


def normalize_email(value: str) -> str:
    return value.strip().lower()


def phone_key(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) < 10:
        return None
    return digits


def display_name(first: str, last: str, nickname: str) -> str:
    parts = " ".join(part.strip() for part in (first, last) if part.strip())
    if parts:
        return parts
    return nickname.strip()


def name_key(first: str, last: str) -> str | None:
    first_n = re.sub(r"\s+", " ", first).strip().lower()
    last_n = re.sub(r"\s+", " ", last).strip().lower()
    if not first_n or not last_n:
        return None
    return f"{first_n}|{last_n}"


def run_jxa(source: str) -> str:
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", source],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Contacts AppleScript failed.")
    return result.stdout.strip()


def load_apple_contacts() -> list[dict[str, Any]]:
    raw = run_jxa(DUMP_CONTACTS_JS)
    people = json.loads(raw)
    if not isinstance(people, list):
        raise RuntimeError("Contacts dump was not a list.")
    return people


def parse_google_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            emails: list[str] = []
            phones: list[str] = []
            for key, value in raw.items():
                if not value or not key:
                    continue
                lowered = key.lower()
                if "e-mail" in lowered and "value" in lowered:
                    emails.append(normalize_email(value))
                elif lowered.startswith("email") and "type" not in lowered:
                    emails.append(normalize_email(value))
                elif "phone" in lowered and "value" in lowered:
                    phones.append(value.strip())
            given = (raw.get("Given Name") or "").strip()
            family = (raw.get("Family Name") or "").strip()
            name = (raw.get("Name") or "").strip()
            if not given and not family and name and "@" not in name:
                parts = name.split()
                if len(parts) >= 2:
                    given, family = parts[0], parts[-1]
            emails = [email for email in emails if "@" in email]
            if not emails:
                continue
            rows.append(
                {
                    "given": given,
                    "family": family,
                    "name": name,
                    "emails": emails,
                    "phones": phones,
                }
            )
    return rows


def index_apple(people: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {person["id"]: person for person in people}
    by_email: dict[str, set[str]] = defaultdict(set)
    by_phone: dict[str, set[str]] = defaultdict(set)
    by_name: dict[str, set[str]] = defaultdict(set)
    for person in people:
        for email in person.get("emails") or []:
            normalized = normalize_email(email)
            if normalized:
                by_email[normalized].add(person["id"])
        for phone in person.get("phones") or []:
            key = phone_key(phone)
            if key:
                by_phone[key].add(person["id"])
        key = name_key(person.get("first") or "", person.get("last") or "")
        if key:
            by_name[key].add(person["id"])
    return {
        "by_id": by_id,
        "by_email": by_email,
        "by_phone": by_phone,
        "by_name": by_name,
    }


def match_person(row: dict[str, Any], index: dict[str, Any]) -> tuple[str | None, str]:
    ids: set[str] = set()
    for email in row["emails"]:
        ids.update(index["by_email"].get(email, set()))
    if len(ids) == 1:
        return next(iter(ids)), "email"
    if len(ids) > 1:
        return None, "ambiguous-email"

    phone_ids: set[str] = set()
    for phone in row["phones"]:
        key = phone_key(phone)
        if key:
            phone_ids.update(index["by_phone"].get(key, set()))
    if len(phone_ids) == 1:
        return next(iter(phone_ids)), "phone"
    if len(phone_ids) > 1:
        return None, "ambiguous-phone"

    key = name_key(row["given"], row["family"])
    if key:
        name_ids = index["by_name"].get(key, set())
        if len(name_ids) == 1:
            return next(iter(name_ids)), "name"
        if len(name_ids) > 1:
            return None, "ambiguous-name"
    return None, "unmatched"


def add_email(contact_id: str, email: str, label: str) -> None:
    escaped_email = email.replace("\\", "\\\\").replace('"', '\\"')
    escaped_id = contact_id.replace("\\", "\\\\").replace('"', '\\"')
    escaped_label = label.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Contacts"
  set thePerson to person id "{escaped_id}"
  make new email at end of emails of thePerson with properties {{label:"{escaped_label}", value:"{escaped_email}"}}
  save
end tell
'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Failed to add {email}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_paths", nargs="+", type=Path, help="Google CSV export files")
    parser.add_argument(
        "--domains",
        required=True,
        help="Comma-separated email domains to add, e.g. princeton.edu,alumni.princeton.edu",
    )
    parser.add_argument("--label", default="school", help="Apple Contacts email label")
    parser.add_argument("--apply", action="store_true", help="Write to Contacts.app (otherwise dry-run)")
    args = parser.parse_args()

    domains = {item.strip().lower().lstrip("@") for item in args.domains.split(",") if item.strip()}
    if not domains:
        print("Pass at least one --domains value.", file=sys.stderr)
        return 1

    google_rows: list[dict[str, Any]] = []
    for path in args.csv_paths:
        if path.exists() is False:
            print(f"Missing file: {path}", file=sys.stderr)
            return 1
        google_rows.extend(parse_google_csv(path))

    print("Reading Apple Contacts…")
    apple = load_apple_contacts()
    index = index_apple(apple)
    print(f"Apple contacts: {len(apple)}")
    print(f"Google rows with emails: {len(google_rows)}")

    planned: list[tuple[str, str, str, str]] = []
    skipped: dict[str, int] = defaultdict(int)
    seen: set[tuple[str, str]] = set()

    for row in google_rows:
        college_emails = [
            email
            for email in row["emails"]
            if email.split("@", 1)[-1].lower() in domains
        ]
        if not college_emails:
            skipped["no-college-email"] += 1
            continue
        contact_id, reason = match_person(row, index)
        if contact_id is None:
            skipped[reason] += 1
            if reason == "unmatched":
                print(
                    f"  unmatched: {row['name'] or (row['given'] + ' ' + row['family']).strip()} "
                    f"({', '.join(college_emails)})"
                )
            continue
        person = index["by_id"][contact_id]
        existing = {normalize_email(email) for email in person.get("emails") or []}
        for email in college_emails:
            if email in existing:
                skipped["already-present"] += 1
                continue
            key = (contact_id, email)
            if key in seen:
                continue
            seen.add(key)
            person_name = display_name(person.get("first") or "", person.get("last") or "", person.get("nickname") or "")
            planned.append((contact_id, person_name, email, reason))

    print()
    print(f"Would add {len(planned)} email(s):")
    for _contact_id, person_name, email, reason in planned:
        print(f"  {person_name} += {email}  [{reason}]")
    print()
    print("Skipped:", dict(skipped))

    if args.apply is False:
        print("\nDry run. Re-run with --apply to write these into Contacts.app.")
        return 0

    failures = 0
    for contact_id, person_name, email, _reason in planned:
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
