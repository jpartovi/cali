"""Thumbs up/down on an agent result, saved with the calendar as it was when rated."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Literal, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core.dependencies import AuthenticatedUser, get_current_user, get_user_timezone
from domains.agent_ratings.repository import AgentRatingsRepository
from services.event_finder import snapshot_events_for_user
from utils.errors import SupabaseStorageError

router = APIRouter(prefix="/agent/ratings", tags=["agent"])
logger = logging.getLogger(__name__)

# The calendar listener's horizon: yesterday through two weeks out
SNAPSHOT_DAYS_BACK = 1
SNAPSHOT_DAYS_AHEAD = 14


class AgentRatingRequest(BaseModel):
    # The agent run's request_id (its LangSmith trace id)
    request_id: UUID
    rating: Literal["good", "bad"]
    # What went wrong (or right); sent after the rating, so omitting it keeps an earlier note
    note: Optional[str] = Field(default=None, max_length=4000)
    query: Optional[str] = Field(default=None, max_length=4000)
    response: Optional[Dict[str, Any]] = None
    app_version: Optional[str] = Field(default=None, max_length=64)
    app_build: Optional[str] = Field(default=None, max_length=64)


class AgentRatingResponse(BaseModel):
    id: str


def _calendar_snapshot(user_id: str) -> Optional[Dict[str, Any]]:
    """The user's events around now, in the same format the agent's pre-pass sees them."""
    try:
        timezone_name = get_user_timezone(user_id)
        today = datetime.now(ZoneInfo(timezone_name)).date()
        start, end = today - timedelta(days=SNAPSHOT_DAYS_BACK), today + timedelta(days=SNAPSHOT_DAYS_AHEAD)
        events = snapshot_events_for_user(user_id, start, end, timezone_name)
    except Exception as exc:
        # A rating without its calendar is still worth keeping
        logger.warning("Calendar snapshot for rating failed user_id=%s: %s", user_id, exc)
        return None
    return {"timezone": timezone_name, "start_date": start.isoformat(), "end_date": end.isoformat(), "events": events}


@router.post("", response_model=AgentRatingResponse, status_code=status.HTTP_201_CREATED)
async def rate_agent_result(
    payload: AgentRatingRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AgentRatingResponse:
    row = payload.model_dump(mode="json")
    if not (payload.note or "").strip():
        row.pop("note")
    row["user_id"] = current_user.id
    row["calendar_snapshot"] = await asyncio.to_thread(_calendar_snapshot, current_user.id)
    try:
        saved = await asyncio.to_thread(AgentRatingsRepository().upsert, row)
    except SupabaseStorageError as exc:
        logger.error("Agent rating save failed user_id=%s: %s", current_user.id, exc)
        raise HTTPException(status_code=500, detail="Couldn't save rating. Please try again.")
    logger.info("Agent rated user_id=%s request_id=%s rating=%s", current_user.id, payload.request_id, payload.rating)
    return AgentRatingResponse(id=str(saved.get("id", "")))


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_agent_rating(
    request_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> None:
    """Deselecting a thumb removes the rating (and any note with it)."""
    try:
        await asyncio.to_thread(AgentRatingsRepository().delete, current_user.id, str(request_id))
    except SupabaseStorageError as exc:
        logger.error("Agent rating removal failed user_id=%s: %s", current_user.id, exc)
        raise HTTPException(status_code=500, detail="Couldn't remove rating. Please try again.")
    logger.info("Agent rating removed user_id=%s request_id=%s", current_user.id, request_id)
