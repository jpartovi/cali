"""
Short-lived credentials for live dictation.

The app streams microphone audio straight to a speech-to-text provider over a websocket.
It never holds a provider API key: it asks the backend for a token that is only good for
a few minutes (and only needs to be valid when the socket opens).

To add a provider, add a function here and an entry in STREAMING_PROVIDERS; the app picks
the provider and calls POST /agent/transcription/{provider}/token.
"""

from typing import Awaitable, Callable, TypedDict

import httpx

from core.config import get_settings

TOKEN_TTL_SECONDS = 300


class StreamingToken(TypedDict):
    access_token: str
    expires_in: int


async def deepgram_token() -> StreamingToken:
    """Temporary Deepgram token for wss://api.deepgram.com/v1/listen."""
    api_key = get_settings().deepgram_api_key
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY is not set")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://api.deepgram.com/v1/auth/grant",
            headers={"Authorization": f"Token {api_key}"},
            json={"ttl_seconds": TOKEN_TTL_SECONDS},
        )
        resp.raise_for_status()
        payload = resp.json()
    return {
        "access_token": payload["access_token"],
        "expires_in": int(payload.get("expires_in") or TOKEN_TTL_SECONDS),
    }


async def openai_token() -> StreamingToken:
    """
    Ephemeral key for wss://api.openai.com/v1/realtime, bound to a transcription-only session.
    The session settings (model, audio format) are fixed here, so the app can't change them.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not set")
    session = {
        "type": "transcription",
        "audio": {
            "input": {
                # 24 kHz mono 16-bit PCM, which the app sends base64-encoded
                "format": {"type": "audio/pcm", "rate": 24000},
                "transcription": {"model": settings.openai_transcription_model},
                "noise_reduction": {"type": "near_field"},
                # The live models stream text as audio arrives; the app commits on release
                "turn_detection": None,
            }
        },
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "expires_after": {"anchor": "created_at", "seconds": TOKEN_TTL_SECONDS},
                "session": session,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
    return {"access_token": payload["value"], "expires_in": TOKEN_TTL_SECONDS}


STREAMING_PROVIDERS: dict[str, Callable[[], Awaitable[StreamingToken]]] = {
    "deepgram": deepgram_token,
    "openai": openai_token,
}
