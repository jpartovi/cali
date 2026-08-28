"""APNs client for Live Activity start/end pushes."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx
import jwt

from core.config import get_settings

logger = logging.getLogger(__name__)

_MAX_JWT_AGE_SECONDS = 50 * 60


class APNsError(Exception):
    """Raised when an APNs request fails."""


class APNsClient:
    def __init__(self) -> None:
        self._jwt: str | None = None
        self._jwt_issued_at: float = 0

    def is_configured(self) -> bool:
        settings = get_settings()
        return bool(settings.apns_key_p8 and settings.apns_key_id and settings.apns_team_id)

    def _token(self) -> str:
        settings = get_settings()
        now = time.time()
        if self._jwt and now - self._jwt_issued_at < _MAX_JWT_AGE_SECONDS:
            return self._jwt
        if not settings.apns_key_p8 or not settings.apns_key_id or not settings.apns_team_id:
            raise APNsError("APNs is not configured.")

        issued_at = int(now)
        self._jwt = jwt.encode(
            {"iss": settings.apns_team_id, "iat": issued_at},
            settings.apns_key_p8.replace("\\n", "\n"),
            algorithm="ES256",
            headers={"kid": settings.apns_key_id, "typ": "JWT"},
        )
        self._jwt_issued_at = now
        return self._jwt

    def _host(self) -> str:
        environment = (get_settings().apns_environment or "sandbox").lower()
        if environment == "production":
            return "https://api.push.apple.com"
        return "https://api.sandbox.push.apple.com"

    async def _send(self, *, device_token: str, payload: dict[str, Any], priority: str) -> None:
        settings = get_settings()
        topic = f"{settings.apns_bundle_id}.push-type.liveactivity"
        url = f"{self._host()}/3/device/{device_token}"
        headers = {
            "authorization": f"bearer {self._token()}",
            "apns-topic": topic,
            "apns-push-type": "liveactivity",
            "apns-priority": priority,
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(http2=True, timeout=15.0) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 300:
            logger.error(
                "APNs error status=%s body=%s token_suffix=%s",
                response.status_code,
                response.text,
                device_token[-8:],
            )
            raise APNsError(f"APNs rejected push ({response.status_code})")

    async def start_live_activity(
        self,
        *,
        push_to_start_token: str,
        event_id: str,
        title: str,
        end_at: datetime,
    ) -> None:
        timestamp = int(time.time())
        end_unix = int(end_at.timestamp())
        payload = {
            "aps": {
                "timestamp": timestamp,
                "event": "start",
                "attributes-type": "EventActivityAttributes",
                "attributes": {"eventId": event_id},
                "content-state": {
                    "title": title,
                    "endDate": end_unix,
                },
                "stale-date": end_unix,
                "dismissal-date": end_unix,
                "alert": {
                    "title": title,
                    "body": "Now",
                },
            }
        }
        await self._send(device_token=push_to_start_token, payload=payload, priority="10")

    async def update_live_activity(
        self,
        *,
        activity_push_token: str,
        title: str,
        end_at: datetime,
    ) -> None:
        timestamp = int(time.time())
        end_unix = int(end_at.timestamp())
        payload = {
            "aps": {
                "timestamp": timestamp,
                "event": "update",
                "content-state": {
                    "title": title,
                    "endDate": end_unix,
                },
                "stale-date": end_unix,
                "dismissal-date": end_unix,
            }
        }
        await self._send(device_token=activity_push_token, payload=payload, priority="10")

    async def end_live_activity(
        self,
        *,
        activity_push_token: str,
        title: str,
        end_at: datetime,
    ) -> None:
        timestamp = int(time.time())
        payload = {
            "aps": {
                "timestamp": timestamp,
                "event": "end",
                "dismissal-date": timestamp,
                "content-state": {
                    "title": title,
                    "endDate": int(end_at.timestamp()),
                },
            }
        }
        await self._send(device_token=activity_push_token, payload=payload, priority="10")
