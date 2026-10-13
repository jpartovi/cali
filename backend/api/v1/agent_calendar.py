"""Calendar API endpoints for the agent.

These endpoints are specifically designed for use by the LangGraph agent
and return data in the format expected by agent tools.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import AuthenticatedUser, get_current_user, get_user_timezone
from domains.calendars.service import CalendarService
from domains.calendars.schemas import ScheduleRequest
from services import agent_calendar_service
from utils.errors import (
    GoogleCalendarUserError,
    GoogleCalendarAuthError,
    GoogleCalendarServiceError,
    GoogleCalendarEventNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent/calendars", tags=["agent-calendars"])


def _parse_datetime_or_date(dt_str: str) -> datetime:
    """Parse ISO datetime string, handling both date-only and datetime formats."""
    try:
        # Handle Z suffix and timezone offsets
        normalized = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except Exception as e:
        logger.error(f"Failed to parse datetime: {dt_str} - {e}")
        raise ValueError(f"Invalid datetime format: {dt_str}") from e


def _to_user_tz(value: Any, tz: ZoneInfo) -> Any:
    """Express an event start/end in the user's timezone.

    Google returns each event in its own calendar's timezone, so without this the
    agent sees mixed offsets (e.g. -07:00 next to -04:00) and compares clock times
    across zones. All-day dates are left as they are.
    """
    if isinstance(value, dict):
        if value.get("dateTime"):
            local = _parse_datetime_or_date(value["dateTime"]).astimezone(tz)
            return {**value, "dateTime": local.isoformat(), "timeZone": tz.key}
        return value
    if isinstance(value, str) and len(value) > 10:
        return _parse_datetime_or_date(value).astimezone(tz).isoformat()
    return value


def _localize_event(event: Dict[str, Any], tz: ZoneInfo) -> Dict[str, Any]:
    """Copy of an event with start and end in the user's timezone."""
    return {**event, "start": _to_user_tz(event.get("start"), tz), "end": _to_user_tz(event.get("end"), tz)}


@router.get("")
async def list_calendars(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    List calendars from ALL connected Google accounts with write permissions.
    
    Only returns calendars where the user has "writer" or "owner" access role.
    This prevents the agent from selecting read-only calendars for create/update/delete operations.
    
    Returns calendars in format expected by agent tools.
    """
    from domains.calendars.repository import CalendarRepository
    
    try:
        repository = CalendarRepository()
        
        # Get all calendars from repository (these are already synced from all accounts)
        user_calendars = repository.get_calendars(current_user.id)
        
        # Filter to only include calendars with write permissions (writer or owner)
        # This prevents the agent from selecting read-only calendars
        writable_access_roles = {"writer", "owner"}
        
        # Format calendars for agent - only include writable calendars
        formatted_calendars = []
        for cal in user_calendars:
            access_role = cal.get("access_role")
            # Only include calendars with write permissions
            if access_role not in writable_access_roles:
                continue
                
            formatted_calendars.append({
                "id": cal.get("google_calendar_id"),
                "name": cal.get("name"),
                "summary": cal.get("name"),
                "description": cal.get("description"),
                "timezone": "UTC",  # Calendar timezone can vary
                "color": cal.get("color"),
                "is_primary": cal.get("is_primary", False),
                "access_role": access_role,
            })
        
        return {"calendars": formatted_calendars}
        
    except Exception as e:
        # Log full error details for debugging (verbose internal logging)
        logger.error(
            f"Failed to list calendars user_id={current_user.id}: {e}",
            exc_info=True,
        )
        # Return brief, user-friendly message (not technical details)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while loading your calendars. Please try again."
        ) from e


@router.post("/schedule")
async def get_schedule(
    payload: Dict[str, Any],
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Read schedule across ALL calendars within a date range.
    
    Expects JSON body with:
    - start_date: ISO date string (e.g., "2026-01-14")
    - end_date: ISO date string (e.g., "2026-01-15")
    
    Returns events from ALL connected Google calendars.
    All events include both id and calendar_id (required for agent tools).
    """
    from datetime import date
    
    try:
        start_date_str = payload.get("start_date")
        end_date_str = payload.get("end_date")
        
        if not start_date_str or not end_date_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_date and end_date are required"
            )
        
        # Parse dates
        start_date = date.fromisoformat(start_date_str)
        end_date = date.fromisoformat(end_date_str)
        
        # Get user timezone
        user_timezone = get_user_timezone(current_user.id)
        
        # Use CalendarService which aggregates across ALL calendars
        service = CalendarService()
        try:
            result = await service.events_for_date_range(
            user_id=current_user.id,
            start_date=start_date,
            end_date=end_date,
            timezone_name=user_timezone,
            )
        except Exception as e:
            # Re-raise HTTPExceptions, but wrap other errors
            if isinstance(e, HTTPException):
                raise
            logger.error(f"Error in events_for_date_range: {e}", exc_info=True)
            raise
        
        # Format events for agent tools - ensure both id and calendar_id are present
        formatted_events = []
        for event in result.get("events", []):
            event_id = event.get("id")
            calendar_id = event.get("calendar_id")
            
            if not event_id or not calendar_id:
                logger.warning(f"Event missing required fields (id, calendar_id): {event}")
                continue
            
            formatted_events.append({
                "id": event_id,
                "summary": event.get("summary"),
                "description": event.get("description"),
                "start": event.get("start"),
                "end": event.get("end"),
                "calendar_id": calendar_id,
                "calendar_name": event.get("calendar_name"),
                "location": event.get("raw", {}).get("location"),
                "status": event.get("status"),
            })
        
        user_tz = ZoneInfo(user_timezone)
        return {"events": [_localize_event(e, user_tz) for e in formatted_events]}
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid date format: {str(e)}"
        ) from e
    except GoogleCalendarUserError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except GoogleCalendarAuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    except GoogleCalendarServiceError as e:
        logger.exception(
            f"Calendar service error user_id={current_user.id}",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e
    except Exception as e:
        # Log full error details for debugging (verbose internal logging)
        logger.exception(
            f"Unexpected error getting schedule user_id={current_user.id}: {e}",
        )
        # Return brief, user-friendly message (not technical details)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while loading your schedule. Please try again."
        ) from e


