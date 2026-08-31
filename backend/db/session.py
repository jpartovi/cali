"""Database session and client factory for Supabase."""

from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache

from supabase import Client, ClientOptions, create_client

from core.config import get_settings

logger = logging.getLogger(__name__)


def _supabase_key_role(key: str) -> str:
    token = (key or "").strip()
    if token.startswith("sb_secret_"):
        return "sb_secret"
    if token.startswith("sb_publishable_") or token.startswith("sb_anon_"):
        return "sb_publishable"
    parts = token.split(".")
    if len(parts) != 3:
        return "unknown"
    padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return "undecodable"
    return str(payload.get("role") or "missing")


@lru_cache
def get_service_client() -> Client:
    """Get cached Supabase service client.

    persist_session/auto_refresh must be off so GoTrue does not replace the
    service-role Authorization header with an anon/user session. That was
    causing RLS 403s on calendar_watches despite a service_role key.
    """
    settings = get_settings()
    key = settings.supabase_service_role_key.strip()
    logger.info("Creating Supabase service client key_role=%s", _supabase_key_role(key))
    # Public ClientOptions is SyncClientOptions and includes `storage`.
    # supabase.lib.client_options.ClientOptions is the base class without it,
    # which makes create_client raise AttributeError.
    client = create_client(
        settings.supabase_url,
        key,
        options=ClientOptions(
            auto_refresh_token=False,
            persist_session=False,
        ),
    )
    client.postgrest.auth(key)
    return client
