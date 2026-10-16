"""Apple contact import and list."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from domains.contacts.repository import ContactsRepository
from domains.contacts.schemas import (
    AppleContactImport,
    AppleImportResponse,
    ContactResponse,
)
from utils.errors import SupabaseStorageError


def compute_display_name(
    given_name: str | None,
    family_name: str | None,
    nickname: str | None,
) -> str:
    parts = " ".join(part.strip() for part in (given_name, family_name) if part and part.strip())
    if parts:
        return parts
    if nickname and nickname.strip():
        return nickname.strip()
    return "Unknown"


def resolve_invite_email(
    emails: Sequence[str],
    existing: str | None,
    event_counts: Dict[str, int] | None = None,
) -> str | None:
    """The email to invite this contact at.

    Keeps the current choice while it's still on the card; otherwise the address the user
    has been on the most calendar events with; otherwise the card's first email.
    """
    unique = list(emails)
    if not unique:
        return None
    if len(unique) == 1:
        return unique[0]
    existing_normalized = (existing or "").strip().lower() or None
    if existing_normalized and existing_normalized in unique:
        return existing_normalized
    counts = event_counts or {}
    # max() keeps the first of equal counts, so with no history this is the card's first email
    return max(unique, key=lambda email: counts.get(email, 0))


_UPDATE_KEYS = (
    "given_name",
    "family_name",
    "nickname",
    "display_name",
    "invite_email",
    "emails",
    "apple_identifier",
)


def _row_needs_update(existing: Dict[str, Any], patch: Dict[str, Any]) -> bool:
    return any(existing.get(key) != patch.get(key) for key in _UPDATE_KEYS)


class ContactsService:
    """Snapshot Apple Contacts and remember invite_email."""

    def __init__(self, repository: ContactsRepository | None = None) -> None:
        self.repository = repository or ContactsRepository()

    def import_apple(
        self,
        user_id: str,
        contacts: List[AppleContactImport],
        *,
        replace: bool = False,
        kept_identifiers: Sequence[str] | None = None,
    ) -> AppleImportResponse:
        imported = 0
        updated = 0
        apple_ids = [item.apple_identifier for item in contacts]
        by_apple = {
            row["apple_identifier"]: row
            for row in self.repository.get_by_apple_identifiers(user_id, apple_ids)
            if row.get("apple_identifier")
        }
        # Calendar history only matters for cards with several emails and no remembered choice
        event_counts = self.repository.event_counts_by_email(
            user_id,
            [email for item in contacts if len(item.emails) > 1 for email in item.emails],
        )
        now = self.repository.utc_now_iso()
        new_payloads: List[Dict[str, Any]] = []
        upsert_rows: List[Dict[str, Any]] = []

        for item in contacts:
            display_name = compute_display_name(item.given_name, item.family_name, item.nickname)
            existing = by_apple.get(item.apple_identifier)
            invite_email = resolve_invite_email(
                item.emails,
                existing.get("invite_email") if existing else None,
                event_counts,
            )
            payload = {
                "user_id": user_id,
                "apple_identifier": item.apple_identifier,
                "given_name": item.given_name,
                "family_name": item.family_name,
                "nickname": item.nickname,
                "display_name": display_name,
                "invite_email": invite_email,
                "emails": item.emails,
                "last_imported_at": now,
            }
            if existing is None:
                new_payloads.append(payload)
                imported += 1
                continue
            if _row_needs_update(existing, payload):
                upsert_rows.append({"id": existing["id"], **payload})
                updated += 1

        if new_payloads:
            self.repository.insert_contacts(new_payloads)
        if upsert_rows:
            self.repository.upsert_contacts(upsert_rows)

        deleted = 0
        if replace:
            keep = {
                identifier
                for identifier in (list(kept_identifiers) if kept_identifiers else apple_ids)
                if identifier
            }
            existing_ids = self.repository.list_apple_identifiers(user_id)
            stale = [identifier for identifier in existing_ids if identifier not in keep]
            deleted = self.repository.delete_by_apple_identifiers(user_id, stale)

        return AppleImportResponse(imported=imported, updated=updated, deleted=deleted)

    def add_email(self, user_id: str, contact_id: str, email: str) -> ContactResponse | None:
        """Adds an email to a contact (the app also writes it to the Apple card) and invites them
        at it when they had no invite email. None when the contact isn't the user's."""
        existing = self.repository.get_contact(user_id, contact_id)
        if existing is None:
            return None
        emails = list(existing.get("emails") or [])
        if email not in emails:
            emails.append(email)
        row = self.repository.update_contact(
            user_id,
            contact_id,
            {"emails": emails, "invite_email": existing.get("invite_email") or email},
        )
        return self._to_response(row)

    def list_contacts(self, user_id: str) -> List[ContactResponse]:
        return [self._to_response(row) for row in self.repository.list_contacts(user_id)]

    @staticmethod
    def _to_response(row: Dict[str, Any]) -> ContactResponse:
        apple_identifier = row.get("apple_identifier")
        if not apple_identifier:
            raise SupabaseStorageError("Contact is missing apple_identifier.")
        return ContactResponse(
            id=row["id"],
            user_id=row["user_id"],
            apple_identifier=apple_identifier,
            given_name=row.get("given_name"),
            family_name=row.get("family_name"),
            nickname=row.get("nickname"),
            display_name=row["display_name"],
            invite_email=row.get("invite_email"),
            emails=row.get("emails") or [],
            last_imported_at=row.get("last_imported_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