@router.post("/search")
async def search_events(
    payload: Dict[str, Any],
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Search for events across ALL calendars matching keywords.
    
    Expects JSON body with:
    - keywords: Search query string
    - start_time: ISO datetime string (timezone-aware)
    - end_time: ISO datetime string (timezone-aware)
    
    Returns events from ALL connected Google calendars.
    All events include both id (as event_id) and calendar_id (required for agent tools).
    """
    try:
        keywords = payload.get("keywords", "")
        start_time_str = payload.get("start_time")
        end_time_str = payload.get("end_time")
        
        if not keywords:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="keywords is required"
            )
        
        if not start_time_str or not end_time_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_time and end_time are required"
            )
        
        # Parse datetime strings
        start_time = _parse_datetime_or_date(start_time_str)
        end_time = _parse_datetime_or_date(end_time_str)
        
        # Convert to ISO strings for the search function
        # The search_events_for_user expects ISO strings
        start_iso = start_time.isoformat()
        end_iso = end_time.isoformat()
        
        # Use search_events_for_user which searches across ALL calendars
        result = await agent_calendar_service.search_events_for_user(
            user_id=current_user.id,
            query=keywords,
            time_min=start_iso,
            time_max=end_iso,
        )
        
        # Format events for agent tools - ensure both id and calendar_id are present
        formatted_events = []
        events = result.get("events", [])
        
        for event in events:
            event_id = event.get("event_id") or event.get("id")
            calendar_id = event.get("calendar_id")
            
            if not event_id or not calendar_id:
                logger.warning(f"Event missing required fields (id, calendar_id): {event}")
                continue
            
            formatted_events.append({
                "id": event_id,
                "event_id": event_id,  # Include both for compatibility
                "summary": event.get("summary"),
                "description": event.get("description"),
                "start": event.get("start"),
                "end": event.get("end"),
                "calendar_id": calendar_id,
                "calendar_name": event.get("calendar_name"),
                "location": event.get("location"),
            })
        
        user_tz = ZoneInfo(get_user_timezone(current_user.id))
        return {"events": [_localize_event(e, user_tz) for e in formatted_events]}
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid datetime format: {str(e)}"
        ) from e
    except Exception as e:
        # Log full error details for debugging (verbose internal logging)
        logger.exception(
            f"Unexpected error searching events user_id={current_user.id}: {e}",
        )
        # Return brief, user-friendly message (not technical details)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while searching for events. Please try again."
        ) from e


@router.post("/find")
async def find_event(
    payload: Dict[str, Any],
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Find the one event a request refers to, using TypeSafe Jev.

    Expects JSON body with:
    - description: what the user said about the event (e.g. "my frisbee game")
    - start_time / end_time: ISO datetimes bounding where to look

    Returns status "match" (with event), "ambiguous" (with candidates and
    probabilities), "none", or "too_many" (range holds more events than Jev can
    choose between; narrow it).
    """
    from services.event_finder import EventFinderNotConfigured, find_event_for_user
    from typesafe_sdk import TypeSafeError

    try:
        description = (payload.get("description") or "").strip()
        start_time_str = payload.get("start_time")
        end_time_str = payload.get("end_time")
        if not description:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="description is required")
        if not start_time_str or not end_time_str:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start_time and end_time are required")

        user_timezone = get_user_timezone(current_user.id)
        tz = ZoneInfo(user_timezone)
        start_time = _parse_datetime_or_date(start_time_str)
        end_time = _parse_datetime_or_date(end_time_str)
        # Whole days in the user's timezone, matching the schedule endpoint
        start_date = (start_time.astimezone(tz) if start_time.tzinfo else start_time).date()
        end_date = (end_time.astimezone(tz) if end_time.tzinfo else end_time).date()

        result = await find_event_for_user(
            user_id=current_user.id,
            description=description,
            start_date=start_date,
            end_date=end_date,
            timezone_name=user_timezone,
        )
        if result.get("event"):
            result["event"] = _localize_event(result["event"], tz)
        result["candidates"] = [_localize_event(c, tz) for c in result.get("candidates", [])]
        return result
    except HTTPException:
        raise
    except EventFinderNotConfigured as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Event finder is not configured") from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid datetime format: {str(e)}") from e
    except GoogleCalendarAuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    except TypeSafeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Event finder is unavailable. Please try again.") from e
    except Exception as e:
        logger.exception(f"Unexpected error finding event user_id={current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while finding the event. Please try again.",
        ) from e


