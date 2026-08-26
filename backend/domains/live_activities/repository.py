"""Repository for Live Activity push tokens and instances."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from postgrest import APIError

from db.session import get_service_client
from utils.errors import SupabaseStorageError


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

    def list_devices(self) -> List[Dict[str, Any]]:
        client = get_service_client()
        try:
            result = client.table("live_activity_devices").select("user_id, push_to_start_token").execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return result.data or []

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

    def insert_started_instance(
        self,
        *,
        user_id: str,
        event_id: str,
        title: str,
        end_at: datetime,
    ) -> None:
        client = get_service_client()
        try:
            client.table("live_activity_instances").insert(
                {
                    "user_id": user_id,
                    "event_id": event_id,
                    "title": title,
                    "end_at": end_at.isoformat(),
                    "status": "started",
                }
            ).execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def set_activity_push_token(self, user_id: str, event_id: str, token: str) -> None:
        client = get_service_client()
        try:
            client.table("live_activity_instances").update(
                {"activity_push_token": token}
            ).eq("user_id", user_id).eq("event_id", event_id).eq("status", "started").execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

    def mark_ended(self, instance_id: str) -> None:
        client = get_service_client()
        try:
            client.table("live_activity_instances").update(
                {"status": "ended"}
            ).eq("id", instance_id).execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
