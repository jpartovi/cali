"""Repository for calendar listener tables."""

from __future__ import annotations

from datetime import date, datetime, timezone
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
        normalized = (channel_id or "").strip()
        if not normalized:
            return None
        try:
            result = (
                client.table("calendar_watches")
                .select("*")
                .eq("channel_id", normalized)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if result.data:
            return result.data[0]
        for watch in self.list_watches():
            stored = (watch.get("channel_id") or "").strip()
            if stored == normalized or stored.replace("-", "") == normalized.replace("-", ""):
                return watch
        return None

    def get_watch_by_resource_id(self, resource_id: str) -> Optional[Dict[str, Any]]:
        client = get_service_client()
        normalized = (resource_id or "").strip()
        if not normalized:
            return None
        try:
            result = (
                client.table("calendar_watches")
                .select("*")
                .eq("resource_id", normalized)
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

    def list_in_progress_timed_snapshots(
        self, user_id: str, now: datetime
    ) -> List[Dict[str, Any]]:
        client = get_service_client()
        now_iso = now.astimezone(timezone.utc).isoformat()
        try:
            result = (
                client.table("calendar_event_snapshots")
                .select("*")
                .eq("user_id", user_id)
                .eq("is_all_day", False)
                .in_("status", ["confirmed", "tentative"])
                .lte("start_at", now_iso)
                .gt("end_at", now_iso)
                .order("start_at")
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
            for table in ("calendar_event_snapshots", "calendar_snapshot_coverage"):
                (
                    client.table(table)
                    .delete()
                    .eq("user_id", user_id)
                    .eq("google_calendar_id", google_calendar_id)
                    .execute()
                )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def upsert_snapshots(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not rows:
            return []
        client = get_service_client()
        # One shape for every row: PostgREST bulk upserts need matching keys
        keys = sorted({key for row in rows for key in row})
        payload = [{key: row.get(key) for key in keys} for row in rows]
        try:
            result = (
                client.table("calendar_event_snapshots")
                .upsert(payload, on_conflict="user_id,google_calendar_id,google_event_id")
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return result.data or []

    def list_snapshots_overlapping(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
        google_calendar_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Snapshot rows (cancelled included) that overlap [start, end)."""
        client = get_service_client()
        rows: List[Dict[str, Any]] = []
        page = 1000
        offset = 0
        try:
            while True:
                query = (
                    client.table("calendar_event_snapshots")
                    .select("*")
                    .eq("user_id", user_id)
                    .gt("end_at", start.isoformat())
                    .lt("start_at", end.isoformat())
                )
                if google_calendar_id:
                    query = query.eq("google_calendar_id", google_calendar_id)
                batch = query.order("id").range(offset, offset + page - 1).execute().data or []
                rows.extend(batch)
                if len(batch) < page:
                    return rows
                offset += page
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def list_coverage(self, user_id: str, day_from: date, day_to: date) -> List[Dict[str, Any]]:
        """Coverage rows for days in [day_from, day_to]."""
        client = get_service_client()
        try:
            result = (
                client.table("calendar_snapshot_coverage")
                .select("google_calendar_id,day,starts_at,ends_at,fetched_at,last_viewed_at")
                .eq("user_id", user_id)
                .gte("day", day_from.isoformat())
                .lte("day", day_to.isoformat())
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return result.data or []

    def covered_span(self, user_id: str, google_calendar_id: str) -> Optional[tuple[datetime, datetime]]:
        """Earliest start and latest end of a calendar's covered days, if any."""
        client = get_service_client()
        try:
            first = (
                client.table("calendar_snapshot_coverage")
                .select("starts_at")
                .eq("user_id", user_id)
                .eq("google_calendar_id", google_calendar_id)
                .order("starts_at")
                .limit(1)
                .execute()
                .data
            )
            last = (
                client.table("calendar_snapshot_coverage")
                .select("ends_at")
                .eq("user_id", user_id)
                .eq("google_calendar_id", google_calendar_id)
                .order("ends_at", desc=True)
                .limit(1)
                .execute()
                .data
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if not first or not last:
            return None
        return (
            datetime.fromisoformat(first[0]["starts_at"]),
            datetime.fromisoformat(last[0]["ends_at"]),
        )

    def is_covered(self, user_id: str, google_calendar_id: str, start: datetime, end: datetime) -> bool:
        """Whether a covered day of this calendar overlaps [start, end)."""
        client = get_service_client()
        try:
            result = (
                client.table("calendar_snapshot_coverage")
                .select("day")
                .eq("user_id", user_id)
                .eq("google_calendar_id", google_calendar_id)
                .lt("starts_at", end.isoformat())
                .gt("ends_at", start.isoformat())
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return bool(result.data)

    def upsert_coverage(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        client = get_service_client()
        try:
            (
                client.table("calendar_snapshot_coverage")
                .upsert(rows, on_conflict="user_id,google_calendar_id,day")
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def touch_coverage(self, user_id: str, days: List[date], viewed_at: datetime) -> None:
        if not days:
            return
        client = get_service_client()
        try:
            (
                client.table("calendar_snapshot_coverage")
                .update({"last_viewed_at": viewed_at.isoformat()})
                .eq("user_id", user_id)
                .in_("day", [day.isoformat() for day in days])
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def delete_coverage(self, user_id: str, google_calendar_id: str) -> None:
        client = get_service_client()
        try:
            (
                client.table("calendar_snapshot_coverage")
                .delete()
                .eq("user_id", user_id)
                .eq("google_calendar_id", google_calendar_id)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def prune_snapshots(self, keep_from: datetime, keep_until: datetime, viewed_after: datetime) -> None:
        """Delete stale coverage, then rows outside [keep_from, keep_until) no coverage holds."""
        client = get_service_client()
        try:
            client.rpc(
                "prune_calendar_snapshots",
                {
                    "keep_from": keep_from.isoformat(),
                    "keep_until": keep_until.isoformat(),
                    "viewed_after": viewed_after.isoformat(),
                },
            ).execute()
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

    def insert_jobs(self, rows: List[Dict[str, Any]]) -> None:
        """Insert many jobs at once; on a duplicate, fall back to one at a time (skipping it)."""
        if not rows:
            return
        client = get_service_client()
        try:
            client.table("calendar_jobs").insert(rows).execute()
        except APIError as exc:
            message = (exc.message or "").lower()
            if "duplicate" not in message and "unique" not in message:
                raise SupabaseStorageError(exc.message) from exc
            for row in rows:
                self.insert_job(row)

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
