"""Live Activity push token routes."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

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


class StartedRequest(BaseModel):
    eventId: str = Field(min_length=1)
    title: str = Field(min_length=1)
    endAt: datetime


class DismissedRequest(BaseModel):
    eventId: str = Field(min_length=1)


class StartedResponse(BaseModel):
    didClaim: bool


@router.post("/push-to-start-token", status_code=status.HTTP_204_NO_CONTENT)
async def upsert_push_to_start_token(
    payload: PushToStartTokenRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    service = LiveActivitySyncService()
    try:
        service.upsert_push_to_start_token(current_user.id, payload.token)
        # Do not start in-progress events here. Token upload means the app is
        # running and will Activity.request locally; push-to-start would duplicate.
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


@router.post("/started")
async def record_local_start(
    payload: StartedRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> StartedResponse:
    try:
        did_claim = LiveActivitySyncService().record_local_start(
            current_user.id,
            payload.eventId,
            payload.title,
            payload.endAt,
        )
    except SupabaseStorageError as exc:
        logger.error("Failed to record live activity start user_id=%s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not record Live Activity start.",
        ) from exc
    return StartedResponse(didClaim=did_claim)


@router.post("/dismissed", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_activity(
    payload: DismissedRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    try:
        await LiveActivitySyncService().dismiss_event(current_user.id, payload.eventId)
    except SupabaseStorageError as exc:
        logger.error("Failed to dismiss live activity user_id=%s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not dismiss Live Activity.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
