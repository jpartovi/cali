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


def resolve_invite_email(emails: Sequence[str], existing: str | None) -> str | None:
    unique = list(emails)
    if len(unique) == 1:
        return unique[0]
    existing_normalized = (existing or "").strip().lower() or None
    if existing_normalized and existing_normalized in unique:
        return existing_normalized
    return None


_UPDATE_KEYS = (
    "given_name",
    "family_name",
    "nickname",
    "display_name",
    "invite_email",
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
        now = self.repository.utc_now_iso()
        new_payloads: List[Dict[str, Any]] = []
        upsert_rows: List[Dict[str, Any]] = []

        for item in contacts:
            display_name = compute_display_name(item.given_name, item.family_name, item.nickname)
            existing = by_apple.get(item.apple_identifier)
            invite_email = resolve_invite_email(
                item.emails,
                existing.get("invite_email") if existing else None,
            )
            payload = {
                "user_id": user_id,
                "apple_identifier": item.apple_identifier,
                "given_name": item.given_name,
                "family_name": item.family_name,
                "nickname": item.nickname,
                "display_name": display_name,
                "invite_email": invite_email,
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
            last_imported_at=row.get("last_imported_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
