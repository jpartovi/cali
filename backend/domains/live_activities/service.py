"""Start and end Live Activities from Google Calendar events."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

from domains.calendars.service import CalendarService, _localize_event_time
from domains.live_activities.apns import APNsClient, APNsError
from domains.live_activities.repository import LiveActivityRepository
from utils.errors import (
    GoogleCalendarAuthError,
    GoogleCalendarServiceError,
    GoogleCalendarUserError,
    SupabaseStorageError,
)

logger = logging.getLogger(__name__)

MAX_CONCURRENT_ACTIVITIES = 8


def _parse_timed_event(event: Dict[str, Any]) -> Tuple[str, str, datetime, datetime] | None:
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id:
        return None
    start_payload = event.get("start") or {}
    end_payload = event.get("end") or {}
    if not isinstance(start_payload, dict) or not isinstance(end_payload, dict):
        return None
    if "dateTime" not in start_payload or "dateTime" not in end_payload:
        return None
    try:
        start_dt, start_all_day = _localize_event_time(start_payload, ZoneInfo("UTC"))
        end_dt, end_all_day = _localize_event_time(end_payload, ZoneInfo("UTC"))
    except Exception:
        return None
    if start_all_day or end_all_day or start_dt >= end_dt:
        return None
    raw_title = (event.get("summary") or "").strip()
    title = raw_title if raw_title else "Untitled Event"
    return event_id, title, start_dt.astimezone(timezone.utc), end_dt.astimezone(timezone.utc)


class LiveActivitySyncService:
    def __init__(
        self,
        repository: LiveActivityRepository | None = None,
        calendar_service: CalendarService | None = None,
        apns: APNsClient | None = None,
    ) -> None:
        self.repository = repository or LiveActivityRepository()
        self.calendar_service = calendar_service or CalendarService()
        self.apns = apns or APNsClient()

    def upsert_push_to_start_token(self, user_id: str, token: str) -> None:
        self.repository.upsert_push_to_start_token(user_id, token)

    def delete_push_to_start_token(self, user_id: str) -> None:
        self.repository.delete_push_to_start_token(user_id)

    def set_activity_push_token(self, user_id: str, event_id: str, token: str) -> None:
        self.repository.set_activity_push_token(user_id, event_id, token)

    async def sync_all(self) -> Dict[str, int]:
        if not self.apns.is_configured():
            logger.warning("Skipping live activity sync: APNs is not configured")
            return {"users": 0, "started": 0, "ended": 0, "skipped": 0}

        devices = self.repository.list_devices()
        started = 0
        ended = 0
        skipped = 0
        for device in devices:
            user_id = device.get("user_id")
            token = device.get("push_to_start_token")
            if not user_id or not token:
                continue
            try:
                result = await self._sync_user(user_id, token)
            except (GoogleCalendarUserError, GoogleCalendarAuthError, GoogleCalendarServiceError) as exc:
                skipped += 1
                logger.warning("Skipping live activity sync user_id=%s: %s", user_id, exc)
                continue
            except SupabaseStorageError as exc:
                skipped += 1
                logger.error("Live activity storage error user_id=%s: %s", user_id, exc)
                continue
            started += result["started"]
            ended += result["ended"]
        return {"users": len(devices), "started": started, "ended": ended, "skipped": skipped}

    async def _sync_user(self, user_id: str, push_to_start_token: str) -> Dict[str, int]:
        today = datetime.now(timezone.utc).date()
        schedule = await self.calendar_service.events_for_date_range(
            user_id=user_id,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
            timezone_name="UTC",
        )
        now = datetime.now(timezone.utc)
        timed: List[Tuple[str, str, datetime, datetime]] = []
        for event in schedule.get("events") or []:
            parsed = _parse_timed_event(event)
            if parsed is None:
                continue
            event_id, title, start_at, end_at = parsed
            if start_at <= now < end_at:
                timed.append((event_id, title, start_at, end_at))
        timed.sort(key=lambda item: item[2])
        active = timed[:MAX_CONCURRENT_ACTIVITIES]
        active_ids = {item[0] for item in active}

        started_rows = self.repository.list_started_instances(user_id)
        started_by_event = {row["event_id"]: row for row in started_rows}

        started_count = 0
        for event_id, title, _, end_at in active:
            if event_id in started_by_event:
                continue
            try:
                await self.apns.start_live_activity(
                    push_to_start_token=push_to_start_token,
                    event_id=event_id,
                    title=title,
                    end_at=end_at,
                )
            except APNsError as exc:
                logger.error("Failed to start live activity user_id=%s event_id=%s: %s", user_id, event_id, exc)
                continue
            try:
                self.repository.insert_started_instance(
                    user_id=user_id,
                    event_id=event_id,
                    title=title,
                    end_at=end_at,
                )
            except SupabaseStorageError as exc:
                logger.error("Failed to record live activity instance user_id=%s event_id=%s: %s", user_id, event_id, exc)
                continue
            started_count += 1

        ended_count = 0
        for row in started_rows:
            event_id = row.get("event_id")
            instance_id = row.get("id")
            if not event_id or not instance_id:
                continue
            should_end = event_id not in active_ids
            if not should_end:
                continue
            activity_token = row.get("activity_push_token")
            if activity_token:
                try:
                    end_at = row.get("end_at")
                    if isinstance(end_at, str):
                        end_dt = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
                    elif isinstance(end_at, datetime):
                        end_dt = end_at
                    else:
                        end_dt = now
                    await self.apns.end_live_activity(
                        activity_push_token=activity_token,
                        title=row.get("title") or "Untitled Event",
                        end_at=end_dt,
                    )
                except APNsError as exc:
                    logger.error("Failed to end live activity user_id=%s event_id=%s: %s", user_id, event_id, exc)
            self.repository.mark_ended(instance_id)
            ended_count += 1

        return {"started": started_count, "ended": ended_count}
