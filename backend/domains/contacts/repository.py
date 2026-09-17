"""Database access for Apple contacts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

from postgrest import APIError

from db.session import get_service_client
from utils.errors import SupabaseStorageError

_IN_CHUNK = 100


def _chunks(values: Sequence[str], size: int = _IN_CHUNK) -> Iterable[Sequence[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _row_chunks(rows: Sequence[Dict[str, Any]], size: int = _IN_CHUNK) -> Iterable[Sequence[Dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def _contact_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None or key == "invite_email"}


class ContactsRepository:
    """Upsert and list contacts by Apple identifier."""

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

    def insert_contacts(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not rows:
            return []
        client = get_service_client()
        inserted: List[Dict[str, Any]] = []
        try:
            for chunk in _row_chunks([_contact_payload(row) for row in rows]):
                result = client.table("contacts").insert(list(chunk)).execute()
                if not result.data or len(result.data) != len(chunk):
                    raise SupabaseStorageError("Contact insert returned no row.")
                inserted.extend(result.data)
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return inserted

    def upsert_contacts(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not rows:
            return []
        client = get_service_client()
        upserted: List[Dict[str, Any]] = []
        try:
            for chunk in _row_chunks([_contact_payload(row) for row in rows]):
                result = client.table("contacts").upsert(list(chunk), on_conflict="id").execute()
                upserted.extend(result.data or [])
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return upserted

    def list_contacts(self, user_id: str, limit: int = 2000) -> List[Dict[str, Any]]:
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

    def list_apple_identifiers(self, user_id: str) -> List[str]:
        client = get_service_client()
        identifiers: List[str] = []
        page_size = 1000
        offset = 0
        try:
            while True:
                result = (
                    client.table("contacts")
                    .select("apple_identifier")
                    .eq("user_id", user_id)
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                rows = result.data or []
                for row in rows:
                    identifier = row.get("apple_identifier")
                    if identifier:
                        identifiers.append(identifier)
                if len(rows) < page_size:
                    break
                offset += page_size
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return identifiers

    def delete_by_apple_identifiers(self, user_id: str, identifiers: Sequence[str]) -> int:
        if not identifiers:
            return 0
        client = get_service_client()
        deleted = 0
        try:
            for chunk in _chunks(list(dict.fromkeys(identifiers))):
                result = (
                    client.table("contacts")
                    .delete()
                    .eq("user_id", user_id)
                    .in_("apple_identifier", list(chunk))
                    .execute()
                )
                deleted += len(result.data or [])
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return deleted

    @staticmethod
    def utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
