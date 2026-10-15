"""Repository for thumbs up/down ratings on agent results."""

from __future__ import annotations

from typing import Any, Dict

from postgrest import APIError

from db.session import get_service_client
from utils.errors import SupabaseStorageError


class AgentRatingsRepository:
    def upsert(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """One rating per request: rating it again replaces the earlier rating."""
        client = get_service_client()
        try:
            result = client.table("agent_ratings").upsert(row, on_conflict="user_id,request_id").execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
        return (result.data or [{}])[0]

    def delete(self, user_id: str, request_id: str) -> None:
        client = get_service_client()
        try:
            client.table("agent_ratings").delete().eq("user_id", user_id).eq("request_id", request_id).execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc
