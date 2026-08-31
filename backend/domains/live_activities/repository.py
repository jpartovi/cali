"""Repository for Live Activity push tokens and instances."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from postgrest import APIError

from db.session import get_service_client
from utils.errors import SupabaseStorageError

logger = logging.getLogger(__name__)


class LiveActivityRepository:
    def upsert_push_to_start_token(self, user_id: str, token: str) -> None:
        client = get_service_client()
        try:
            client.table("live_activity_devices").upsert(
                {
                    "user_id": user_id,
                    "push_to_start_token": token,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="user_id",
            ).execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def delete_push_to_start_token(self, user_id: str) -> None:
        client = get_service_client()
        try:
            client.table("live_activity_devices").delete().eq("user_id", user_id).execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def get_push_to_start_token(self, user_id: str) -> Optional[str]:
        client = get_service_client()
        try:
            result = (
                client.table("live_activity_devices")
                .select("push_to_start_token")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            return None
        token = result.data[0].get("push_to_start_token")
        return token if isinstance(token, str) and token else None

    def list_started_instances(self, user_id: str) -> List[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = (
                client.table("live_activity_instances")
                .select("*")
                .eq("user_id", user_id)
                .eq("status", "started")
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return result.data or []

    def get_started_instance(self, user_id: str, event_id: str) -> Optional[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = (
                client.table("live_activity_instances")
                .select("*")
                .eq("user_id", user_id)
                .eq("event_id", event_id)
                .eq("status", "started")
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            return None
        return result.data[0]

    def try_insert_started_instance(
        self,
        *,
        user_id: str,
        event_id: str,
        title: str,
        end_at: datetime,
    ) -> Optional[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = client.table("live_activity_instances").insert(
                {
                    "user_id": user_id,
                    "event_id": event_id,
                    "title": title,
                    "end_at": end_at.isoformat(),
                    "status": "started",
                }
            ).execute()
        except APIError as exc:
            message = (exc.message or "").lower()
            if "duplicate" in message or "unique" in message:
                return None
            raise SupabaseStorageError(exc.message) from exc
        if not result.data:
            return None
        return result.data[0]

    def update_started_instance(
        self,
        instance_id: str,
        *,
        title: str,
        end_at: datetime,
    ) -> None:
        client = get_service_client()
        try:
            (
                client.table("live_activity_instances")
                .update({"title": title, "end_at": end_at.isoformat()})
                .eq("id", instance_id)
                .eq("status", "started")
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def set_activity_push_token(self, user_id: str, event_id: str, token: str) -> None:
        client = get_service_client()
        try:
            (
                client.table("live_activity_instances")
                .update({"activity_push_token": token})
                .eq("user_id", user_id)
                .eq("event_id", event_id)
                .eq("status", "started")
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def mark_ended(self, instance_id: str) -> None:
        client = get_service_client()
        try:
            (
                client.table("live_activity_instances")
                .update({"status": "ended"})
                .eq("id", instance_id)
                .execute()
            )
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def upsert_dismissal(self, user_id: str, event_id: str) -> None:
        client = get_service_client()
        try:
            client.table("live_activity_dismissals").upsert(
                {"user_id": user_id, "event_id": event_id},
                on_conflict="user_id,event_id",
            ).execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def has_dismissal(self, user_id: str, event_id: str) -> bool:
        client = get_service_client()
        try:
            result = (
                client.table("live_activity_dismissals")
                .select("event_id")
                .eq("user_id", user_id)
                .eq("event_id", event_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            # Missing table or RLS must not block push-to-start.
            logger.warning("live_activity_dismissals lookup failed: %s", exc.message)
            return False
        return bool(result.data)
