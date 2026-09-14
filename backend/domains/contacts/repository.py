"""Repository for contact tables."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

from postgrest import APIError

from db.session import get_service_client
from utils.errors import SupabaseStorageError

_IN_CHUNK = 100


def _without_none(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def _chunks(values: Sequence[str], size: int = _IN_CHUNK) -> Iterable[Sequence[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _row_chunks(rows: Sequence[Dict[str, Any]], size: int = _IN_CHUNK) -> Iterable[Sequence[Dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


class ContactsRepository:
    """Database access for contacts, emails, and phones."""

    def get_by_ids(self, user_id: str, contact_ids: Sequence[str]) -> List[Dict[str, Any]]:
        if not contact_ids:
            return []
        client = get_service_client()
        rows: List[Dict[str, Any]] = []
        try:
            for chunk in _chunks(list(dict.fromkeys(contact_ids))):
                result = (
                    client.table("contacts")
                    .select("*")
                    .eq("user_id", user_id)
                    .in_("id", list(chunk))
                    .execute()
                )
                rows.extend(result.data or [])
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return rows

    def get_by_apple_identifiers(
        self, user_id: str, identifiers: Sequence[str]
    ) -> List[Dict[str, Any]]:
        if not identifiers:
            return []
        client = get_service_client()
        rows: List[Dict[str, Any]] = []
        try:
            for chunk in _chunks(list(dict.fromkeys(identifiers))):
                result = (
                    client.table("contacts")
                    .select("*")
                    .eq("user_id", user_id)
                    .in_("apple_identifier", list(chunk))
                    .execute()
                )
                rows.extend(result.data or [])
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return rows

    def get_by_google_resource_names(
        self, user_id: str, resource_names: Sequence[str]
    ) -> List[Dict[str, Any]]:
        if not resource_names:
            return []
        client = get_service_client()
        rows: List[Dict[str, Any]] = []
        try:
            for chunk in _chunks(list(dict.fromkeys(resource_names))):
                result = (
                    client.table("contacts")
                    .select("*")
                    .eq("user_id", user_id)
                    .in_("google_resource_name", list(chunk))
                    .execute()
                )
                rows.extend(result.data or [])
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return rows

    def get_emails(self, user_id: str, emails: Sequence[str]) -> List[Dict[str, Any]]:
        if not emails:
            return []
        normalized = [email.lower() for email in emails if email]
        client = get_service_client()
        rows: List[Dict[str, Any]] = []
        try:
            for chunk in _chunks(list(dict.fromkeys(normalized))):
                result = (
                    client.table("contact_emails")
                    .select("*")
                    .eq("user_id", user_id)
                    .in_("email", list(chunk))
                    .execute()
                )
                rows.extend(result.data or [])
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return rows

    def get_emails_for_contacts(
        self, user_id: str, contact_ids: Sequence[str]
    ) -> List[Dict[str, Any]]:
        if not contact_ids:
            return []
        client = get_service_client()
        rows: List[Dict[str, Any]] = []
        try:
            for chunk in _chunks(list(dict.fromkeys(contact_ids))):
                result = (
                    client.table("contact_emails")
                    .select("*")
                    .eq("user_id", user_id)
                    .in_("contact_id", list(chunk))
                    .execute()
                )
                rows.extend(result.data or [])
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return rows

    def get_phones_by_e164(self, user_id: str, e164s: Sequence[str]) -> List[Dict[str, Any]]:
        if not e164s:
            return []
        client = get_service_client()
        rows: List[Dict[str, Any]] = []
        try:
            for chunk in _chunks(list(dict.fromkeys(e164s))):
                result = (
                    client.table("contact_phones")
                    .select("*")
                    .eq("user_id", user_id)
                    .in_("phone_e164", list(chunk))
                    .execute()
                )
                rows.extend(result.data or [])
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return rows

    def get_phones_for_contacts(
        self, user_id: str, contact_ids: Sequence[str]
    ) -> List[Dict[str, Any]]:
        if not contact_ids:
            return []
        client = get_service_client()
        rows: List[Dict[str, Any]] = []
        try:
            for chunk in _chunks(list(dict.fromkeys(contact_ids))):
                result = (
                    client.table("contact_phones")
                    .select("*")
                    .eq("user_id", user_id)
                    .in_("contact_id", list(chunk))
                    .execute()
                )
                rows.extend(result.data or [])
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return rows

    def insert_contact(self, data: Dict[str, Any]) -> Dict[str, Any]:
        rows = self.insert_contacts([data])
        if not rows:
            raise SupabaseStorageError("Contact insert returned no row.")
        return rows[0]

    def insert_contacts(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not rows:
            return []
        client = get_service_client()
        inserted: List[Dict[str, Any]] = []
        try:
            for chunk in _row_chunks([_without_none(row) for row in rows]):
                result = client.table("contacts").insert(list(chunk)).execute()
                if not result.data or len(result.data) != len(chunk):
                    raise SupabaseStorageError("Contact insert returned no row.")
                inserted.extend(result.data)
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return inserted

    def update_contact(self, user_id: str, contact_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        client = get_service_client()
        payload = _without_none(data)
        if not payload:
            rows = self.get_by_ids(user_id, [contact_id])
            if not rows:
                raise SupabaseStorageError("Contact not found.")
            return rows[0]
        try:
            result = (
                client.table("contacts")
                .update(payload)
                .eq("user_id", user_id)
                .eq("id", contact_id)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            raise SupabaseStorageError("Contact not found or update failed.")
        return result.data[0]

    def insert_emails(self, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        client = get_service_client()
        try:
            for chunk in _row_chunks(rows):
                (
                    client.table("contact_emails")
                    .upsert(list(chunk), on_conflict="contact_id,email", ignore_duplicates=True)
                    .execute()
                )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return len(rows)

    def insert_phones(self, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        inserted = 0
        client = get_service_client()
        try:
            for chunk in _row_chunks(rows):
                try:
                    result = (
                        client.table("contact_phones")
                        .upsert(
                            list(chunk),
                            on_conflict="contact_id,phone_raw",
                            ignore_duplicates=True,
                        )
                        .execute()
                    )
                    inserted += len(result.data or chunk)
                except APIError as exc:
                    message = (exc.message or "").lower()
                    if "contact_phones_contact_e164_key" not in message and "duplicate" not in message:
                        raise SupabaseStorageError(exc.message) from exc
                    inserted += self._insert_phones_one_by_one(list(chunk))
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return inserted

    def _insert_phones_one_by_one(self, rows: List[Dict[str, Any]]) -> int:
        inserted = 0
        client = get_service_client()
        for row in rows:
            try:
                result = (
                    client.table("contact_phones")
                    .upsert([row], on_conflict="contact_id,phone_raw", ignore_duplicates=True)
                    .execute()
                )
            except APIError as exc:
                message = (exc.message or "").lower()
                if "contact_phones_contact_e164_key" in message or "duplicate" in message:
                    continue
                raise SupabaseStorageError(exc.message) from exc
            inserted += len(result.data or [])
        return inserted

    def list_contacts(self, user_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = (
                client.table("contacts")
                .select("*")
                .eq("user_id", user_id)
                .order("display_name")
                .limit(limit)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return result.data or []

    def search_contacts(self, user_id: str, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        client = get_service_client()
        safe = (
            query.replace("%", "")
            .replace(",", " ")
            .replace("(", " ")
            .replace(")", " ")
            .replace('"', "")
            .strip()
        )
        if not safe:
            return []
        pattern = f"%{safe}%"
        quoted = f'"{pattern}"'
        try:
            result = (
                client.table("contacts")
                .select("*")
                .eq("user_id", user_id)
                .or_(
                    f"display_name.ilike.{quoted},"
                    f"nickname.ilike.{quoted},"
                    f"given_name.ilike.{quoted},"
                    f"family_name.ilike.{quoted}"
                )
                .limit(limit)
                .execute()
            )
            contacts = list(result.data or [])
            found_ids = {row["id"] for row in contacts}

            email_hits = (
                client.table("contact_emails")
                .select("contact_id")
                .eq("user_id", user_id)
                .ilike("email", pattern)
                .limit(limit)
                .execute()
            )
            extra_ids = [
                row["contact_id"]
                for row in (email_hits.data or [])
                if row.get("contact_id") not in found_ids
            ]

            digits = "".join(ch for ch in safe if ch.isdigit() or ch == "+")
            if digits:
                phone_hits = (
                    client.table("contact_phones")
                    .select("contact_id")
                    .eq("user_id", user_id)
                    .or_(f"phone_raw.ilike.{quoted},phone_e164.ilike.{quoted}")
                    .limit(limit)
                    .execute()
                )
                extra_ids.extend(
                    row["contact_id"]
                    for row in (phone_hits.data or [])
                    if row.get("contact_id") not in found_ids
                )

            if extra_ids:
                contacts.extend(self.get_by_ids(user_id, extra_ids[:limit]))
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return contacts[:limit]

    def patch_google_account_metadata(
        self, user_id: str, account_id: str, metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        client = get_service_client()
        try:
            result = (
                client.table("google_accounts")
                .update({"metadata": metadata})
                .eq("user_id", user_id)
                .eq("id", account_id)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            raise SupabaseStorageError("Google account not found.")
        return result.data[0]

    @staticmethod
    def utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
