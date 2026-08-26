"""Orchestrate Google Calendar watches, incremental sync, jobs, and triggers."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from core.config import get_settings
from domains.calendar_listener.changes import (
    CHANGE_CANCELLED,
    CHANGE_EVENT_END,
    CHANGE_EVENT_START,
    change_fingerprint,
    classify_snapshot_change,
)
from domains.calendar_listener.repository import CalendarListenerRepository
from domains.calendar_listener.snapshots import (
    HORIZON_FUTURE,
    HORIZON_PAST,
    is_recurring_master,
    parse_iso,
    snapshot_from_google_event,
)
from domains.calendar_listener.triggers import CalendarChangeEvent, emit_change
from domains.calendars.providers.google import GoogleCalendarAPIError, GoogleCalendarHttpClient
from domains.calendars.repository import CalendarRepository
from domains.calendars.service import CalendarService

logger = logging.getLogger(__name__)

SUPPRESSION_WINDOW = timedelta(minutes=2)
SYNC_LOCK_STALE = timedelta(minutes=2)
WATCH_RENEW_LEAD = timedelta(hours=24)
RECONCILE_STALE = timedelta(minutes=20)
MAX_BOOTSTRAPS_PER_TICK = 3
MAX_SYNCS_PER_TICK = 10
MAX_JOBS_PER_TICK = 50
MAX_RENEWS_PER_TICK = 10


class CalendarListenerService:
    """Listen to Google Calendar, project events, and emit internal change triggers."""

    def __init__(
        self,
        listener_repo: CalendarListenerRepository | None = None,
        calendar_repo: CalendarRepository | None = None,
        calendar_service: CalendarService | None = None,
    ) -> None:
        self.listener_repo = listener_repo or CalendarListenerRepository()
        self.calendar_repo = calendar_repo or CalendarRepository()
        self.calendar_service = calendar_service or CalendarService(repository=self.calendar_repo)

    def is_configured(self) -> bool:
        settings = get_settings()
        return bool(settings.google_webhook_token and self._webhook_address())

    def _webhook_address(self) -> Optional[str]:
        settings = get_settings()
        if settings.google_calendar_webhook_url:
            address = settings.google_calendar_webhook_url.rstrip("/")
        else:
            address = f"{settings.backend_url.rstrip('/')}/api/v1/webhooks/google-calendar"
        parsed = urlparse(address)
        if parsed.scheme != "https" or not parsed.netloc:
            return None
        return address

    async def handle_google_webhook(
        self,
        *,
        channel_id: str,
        channel_token: str,
        resource_state: str,
        resource_id: Optional[str],
    ) -> None:
        settings = get_settings()
        if not settings.google_webhook_token or channel_token != settings.google_webhook_token:
            logger.warning("Ignoring Google calendar webhook with invalid channel token")
            return
        if resource_state == "sync":
            return
        watch = self.listener_repo.get_watch_by_channel(channel_id)
        if not watch:
            logger.warning("Google calendar webhook for unknown channel_id=%s", channel_id)
            return
        if resource_id and watch.get("resource_id") and resource_id != watch["resource_id"]:
            logger.warning("Google calendar webhook resource_id mismatch channel=%s", channel_id)
            return
        if watch.get("kind") == "calendar_list":
            await self._handle_calendar_list_notification(watch)
            return
        if resource_state == "not_exists":
            await self._stop_watch(watch, access_token=None)
            return
        await self.sync_watch(watch["id"])

    async def bootstrap_account(self, user_id: str, google_account_id: str) -> None:
        account = self.calendar_repo.get_account_by_id(user_id, google_account_id)
        if not account:
            return
        await self._ensure_account_watches(account, bootstrap=True)

    async def ensure_calendar_watch(
        self, user_id: str, calendar_row: Dict[str, Any], *, bootstrap: bool = True
    ) -> None:
        if calendar_row.get("is_hidden"):
            await self.stop_calendar(user_id, calendar_row.get("google_calendar_id") or "")
            return
        account = self.calendar_repo.get_account_by_id(user_id, calendar_row["google_account_id"])
        if not account:
            return
        await self._ensure_event_watch(account, calendar_row, bootstrap=bootstrap)

    async def stop_calendar(self, user_id: str, google_calendar_id: str) -> None:
        if not google_calendar_id:
            return
        watches = [
            watch
            for watch in self.listener_repo.list_watches(user_id=user_id, kind="events")
            if watch.get("google_calendar_id") == google_calendar_id
        ]
        account_tokens: Dict[str, str] = {}
        for watch in watches:
            token = await self._token_for_watch(watch, cache=account_tokens)
            await self._stop_watch(watch, access_token=token)
        self.listener_repo.cancel_calendar_jobs(user_id, google_calendar_id)
        self.listener_repo.delete_snapshots_for_calendar(user_id, google_calendar_id)

    async def stop_account(self, user_id: str, google_account_id: str) -> None:
        watches = self.listener_repo.list_watches(google_account_id=google_account_id)
        account = self.calendar_repo.get_account_by_id(user_id, google_account_id)
        access_token = None
        if account:
            try:
                access_token = await self.calendar_service._ensure_access_token(account)
            except Exception:
                logger.warning("Could not refresh token while stopping watches account=%s", google_account_id)
        for watch in watches:
            await self._stop_watch(watch, access_token=access_token)

    async def tick(self) -> Dict[str, int]:
        """Cron entry: renew, reconcile, run due jobs, prune."""
        now = datetime.now(timezone.utc)
        stats = {
            "bootstraps": 0,
            "renews": 0,
            "syncs": 0,
            "jobs": 0,
        }
        if self.is_configured():
            stats["bootstraps"] = await self._bootstrap_missing_accounts(limit=MAX_BOOTSTRAPS_PER_TICK)
            stats["renews"] = await self._renew_expiring_watches(now)
            stats["syncs"] = await self._reconcile_stale_watches(now)
        stats["jobs"] = await self.run_due_jobs(now)
        self.listener_repo.prune_old_snapshots(now - HORIZON_PAST)
        self.listener_repo.prune_processed_keys(now - timedelta(days=7))
        self.listener_repo.prune_suppressions(now - timedelta(hours=1))
        return stats

    async def run_due_jobs(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(timezone.utc)
        jobs = self.listener_repo.list_due_jobs(now, MAX_JOBS_PER_TICK)
        ran = 0
        for job in jobs:
            kind = job.get("kind")
            job_id = job.get("id")
            if not job_id:
                continue
            self.listener_repo.mark_job_done(job_id)
            ran += 1
            if kind == "watch_renew":
                payload = job.get("payload") or {}
                watch_id = payload.get("watch_id")
                if isinstance(watch_id, str):
                    await self._renew_watch_id(watch_id)
                continue
            if kind in {CHANGE_EVENT_START, CHANGE_EVENT_END}:
                await self._emit_time_job(job)
        return ran

    async def sync_watch(self, watch_id: str) -> None:
        watch = self.listener_repo.get_watch(watch_id)
        if not watch or watch.get("kind") != "events":
            return
        stale_after = datetime.now(timezone.utc) - SYNC_LOCK_STALE
        if not self.listener_repo.try_acquire_sync_lock(watch_id, stale_after=stale_after):
            return
        try:
            await self._sync_event_watch(watch)
            self.listener_repo.update_watch(
                watch_id,
                {
                    "sync_lock_at": None,
                    "last_synced_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            self.listener_repo.update_watch(watch_id, {"sync_lock_at": None})
            raise

    async def _bootstrap_missing_accounts(self, limit: int) -> int:
        accounts = self.calendar_repo.get_all_accounts()
        existing = {
            watch.get("google_account_id")
            for watch in self.listener_repo.list_watches(kind="calendar_list")
        }
        bootstrapped = 0
        for account in accounts:
            if bootstrapped >= limit:
                break
            if account.get("id") in existing:
                continue
            try:
                await self._ensure_account_watches(account, bootstrap=True)
                bootstrapped += 1
            except Exception:
                logger.exception("Failed to bootstrap calendar listener account=%s", account.get("id"))
        return bootstrapped

    async def _ensure_account_watches(self, account: Dict[str, Any], *, bootstrap: bool) -> None:
        if not self.is_configured():
            logger.warning("Skipping calendar watches; webhook URL or token is not configured")
            return
        user_id = account.get("user_id")
        account_id = account.get("id")
        if not user_id or not account_id:
            return
        await self._ensure_calendar_list_watch(account)
        calendars = self.calendar_repo.get_calendars_by_account(account_id, include_hidden=True)
        visible_ids = set()
        for calendar in calendars:
            google_calendar_id = calendar.get("google_calendar_id")
            if calendar.get("is_hidden") or not google_calendar_id:
                continue
            visible_ids.add(google_calendar_id)
            await self._ensure_event_watch(account, calendar, bootstrap=bootstrap)
        for watch in self.listener_repo.list_watches(google_account_id=account_id, kind="events"):
            if watch.get("google_calendar_id") not in visible_ids:
                token = await self._token_for_account(account)
                await self._stop_watch(watch, access_token=token)

    async def _handle_calendar_list_notification(self, watch: Dict[str, Any]) -> None:
        account = self.calendar_repo.get_account_by_id(watch["user_id"], watch["google_account_id"])
        if not account:
            return
        try:
            await self.calendar_service.hydrate_calendars(watch["user_id"])
        except Exception:
            logger.exception("Failed to hydrate calendars after calendarList watch user=%s", watch["user_id"])
        account = self.calendar_repo.get_account_by_id(watch["user_id"], watch["google_account_id"])
        if account:
            await self._ensure_account_watches(account, bootstrap=True)

    async def _ensure_calendar_list_watch(self, account: Dict[str, Any]) -> None:
        existing = [
            watch
            for watch in self.listener_repo.list_watches(
                google_account_id=account["id"], kind="calendar_list"
            )
        ]
        if existing and not self._needs_renewal(existing[0]):
            return
        access_token = await self._token_for_account(account)
        if existing:
            await self._stop_watch(existing[0], access_token=access_token)
        await self._start_watch(
            account=account,
            kind="calendar_list",
            google_calendar_id=None,
            access_token=access_token,
        )

    async def _ensure_event_watch(
        self, account: Dict[str, Any], calendar: Dict[str, Any], *, bootstrap: bool
    ) -> None:
        google_calendar_id = calendar.get("google_calendar_id")
        if not google_calendar_id:
            return
        existing = next(
            (
                watch
                for watch in self.listener_repo.list_watches(
                    google_account_id=account["id"], kind="events"
                )
                if watch.get("google_calendar_id") == google_calendar_id
            ),
            None,
        )
        needs_start = existing is None or self._needs_renewal(existing)
        access_token = await self._token_for_account(account)
        if needs_start:
            if existing:
                await self._stop_watch(existing, access_token=access_token)
            watch = await self._start_watch(
                account=account,
                kind="events",
                google_calendar_id=google_calendar_id,
                access_token=access_token,
            )
        else:
            watch = existing
        if bootstrap and watch and not watch.get("sync_token"):
            await self.sync_watch(watch["id"])

    def _needs_renewal(self, watch: Dict[str, Any]) -> bool:
        expiration = parse_iso(watch.get("expiration"))
        if not expiration:
            return True
        return expiration <= datetime.now(timezone.utc) + WATCH_RENEW_LEAD

    async def _start_watch(
        self,
        *,
        account: Dict[str, Any],
        kind: str,
        google_calendar_id: Optional[str],
        access_token: str,
    ) -> Optional[Dict[str, Any]]:
        address = self._webhook_address()
        token = get_settings().google_webhook_token
        if not address or not token:
            return None
        channel_id = uuid.uuid4().hex
        async with GoogleCalendarHttpClient() as client:
            try:
                if kind == "calendar_list":
                    response = await client.watch_calendar_list(
                        access_token=access_token,
                        channel_id=channel_id,
                        address=address,
                        token=token,
                    )
                else:
                    if not google_calendar_id:
                        return None
                    response = await client.watch_events(
                        access_token=access_token,
                        calendar_id=google_calendar_id,
                        channel_id=channel_id,
                        address=address,
                        token=token,
                    )
            except GoogleCalendarAPIError:
                logger.exception(
                    "Failed to start Google watch kind=%s calendar=%s account=%s",
                    kind,
                    google_calendar_id,
                    account.get("id"),
                )
                return None
        expiration = _parse_google_expiration(response.get("expiration"))
        if expiration is None:
            expiration = datetime.now(timezone.utc) + timedelta(days=6)
        watch = self.listener_repo.upsert_watch(
            {
                "user_id": account["user_id"],
                "google_account_id": account["id"],
                "kind": kind,
                "google_calendar_id": google_calendar_id,
                "channel_id": channel_id,
                "resource_id": response.get("resourceId"),
                "expiration": expiration.isoformat() if expiration else None,
            }
        )
        if expiration:
            self._schedule_watch_renew(watch, expiration)
        return watch

    async def _stop_watch(self, watch: Dict[str, Any], access_token: Optional[str]) -> None:
        channel_id = watch.get("channel_id")
        resource_id = watch.get("resource_id")
        if access_token and channel_id and resource_id:
            async with GoogleCalendarHttpClient() as client:
                try:
                    await client.stop_channel(
                        access_token=access_token,
                        channel_id=channel_id,
                        resource_id=resource_id,
                    )
                except GoogleCalendarAPIError as exc:
                    if exc.status_code not in {404, 410}:
                        logger.warning(
                            "Failed to stop Google channel %s: %s",
                            channel_id,
                            exc,
                        )
        if watch.get("id"):
            self.listener_repo.cancel_watch_renew_job(watch["id"])
            self.listener_repo.delete_watch(watch["id"])

    async def _renew_watch_id(self, watch_id: str) -> None:
        watch = self.listener_repo.get_watch(watch_id)
        if not watch:
            return
        account = self.calendar_repo.get_account_by_id(watch["user_id"], watch["google_account_id"])
        if not account:
            return
        access_token = await self._token_for_account(account)
        google_calendar_id = watch.get("google_calendar_id")
        await self._stop_watch(watch, access_token=access_token)
        if watch.get("kind") == "calendar_list":
            await self._start_watch(
                account=account,
                kind="calendar_list",
                google_calendar_id=None,
                access_token=access_token,
            )
            return
        calendar = next(
            (
                row
                for row in self.calendar_repo.get_calendars_by_account(account["id"], include_hidden=True)
                if row.get("google_calendar_id") == google_calendar_id
            ),
            None,
        )
        if not calendar or calendar.get("is_hidden"):
            return
        new_watch = await self._start_watch(
            account=account,
            kind="events",
            google_calendar_id=google_calendar_id,
            access_token=access_token,
        )
        if new_watch:
            await self.sync_watch(new_watch["id"])

    async def _renew_expiring_watches(self, now: datetime) -> int:
        renewed = 0
        for watch in self.listener_repo.list_expiring_watches(now + WATCH_RENEW_LEAD):
            if renewed >= MAX_RENEWS_PER_TICK:
                break
            try:
                await self._renew_watch_id(watch["id"])
                renewed += 1
            except Exception:
                logger.exception("Failed to renew watch %s", watch.get("id"))
        return renewed

    async def _reconcile_stale_watches(self, now: datetime) -> int:
        synced = 0
        stale_before = now - RECONCILE_STALE
        for watch in self.listener_repo.list_stale_event_watches(stale_before, MAX_SYNCS_PER_TICK):
            try:
                await self.sync_watch(watch["id"])
                synced += 1
            except Exception:
                logger.exception("Failed to reconcile watch %s", watch.get("id"))
        return synced

    async def _sync_event_watch(self, watch: Dict[str, Any]) -> None:
        account = self.calendar_repo.get_account_by_id(watch["user_id"], watch["google_account_id"])
        if not account:
            return
        google_calendar_id = watch.get("google_calendar_id")
        if not google_calendar_id:
            return
        access_token = await self._token_for_account(account)
        sync_token = watch.get("sync_token")
        async with GoogleCalendarHttpClient() as client:
            if sync_token:
                try:
                    result = await client.list_events_sync(
                        access_token=access_token,
                        calendar_id=google_calendar_id,
                        sync_token=sync_token,
                    )
                except GoogleCalendarAPIError as exc:
                    if exc.status_code == 410:
                        result = await self._horizon_list(
                            client, access_token=access_token, calendar_id=google_calendar_id
                        )
                    else:
                        raise
            else:
                result = await self._horizon_list(
                    client, access_token=access_token, calendar_id=google_calendar_id
                )
            items = result.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                await self._apply_google_event(
                    client=client,
                    access_token=access_token,
                    account=account,
                    google_calendar_id=google_calendar_id,
                    event=item,
                )
            next_token = result.get("nextSyncToken")
            if next_token:
                self.listener_repo.update_watch(watch["id"], {"sync_token": next_token})

    async def _horizon_list(
        self,
        client: GoogleCalendarHttpClient,
        *,
        access_token: str,
        calendar_id: str,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        return await client.list_events_sync(
            access_token=access_token,
            calendar_id=calendar_id,
            time_min=(now - HORIZON_PAST).isoformat().replace("+00:00", "Z"),
            time_max=(now + HORIZON_FUTURE).isoformat().replace("+00:00", "Z"),
        )

    async def _apply_google_event(
        self,
        *,
        client: GoogleCalendarHttpClient,
        access_token: str,
        account: Dict[str, Any],
        google_calendar_id: str,
        event: Dict[str, Any],
    ) -> None:
        user_id = account["user_id"]
        status = event.get("status")
        if is_recurring_master(event):
            if status == "cancelled":
                await self._cancel_series(user_id, google_calendar_id, event.get("id") or "")
                return
            now = datetime.now(timezone.utc)
            try:
                instances = await client.list_event_instances(
                    access_token=access_token,
                    calendar_id=google_calendar_id,
                    event_id=event["id"],
                    time_min=(now - HORIZON_PAST).isoformat().replace("+00:00", "Z"),
                    time_max=(now + HORIZON_FUTURE).isoformat().replace("+00:00", "Z"),
                )
            except GoogleCalendarAPIError:
                logger.exception("Failed to expand recurring event %s", event.get("id"))
                return
            for instance in instances:
                await self._upsert_and_trigger(
                    account=account,
                    google_calendar_id=google_calendar_id,
                    event=instance,
                )
            return
        await self._upsert_and_trigger(
            account=account,
            google_calendar_id=google_calendar_id,
            event=event,
        )

    async def _cancel_series(self, user_id: str, google_calendar_id: str, master_id: str) -> None:
        if not master_id:
            return
        rows = self.listener_repo.list_snapshots_for_series(user_id, google_calendar_id, master_id)
        master = self.listener_repo.get_snapshot(user_id, google_calendar_id, master_id)
        if master:
            rows = [*rows, master]
        for row in rows:
            cancelled = dict(row)
            cancelled["status"] = "cancelled"
            self.listener_repo.upsert_snapshot(
                {key: cancelled[key] for key in cancelled if key not in {"id", "created_at", "updated_at"}}
            )
            await self._after_snapshot(previous=row, current=cancelled)

    async def _upsert_and_trigger(
        self,
        *,
        account: Dict[str, Any],
        google_calendar_id: str,
        event: Dict[str, Any],
    ) -> None:
        snapshot = snapshot_from_google_event(
            user_id=account["user_id"],
            google_account_id=account["id"],
            google_calendar_id=google_calendar_id,
            event=event,
        )
        if not snapshot:
            return
        end_at = parse_iso(snapshot.get("end_at"))
        now = datetime.now(timezone.utc)
        if (
            snapshot.get("status") != "cancelled"
            and end_at
            and end_at < now - HORIZON_PAST
        ):
            return
        previous = self.listener_repo.get_snapshot(
            account["user_id"], google_calendar_id, snapshot["google_event_id"]
        )
        stored = self.listener_repo.upsert_snapshot(snapshot)
        await self._after_snapshot(previous=previous, current=stored)

    async def _after_snapshot(
        self, *, previous: Optional[Dict[str, Any]], current: Dict[str, Any]
    ) -> None:
        self._sync_jobs(previous=previous, current=current)
        kinds = classify_snapshot_change(previous, current)
        if not kinds:
            return
        if self._is_self_write(current):
            logger.info(
                "Skipping self-write calendar change event=%s kinds=%s",
                current.get("google_event_id"),
                kinds,
            )
            return
        for kind in kinds:
            fingerprint = change_fingerprint(kind, current)
            if not self.listener_repo.try_record_change(
                current["user_id"],
                current["google_event_id"],
                kind,
                fingerprint,
            ):
                continue
            await emit_change(
                CalendarChangeEvent(
                    user_id=current["user_id"],
                    google_calendar_id=current["google_calendar_id"],
                    google_event_id=current["google_event_id"],
                    kind=kind,
                    snapshot=current,
                    previous=previous,
                )
            )

    def _is_self_write(self, snapshot: Dict[str, Any]) -> bool:
        since = datetime.now(timezone.utc) - SUPPRESSION_WINDOW
        return self.listener_repo.has_recent_suppression(
            snapshot["user_id"],
            snapshot["google_calendar_id"],
            snapshot["google_event_id"],
            since=since,
            etag=snapshot.get("etag"),
        )

    def _sync_jobs(self, *, previous: Optional[Dict[str, Any]], current: Dict[str, Any]) -> None:
        user_id = current["user_id"]
        event_id = current["google_event_id"]
        calendar_id = current["google_calendar_id"]
        cancelled = current.get("status") == "cancelled"
        all_day = bool(current.get("is_all_day"))
        times_changed = previous is not None and (
            previous.get("start_at") != current.get("start_at")
            or previous.get("end_at") != current.get("end_at")
            or previous.get("status") != current.get("status")
            or previous.get("is_all_day") != current.get("is_all_day")
        )
        if cancelled or all_day or times_changed:
            self.listener_repo.cancel_event_jobs(
                user_id, event_id, ["event_start", "event_end"]
            )
        if cancelled or all_day:
            return
        start_at = parse_iso(current.get("start_at"))
        end_at = parse_iso(current.get("end_at"))
        now = datetime.now(timezone.utc)
        if not start_at or not end_at or end_at <= now:
            return
        start_run = start_at if start_at > now else now
        self.listener_repo.insert_job(
            {
                "user_id": user_id,
                "google_calendar_id": calendar_id,
                "google_event_id": event_id,
                "kind": CHANGE_EVENT_START,
                "run_at": start_run.isoformat(),
                "payload": {"title": current.get("title"), "end_at": current.get("end_at")},
            }
        )
        self.listener_repo.insert_job(
            {
                "user_id": user_id,
                "google_calendar_id": calendar_id,
                "google_event_id": event_id,
                "kind": CHANGE_EVENT_END,
                "run_at": end_at.isoformat(),
                "payload": {"title": current.get("title")},
            }
        )

    def _schedule_watch_renew(self, watch: Dict[str, Any], expiration: datetime) -> None:
        run_at = expiration - WATCH_RENEW_LEAD
        if run_at < datetime.now(timezone.utc):
            run_at = datetime.now(timezone.utc)
        self.listener_repo.cancel_watch_renew_job(watch["id"])
        self.listener_repo.insert_job(
            {
                "user_id": watch["user_id"],
                "kind": "watch_renew",
                "run_at": run_at.isoformat(),
                "payload": {"watch_id": watch["id"]},
            }
        )

    async def _emit_time_job(self, job: Dict[str, Any]) -> None:
        user_id = job.get("user_id")
        event_id = job.get("google_event_id")
        calendar_id = job.get("google_calendar_id")
        kind = job.get("kind")
        if not user_id or not event_id or not calendar_id or not kind:
            return
        snapshot = self.listener_repo.get_snapshot(user_id, calendar_id, event_id)
        if not snapshot or snapshot.get("status") == "cancelled":
            return
        if kind == CHANGE_EVENT_START and snapshot.get("is_all_day"):
            return
        fingerprint = f"{kind}|{job.get('run_at')}|{snapshot.get('etag') or ''}"
        if not self.listener_repo.try_record_change(user_id, event_id, kind, fingerprint):
            return
        await emit_change(
            CalendarChangeEvent(
                user_id=user_id,
                google_calendar_id=calendar_id,
                google_event_id=event_id,
                kind=kind,
                snapshot=snapshot,
            )
        )

    async def _token_for_account(self, account: Dict[str, Any]) -> str:
        return await self.calendar_service._ensure_access_token(account)

    async def _token_for_watch(
        self, watch: Dict[str, Any], cache: Dict[str, str]
    ) -> Optional[str]:
        account_id = watch.get("google_account_id")
        if not account_id:
            return None
        if account_id in cache:
            return cache[account_id]
        account = self.calendar_repo.get_account_by_id(watch["user_id"], account_id)
        if not account:
            return None
        try:
            token = await self._token_for_account(account)
        except Exception:
            return None
        cache[account_id] = token
        return token


def _parse_google_expiration(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError):
        parsed = parse_iso(value)
        return parsed
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
