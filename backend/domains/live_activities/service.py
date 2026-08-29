"""Start and end Live Activities from calendar listener snapshots and jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from domains.calendar_listener.changes import (
    CHANGE_CANCELLED,
    CHANGE_CREATED,
    CHANGE_EVENT_END,
    CHANGE_EVENT_START,
    CHANGE_UPDATED,
)
from domains.calendar_listener.repository import CalendarListenerRepository
from domains.calendar_listener.snapshots import parse_iso
from domains.calendar_listener.triggers import CalendarChangeEvent, register_handler
from domains.live_activities.apns import APNsClient, APNsError
from domains.live_activities.repository import LiveActivityRepository
from utils.errors import SupabaseStorageError

logger = logging.getLogger(__name__)

MAX_CONCURRENT_ACTIVITIES = 8

_handler_registered = False


def register_live_activity_handler() -> None:
    """Subscribe Live Activities to calendar change events. Safe to call more than once."""
    global _handler_registered
    if _handler_registered:
        return
    register_handler(handle_calendar_change)
    _handler_registered = True


async def handle_calendar_change(event: CalendarChangeEvent) -> None:
    await LiveActivitySyncService().handle_change(event)


def _event_title(snapshot: Dict[str, Any]) -> str:
    raw = (snapshot.get("title") or "").strip()
    return raw if raw else "Untitled Event"


def _is_timed_open(snapshot: Dict[str, Any], now: datetime) -> bool:
    if snapshot.get("status") == "cancelled" or snapshot.get("is_all_day"):
        return False
    end_at = parse_iso(snapshot.get("end_at"))
    start_at = parse_iso(snapshot.get("start_at"))
    if not start_at or not end_at or end_at <= now:
        return False
    return True


def _is_in_progress(snapshot: Dict[str, Any], now: datetime) -> bool:
    if not _is_timed_open(snapshot, now):
        return False
    start_at = parse_iso(snapshot.get("start_at"))
    return start_at is not None and start_at <= now


def _parse_instance_end(row: Dict[str, Any], fallback: datetime) -> datetime:
    parsed = parse_iso(row.get("end_at"))
    return parsed if parsed else fallback


class LiveActivitySyncService:
    def __init__(
        self,
        repository: LiveActivityRepository | None = None,
        listener_repo: CalendarListenerRepository | None = None,
        apns: APNsClient | None = None,
    ) -> None:
        self.repository = repository or LiveActivityRepository()
        self.listener_repo = listener_repo or CalendarListenerRepository()
        self.apns = apns or APNsClient()

    def upsert_push_to_start_token(self, user_id: str, token: str) -> None:
        self.repository.upsert_push_to_start_token(user_id, token)

    def delete_push_to_start_token(self, user_id: str) -> None:
        self.repository.delete_push_to_start_token(user_id)

    def set_activity_push_token(self, user_id: str, event_id: str, token: str) -> None:
        self.repository.set_activity_push_token(user_id, event_id, token)

    def record_local_start(
        self, user_id: str, event_id: str, title: str, end_at: datetime
    ) -> None:
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=timezone.utc)
        else:
            end_at = end_at.astimezone(timezone.utc)
        if self.repository.has_dismissal(user_id, event_id):
            return
        existing = self.repository.get_started_instance(user_id, event_id)
        if existing:
            self.repository.update_started_instance(
                existing["id"], title=title, end_at=end_at
            )
            return
        self.repository.try_insert_started_instance(
            user_id=user_id,
            event_id=event_id,
            title=title,
            end_at=end_at,
        )

    async def start_in_progress_for_user(self, user_id: str) -> None:
        if not self.apns.is_configured():
            return
        now = datetime.now(timezone.utc)
        snapshots = self.listener_repo.list_in_progress_timed_snapshots(user_id, now)
        for snapshot in snapshots:
            await self._try_start_snapshot(user_id, snapshot, now=now)

    async def handle_change(self, event: CalendarChangeEvent) -> None:
        if not self.apns.is_configured():
            return
        if not self.repository.get_push_to_start_token(event.user_id):
            return
        now = datetime.now(timezone.utc)
        kind = event.kind
        if kind == CHANGE_EVENT_START:
            if _is_timed_open(event.snapshot, now):
                await self._try_start_snapshot(event.user_id, event.snapshot, now=now)
            return
        if kind in {CHANGE_CREATED, CHANGE_UPDATED}:
            await self._handle_upsert(event.user_id, event.snapshot, now)
            return
        if kind in {CHANGE_CANCELLED, CHANGE_EVENT_END}:
            await self._end_event(event.user_id, event.google_event_id, event.snapshot, now)
            await self._fill_cap(event.user_id, now)

    async def _handle_upsert(
        self, user_id: str, snapshot: Dict[str, Any], now: datetime
    ) -> None:
        event_id = snapshot.get("google_event_id")
        if not isinstance(event_id, str) or not event_id:
            return
        if _is_in_progress(snapshot, now):
            instance = self.repository.get_started_instance(user_id, event_id)
            if instance:
                await self._update_instance(instance, snapshot)
            else:
                await self._try_start_snapshot(user_id, snapshot, now=now)
            return
        await self._end_event(user_id, event_id, snapshot, now)
        await self._fill_cap(user_id, now)

    async def _try_start_snapshot(
        self, user_id: str, snapshot: Dict[str, Any], *, now: datetime
    ) -> bool:
        event_id = snapshot.get("google_event_id")
        end_at = parse_iso(snapshot.get("end_at"))
        if not isinstance(event_id, str) or not event_id or end_at is None:
            return False
        if not _is_timed_open(snapshot, now):
            return False
        if self.repository.has_dismissal(user_id, event_id):
            return False
        if self.repository.get_started_instance(user_id, event_id):
            return False
        started = self.repository.list_started_instances(user_id)
        if len(started) >= MAX_CONCURRENT_ACTIVITIES:
            return False
        token = self.repository.get_push_to_start_token(user_id)
        if not token:
            return False
        title = _event_title(snapshot)
        try:
            row = self.repository.try_insert_started_instance(
                user_id=user_id,
                event_id=event_id,
                title=title,
                end_at=end_at,
            )
        except SupabaseStorageError as exc:
            logger.error(
                "Failed to record live activity user_id=%s event_id=%s: %s",
                user_id,
                event_id,
                exc,
            )
            return False
        if not row:
            return False
        try:
            await self.apns.start_live_activity(
                push_to_start_token=token,
                event_id=event_id,
                title=title,
                end_at=end_at,
            )
        except APNsError as exc:
            logger.error(
                "Failed to start live activity user_id=%s event_id=%s: %s",
                user_id,
                event_id,
                exc,
            )
            instance_id = row.get("id")
            if isinstance(instance_id, str):
                self.repository.mark_ended(instance_id)
            return False
        return True

    async def _update_instance(self, instance: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
        instance_id = instance.get("id")
        end_at = parse_iso(snapshot.get("end_at"))
        if not isinstance(instance_id, str) or end_at is None:
            return
        title = _event_title(snapshot)
        previous_title = instance.get("title")
        previous_end = parse_iso(instance.get("end_at"))
        if previous_title == title and previous_end == end_at:
            return
        self.repository.update_started_instance(instance_id, title=title, end_at=end_at)
        activity_token = instance.get("activity_push_token")
        if not isinstance(activity_token, str) or not activity_token:
            return
        try:
            await self.apns.update_live_activity(
                activity_push_token=activity_token,
                title=title,
                end_at=end_at,
            )
        except APNsError as exc:
            logger.error(
                "Failed to update live activity event_id=%s: %s",
                instance.get("event_id"),
                exc,
            )

    async def _end_event(
        self,
        user_id: str,
        event_id: str,
        snapshot: Dict[str, Any],
        now: datetime,
    ) -> None:
        instance = self.repository.get_started_instance(user_id, event_id)
        if not instance:
            return
        instance_id = instance.get("id")
        if not isinstance(instance_id, str):
            return
        activity_token = instance.get("activity_push_token")
        if isinstance(activity_token, str) and activity_token:
            try:
                await self.apns.end_live_activity(
                    activity_push_token=activity_token,
                    title=instance.get("title") or _event_title(snapshot),
                    end_at=_parse_instance_end(instance, now),
                )
            except APNsError as exc:
                logger.error(
                    "Failed to end live activity user_id=%s event_id=%s: %s",
                    user_id,
                    event_id,
                    exc,
                )
        self.repository.mark_ended(instance_id)

    async def dismiss_event(self, user_id: str, event_id: str) -> None:
        self.repository.upsert_dismissal(user_id, event_id)
        instance = self.repository.get_started_instance(user_id, event_id)
        if not instance:
            return
        instance_id = instance.get("id")
        if not isinstance(instance_id, str):
            return
        activity_token = instance.get("activity_push_token")
        if isinstance(activity_token, str) and activity_token:
            try:
                await self.apns.end_live_activity(
                    activity_push_token=activity_token,
                    title=instance.get("title") or "Untitled Event",
                    end_at=_parse_instance_end(instance, datetime.now(timezone.utc)),
                )
            except APNsError as exc:
                logger.error(
                    "Failed to end dismissed live activity user_id=%s event_id=%s: %s",
                    user_id,
                    event_id,
                    exc,
                )
        self.repository.mark_ended(instance_id)
        await self._fill_cap(user_id, datetime.now(timezone.utc))

    async def _fill_cap(self, user_id: str, now: datetime) -> None:
        started = self.repository.list_started_instances(user_id)
        if len(started) >= MAX_CONCURRENT_ACTIVITIES:
            return
        started_ids = {row.get("event_id") for row in started}
        snapshots = self.listener_repo.list_in_progress_timed_snapshots(user_id, now)
        for snapshot in snapshots:
            event_id = snapshot.get("google_event_id")
            if event_id in started_ids:
                continue
            started_ok = await self._try_start_snapshot(user_id, snapshot, now=now)
            if started_ok:
                started_ids.add(event_id)
            if len(started_ids) >= MAX_CONCURRENT_ACTIVITIES:
                return
