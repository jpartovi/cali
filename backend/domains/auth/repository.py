"""Repository for authentication-related database operations."""

from __future__ import annotations

import hmac
import logging
import secrets
from typing import Any, Dict, Tuple

import httpx
from postgrest import APIError

from core.config import get_settings
from core.user_cache import cache_user, get_cached_user
from db.session import get_service_client
from utils.errors import SupabaseAuthError, SupabaseStorageError

logger = logging.getLogger(__name__)


def _model_dump(obj: Any) -> Dict[str, Any]:
    """Helper to convert object to dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump()
    dict_fn = getattr(obj, "dict", None)
    if callable(dict_fn):
        return dict_fn()
    return obj.__dict__  # type: ignore[attr-defined]


class AuthRepository:
    """Repository for authentication database operations."""

    def send_phone_otp(self, phone: str) -> None:
        """Send phone OTP."""
        if _dev_otp_code():
            logger.warning("Skipping SMS OTP because AUTH_DEV_OTP_CODE is set")
            return

        client = get_service_client()
        try:
            client.auth.sign_in_with_otp({"phone": phone})
        except Exception as exc:  # pragma: no cover - supabase raises dynamic errors
            raise SupabaseAuthError(str(exc)) from exc

    def verify_phone_otp(self, phone: str, token: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Verify phone OTP and return session and user."""
        dev_code = _dev_otp_code()
        if dev_code:
            if not hmac.compare_digest(token.encode(), dev_code.encode()):
                raise SupabaseAuthError("Invalid OTP code")
            return self._dev_phone_session(phone)

        client = get_service_client()
        try:
            response = client.auth.verify_otp(
                {"phone": phone, "token": token, "type": "sms"}
            )
        except Exception as exc:  # pragma: no cover
            raise SupabaseAuthError(str(exc)) from exc

        session = _model_dump(getattr(response, "session", None))
        user = _model_dump(getattr(response, "user", None))

        if not session or not user:
            raise SupabaseAuthError("Supabase did not return a valid session or user.")

        return session, user

    def _dev_phone_session(self, phone: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Issue a real Supabase session without sending an SMS."""
        client = get_service_client()
        password = secrets.token_urlsafe(24)
        try:
            existing = _find_user_by_phone(client, phone)
            if existing is None:
                created = client.auth.admin.create_user(
                    {"phone": phone, "password": password, "phone_confirm": True}
                )
                user_id = created.user.id
            else:
                user_id = existing.id
                client.auth.admin.update_user_by_id(
                    user_id,
                    {"password": password, "phone_confirm": True},
                )
            session, user = _sign_in_with_phone_password(phone, password)
        except SupabaseAuthError:
            raise
        except Exception as exc:  # pragma: no cover - supabase raises dynamic errors
            raise SupabaseAuthError(str(exc)) from exc

        logger.warning("Dev OTP bypass signed in phone %s", _digits(phone))
        return session, user

    def refresh_session(self, refresh_token: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Refresh session using refresh token."""
        client = get_service_client()
        try:
            response = client.auth.refresh_session(refresh_token)
        except Exception as exc:  # pragma: no cover - supabase raises dynamic errors
            raise SupabaseAuthError(str(exc)) from exc

        session = _model_dump(getattr(response, "session", None))
        user = _model_dump(getattr(response, "user", None))

        if not session or not user:
            raise SupabaseAuthError("Supabase did not return a valid session or user.")

        return session, user

    def ensure_user_profile(self, user: Dict[str, Any], phone: str) -> Dict[str, Any]:
        """Ensure user profile exists in database."""
        client = get_service_client()
        payload = {
            "id": user.get("id"),
            "phone": phone,
        }
        try:
            result = client.table("users").upsert(payload, on_conflict="id").execute()
        except APIError as exc:
            raise SupabaseStorageError(exc.message) from exc

        if result.data:
            cache_user(result.data[0])
        return result.data[0] if result.data else payload

    async def get_user_from_token(self, access_token: str) -> Dict[str, Any]:
        """
        Validate JWT access token and retrieve user information from database.

        Args:
            access_token: Supabase JWT access token

        Returns:
            User dictionary with id, phone, created_at, updated_at

        Raises:
            SupabaseAuthError: If token is invalid, expired, or user not found
        """
        from core.config import get_settings
        import jwt

        settings = get_settings()

        if not settings.supabase_jwt_secret:
            raise SupabaseAuthError("JWT secret not configured")

        try:
            # Decode and validate JWT token
            decoded = jwt.decode(
                access_token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
            user_id = decoded.get("sub")
            if not user_id:
                raise SupabaseAuthError("Invalid token: missing user ID")
        except jwt.ExpiredSignatureError:
            raise SupabaseAuthError("Token has expired")
        except jwt.InvalidTokenError as exc:
            raise SupabaseAuthError(f"Invalid token: {str(exc)}") from exc

        # Fetch user from database (a recent copy is reused; the token itself is always verified above)
        cached = get_cached_user(user_id)
        if cached is not None:
            return cached
        client = get_service_client()
        try:
            result = client.table("users").select("*").eq("id", user_id).execute()
        except APIError as exc:
            # Check if the error is related to JWT expiration
            # Supabase may return JWT errors even with service role key if RLS is checking
            error_message = str(exc.message).lower()
            if exc.code == "PGRST303" or "jwt expired" in error_message:
                raise SupabaseAuthError("Token has expired") from exc
            raise SupabaseStorageError(f"Failed to fetch user: {exc.message}") from exc

        if not result.data:
            raise SupabaseAuthError("User not found")

        cache_user(result.data[0])
        return result.data[0]


def _dev_otp_code() -> str | None:
    code = (get_settings().auth_dev_otp_code or "").strip()
    return code or None


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _find_user_by_phone(client: Any, phone: str) -> Any | None:
    target = _digits(phone)
    page = 1
    per_page = 200
    while page <= 20:
        users = client.auth.admin.list_users(page=page, per_page=per_page)
        for user in users:
            if _digits(user.phone or "") == target:
                return user
        if len(users) < per_page:
            return None
        page += 1
    return None


def _sign_in_with_phone_password(phone: str, password: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Password grant on a throwaway request so the service client keeps its key."""
    settings = get_settings()
    key = settings.supabase_service_role_key.strip()
    response = httpx.post(
        f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
        params={"grant_type": "password"},
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={"phone": phone, "password": password},
        timeout=20,
    )
    if response.status_code >= 400:
        raise SupabaseAuthError(f"Dev OTP sign-in failed: {response.text[:300]}")

    payload = response.json()
    session = {
        "access_token": payload.get("access_token"),
        "refresh_token": payload.get("refresh_token"),
        "token_type": payload.get("token_type", "bearer"),
        "expires_in": payload.get("expires_in"),
    }
    user = payload.get("user") or {}
    if not session["access_token"] or not user.get("id"):
        raise SupabaseAuthError("Supabase did not return a valid session or user.")
    return session, user