@router.post("/free")
async def find_free_time(
    payload: Dict[str, Any],
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Find free blocks where an event of a given length fits.

    Expects JSON body with:
    - start_time / end_time: ISO datetimes bounding where to look
    - duration_minutes: length of the event to fit
    - buffer_minutes: time to keep clear before and after it (default 0)
    - earliest_time / latest_time: hours of each day to consider, "HH:MM" (default 05:00-24:00)

    Returns {blocks: [{start, end, minutes, earliest_start, latest_end, before, after}], block_count}.
    """
    from services.free_time import DEFAULT_EARLIEST, DEFAULT_LATEST, find_free_time_for_user, parse_time_of_day

    try:
        start_time_str = payload.get("start_time")
        end_time_str = payload.get("end_time")
        if not start_time_str or not end_time_str:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start_time and end_time are required")
        duration_minutes = int(payload.get("duration_minutes") or 0)
        buffer_minutes = int(payload.get("buffer_minutes") or 0)
        if duration_minutes <= 0 or buffer_minutes < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="duration_minutes must be positive and buffer_minutes not negative")
        earliest = parse_time_of_day(payload["earliest_time"]) if payload.get("earliest_time") else DEFAULT_EARLIEST
        latest = parse_time_of_day(payload["latest_time"]) if payload.get("latest_time") else DEFAULT_LATEST
        if earliest is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="earliest_time can't be 24:00")

        user_timezone = get_user_timezone(current_user.id)
        tz = ZoneInfo(user_timezone)
        start_time = _parse_datetime_or_date(start_time_str)
        end_time = _parse_datetime_or_date(end_time_str)
        start_time = start_time.astimezone(tz) if start_time.tzinfo else start_time.replace(tzinfo=tz)
        end_time = end_time.astimezone(tz) if end_time.tzinfo else end_time.replace(tzinfo=tz)
        if end_time <= start_time:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_time must be after start_time")

        return await find_free_time_for_user(
            current_user.id,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            buffer_minutes=buffer_minutes,
            timezone_name=user_timezone,
            earliest=earliest,
            latest=latest,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid input: {str(e)}") from e
    except GoogleCalendarAuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    except Exception as e:
        logger.exception(f"Unexpected error finding free time user_id={current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while finding free time. Please try again.",
        ) from e


@router.get("/{calendar_id}/events/{event_id}")
async def get_event(
    calendar_id: str,
    event_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Get a single event from a specific calendar.
    
    Returns event details with both id and calendar_id (required for agent tools).
    """
    service = CalendarService()
    
    try:
        # Use CalendarService to get the event
        event = await service.get_event(
            user_id=current_user.id,
            calendar_id=calendar_id,
            event_id=event_id,
        )
        
        # Ensure both id and calendar_id are present (required)
        if "id" not in event:
            event["id"] = event_id
        if "calendar_id" not in event:
            event["calendar_id"] = calendar_id
        
        return _localize_event(event, ZoneInfo(get_user_timezone(current_user.id)))
        
    except GoogleCalendarEventNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        ) from e
    except GoogleCalendarUserError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except GoogleCalendarAuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    except GoogleCalendarServiceError as e:
        logger.exception(
            f"Calendar service error user_id={current_user.id} calendar={calendar_id} event={event_id}",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e
    except Exception as e:
        # Log full error details for debugging (verbose internal logging)
        logger.exception(
            f"Unexpected error getting event user_id={current_user.id} calendar={calendar_id} event={event_id}: {e}",
        )
        # Return brief, user-friendly message (not technical details)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while loading the event. Please try again."
        ) from e
