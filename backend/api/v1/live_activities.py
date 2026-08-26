"""Live Activity push token and cron routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from core.config import get_settings
from core.dependencies import AuthenticatedUser, get_current_user
from domains.live_activities.service import LiveActivitySyncService
from utils.errors import SupabaseStorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live-activities", tags=["live-activities"])


class PushToStartTokenRequest(BaseModel):
    token: str = Field(min_length=8)


class ActivityTokenRequest(BaseModel):
    eventId: str = Field(min_length=1)
    token: str = Field(min_length=8)


def _require_cron_secret(x_cron_secret: str | None) -> None:
    settings = get_settings()
    if not settings.cron_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cron secret is not configured.",
        )
    if not x_cron_secret or x_cron_secret != settings.cron_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid cron secret.",
        )


@router.post("/push-to-start-token", status_code=status.HTTP_204_NO_CONTENT)
async def upsert_push_to_start_token(
    payload: PushToStartTokenRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    try:
        LiveActivitySyncService().upsert_push_to_start_token(current_user.id, payload.token)
    except SupabaseStorageError as exc:
        logger.error("Failed to store push-to-start token user_id=%s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not store Live Activity token.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/push-to-start-token", status_code=status.HTTP_204_NO_CONTENT)
async def delete_push_to_start_token(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    try:
        LiveActivitySyncService().delete_push_to_start_token(current_user.id)
    except SupabaseStorageError as exc:
        logger.error("Failed to delete push-to-start token user_id=%s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete Live Activity token.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/activity-token", status_code=status.HTTP_204_NO_CONTENT)
async def upsert_activity_token(
    payload: ActivityTokenRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    try:
        LiveActivitySyncService().set_activity_push_token(
            current_user.id, payload.eventId, payload.token
        )
    except SupabaseStorageError as exc:
        logger.error("Failed to store activity token user_id=%s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not store Live Activity token.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sync")
async def sync_live_activities(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> dict[str, int]:
    _require_cron_secret(x_cron_secret)
    try:
        return await LiveActivitySyncService().sync_all()
    except Exception as exc:
        logger.error("Live activity sync failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Live activity sync failed.",
        ) from exc
