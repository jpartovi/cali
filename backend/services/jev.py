"""Shared TypeSafe Jev client.

Every Jev request used to open its own client, paying for a fresh connection each
time. One client per process keeps the connection warm across requests.
"""

from __future__ import annotations

from typing import Optional

from typesafe_sdk import AsyncTypeSafeClient

from core.config import get_settings

_client: Optional[AsyncTypeSafeClient] = None


def get_jev_client() -> AsyncTypeSafeClient:
    """The process-wide Jev client (created on first use; requires TYPESAFE_API_KEY)."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncTypeSafeClient(api_key=settings.typesafe_api_key, model=settings.typesafe_model)
    return _client
