"""Repository for calendar listener tables."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from postgrest import APIError

from db.session import get_service_client
from utils.errors import SupabaseStorageError


def _without_none(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


class CalendarListenerRepository:
    """Persistence for watches, snapshots, jobs, and idempotency keys."""

    def get_watch_by_channel(self, channel_id: str) -> Optional[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = (
                client.table("calendar_watches")
                .select("*")
                .eq("channel_id", channel_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            return None
        return result.data[0]

    def get_watch(self, watch_id: str) -> Optional[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = (
                client.table("calendar_watches")
                .select("*")
                .eq("id", watch_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            return None
        return result.data[0]

    def list_watches(
        self,
        *,
        user_id: Optional[str] = None,
        google_account_id: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        client = get_service_client()
        try:
            query = client.table("calendar_watches").select("*")
            if user_id:
                query = query.eq("user_id", user_id)
            if google_account_id:
                query = query.eq("google_account_id", google_account_id)
            if kind:
                query = query.eq("kind", kind)
            result = query.execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return result.data or []

    def list_expiring_watches(self, before: datetime) -> List[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = (
                client.table("calendar_watches")
                .select("*")
                .lt("expiration", before.isoformat())
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return result.data or []

    def list_stale_event_watches(self, stale_before: datetime, limit: int) -> List[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = (
                client.table("calendar_watches")
                .select("*")
                .eq("kind", "events")
                .or_(
                    "last_synced_at.is.null,last_synced_at.lt."
                    + stale_before.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                )
                .limit(limit)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return result.data or []

    def upsert_watch(self, data: Dict[str, Any]) -> Dict[str, Any]:
        client = get_service_client()
        payload = _without_none(data)
        try:
            result = (
                client.table("calendar_watches")
                .upsert(payload, on_conflict="channel_id")
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            raise SupabaseStorageError("Watch upsert returned no row.")
        return result.data[0]

    def update_watch(self, watch_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = (
                client.table("calendar_watches")
                .update(data)
                .eq("id", watch_id)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            return None
        return result.data[0]

    def try_acquire_sync_lock(self, watch_id: str, *, stale_after: datetime) -> bool:
        """Lock a watch for sync if unlocked or the lock is stale."""
        client = get_service_client()
        now = datetime.now(timezone.utc)
        try:
            result = (
                client.table("calendar_watches")
                .update({"sync_lock_at": now.isoformat()})
                .eq("id", watch_id)
                .or_(
                    "sync_lock_at.is.null,sync_lock_at.lt."
                    + stale_after.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                )
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return bool(result.data)

    def delete_watch(self, watch_id: str) -> None:
        client = get_service_client()
        try:
            client.table("calendar_watches").delete().eq("id", watch_id).execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def get_snapshot(
        self, user_id: str, google_calendar_id: str, google_event_id: str
    ) -> Optional[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = (
                client.table("calendar_event_snapshots")
                .select("*")
                .eq("user_id", user_id)
                .eq("google_calendar_id", google_calendar_id)
                .eq("google_event_id", google_event_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            return None
        return result.data[0]

    def list_snapshots_for_series(
        self, user_id: str, google_calendar_id: str, recurring_event_id: str
    ) -> List[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = (
                client.table("calendar_event_snapshots")
                .select("*")
                .eq("user_id", user_id)
                .eq("google_calendar_id", google_calendar_id)
                .eq("recurring_event_id", recurring_event_id)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return result.data or []

    def upsert_snapshot(self, data: Dict[str, Any]) -> Dict[str, Any]:
        client = get_service_client()
        payload = _without_none(data)
        try:
            result = (
                client.table("calendar_event_snapshots")
                .upsert(payload, on_conflict="user_id,google_calendar_id,google_event_id")
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            raise SupabaseStorageError("Snapshot upsert returned no row.")
        return result.data[0]

    def delete_snapshot(
        self, user_id: str, google_calendar_id: str, google_event_id: str
    ) -> None:
        client = get_service_client()
        try:
            (
                client.table("calendar_event_snapshots")
                .delete()
                .eq("user_id", user_id)
                .eq("google_calendar_id", google_calendar_id)
                .eq("google_event_id", google_event_id)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def delete_snapshots_for_calendar(self, user_id: str, google_calendar_id: str) -> None:
        client = get_service_client()
        try:
            (
                client.table("calendar_event_snapshots")
                .delete()
                .eq("user_id", user_id)
                .eq("google_calendar_id", google_calendar_id)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def prune_old_snapshots(self, ended_before: datetime) -> None:
        client = get_service_client()
        try:
            (
                client.table("calendar_event_snapshots")
                .delete()
                .lt("end_at", ended_before.isoformat())
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def insert_job(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        client = get_service_client()
        payload = _without_none(data)
        try:
            result = client.table("calendar_jobs").insert(payload).execute()
        except APIError as exc:
            message = (exc.message or "").lower()
            if "duplicate" in message or "unique" in message:
                return None
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            return None
        return result.data[0]

    def cancel_event_jobs(
        self, user_id: str, google_event_id: str, kinds: Optional[List[str]] = None
    ) -> None:
        client = get_service_client()
        try:
            query = (
                client.table("calendar_jobs")
                .update({"status": "cancelled"})
                .eq("user_id", user_id)
                .eq("google_event_id", google_event_id)
                .eq("status", "pending")
            )
            if kinds:
                query = query.in_("kind", kinds)
            query.execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def cancel_calendar_jobs(self, user_id: str, google_calendar_id: str) -> None:
        client = get_service_client()
        try:
            (
                client.table("calendar_jobs")
                .update({"status": "cancelled"})
                .eq("user_id", user_id)
                .eq("google_calendar_id", google_calendar_id)
                .eq("status", "pending")
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def cancel_watch_renew_job(self, watch_id: str) -> None:
        client = get_service_client()
        try:
            pending = (
                client.table("calendar_jobs")
                .select("id,payload")
                .eq("kind", "watch_renew")
                .eq("status", "pending")
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        for row in pending.data or []:
            payload = row.get("payload") or {}
            if payload.get("watch_id") == watch_id:
                (
                    client.table("calendar_jobs")
                    .update({"status": "cancelled"})
                    .eq("id", row["id"])
                    .execute()
                )

    def list_due_jobs(self, now: datetime, limit: int) -> List[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = (
                client.table("calendar_jobs")
                .select("*")
                .eq("status", "pending")
                .lte("run_at", now.isoformat())
                .order("run_at")
                .limit(limit)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return result.data or []

    def mark_job_done(self, job_id: str) -> None:
        client = get_service_client()
        try:
            (
                client.table("calendar_jobs")
                .update({"status": "done"})
                .eq("id", job_id)
                .eq("status", "pending")
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def try_record_change(
        self, user_id: str, google_event_id: str, kind: str, fingerprint: str
    ) -> bool:
        client = get_service_client()
        try:
            client.table("processed_change_keys").insert(
                {
                    "user_id": user_id,
                    "google_event_id": google_event_id,
                    "kind": kind,
                    "fingerprint": fingerprint,
                }
            ).execute()
        except APIError as exc:
            message = (exc.message or "").lower()
            if "duplicate" in message or "unique" in message:
                return False
            raise SupabaseStorageError(exc.message) from exc
        return True

    def prune_processed_keys(self, older_than: datetime) -> None:
        client = get_service_client()
        try:
            (
                client.table("processed_change_keys")
                .delete()
                .lt("created_at", older_than.isoformat())
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def insert_suppression(
        self,
        user_id: str,
        google_calendar_id: str,
        google_event_id: str,
        etag: Optional[str],
    ) -> None:
        client = get_service_client()
        try:
            client.table("calendar_write_suppressions").insert(
                _without_none(
                    {
                        "user_id": user_id,
                        "google_calendar_id": google_calendar_id,
                        "google_event_id": google_event_id,
                        "etag": etag,
                    }
                )
            ).execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def has_recent_suppression(
        self,
        user_id: str,
        google_calendar_id: str,
        google_event_id: str,
        *,
        since: datetime,
        etag: Optional[str] = None,
    ) -> bool:
        client = get_service_client()
        try:
            query = (
                client.table("calendar_write_suppressions")
                .select("id,etag")
                .eq("user_id", user_id)
                .eq("google_calendar_id", google_calendar_id)
                .eq("google_event_id", google_event_id)
                .gte("created_at", since.isoformat())
            )
            result = query.execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        rows = result.data or []
        if not rows:
            return False
        if etag is None:
            return True
        return any(row.get("etag") in {None, etag} for row in rows)

    def prune_suppressions(self, older_than: datetime) -> None:
        client = get_service_client()
        try:
            (
                client.table("calendar_write_suppressions")
                .delete()
                .lt("created_at", older_than.isoformat())
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
