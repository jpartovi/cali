"""Service for handling agent calendar operations."""

from __future__ import annotations

import asyncio
import logging
import time as time_module
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from core.timing_logger import log_step, log_start
from domains.calendars.repository import CalendarRepository
from domains.calendars.providers.google import (
    GoogleCalendarAPIError,
    GoogleCalendarCredentials,
    GoogleCalendarWrapper,
)
from domains.calendars.service import CalendarService
from utils.errors import GoogleCalendarAuthError

logger = logging.getLogger(__name__)

# Cap on concurrent per-calendar Google requests during a search, so a user with
# many calendars doesn't open dozens of connections at once.
SEARCH_CONCURRENCY_LIMIT = 8


async def _get_search_credentials(
    user_id: str,
    repository: CalendarRepository,
) -> tuple[Dict[str, Any], GoogleCalendarCredentials]:
    """Return the user's first Google account and credentials with a valid access token.

    Refreshes (and saves) the token up front through CalendarService, the same path the
    schedule uses, so the parallel per-calendar searches never each hit a 401 and
    refresh on their own.
    """
    accounts = repository.get_accounts(user_id)
    if not accounts:
        raise HTTPException(
            status_code=400,
            detail="No Google account linked. Please connect a Google account first."
        )
    
    account = accounts[0]
    if not account.get("access_token") or not account.get("refresh_token"):
        raise HTTPException(
            status_code=401,
            detail="Google account missing access or refresh token."
        )
    
    try:
        access_token = await CalendarService(repository)._ensure_access_token(account)
    except GoogleCalendarAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    
    credentials = GoogleCalendarCredentials(
        access_token=access_token,
        refresh_token=account.get("refresh_token"),
    )
    return account, credentials


async def search_events_for_user(
    user_id: str,
    query: str,
    calendar_id: Optional[str] = None,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 250,
) -> Dict[str, Any]:
    """
    Search for events using a query string across all user calendars.
    
    If calendar_id is specified, searches only that calendar.
    Otherwise, searches across all calendars the user has access to.
    
    Args:
        user_id: User ID to search events for
        query: Free text search query to match against event fields
        calendar_id: Optional calendar ID to search in (if None, searches all calendars)
        time_min: Minimum time for events (ISO 8601 format, optional)
        time_max: Maximum time for events (ISO 8601 format, optional)
        max_results: Maximum number of events to return across all calendars
        
    Returns:
        Dictionary containing search results with events list from all calendars
    """
    repository = CalendarRepository()
    account, search_credentials = await _get_search_credentials(user_id, repository)
    
    # Get user's calendars from Supabase (with hidden calendars filtered out)
    user_calendars = repository.get_calendars(user_id)  # Default include_hidden=False filters hidden calendars
    visible_calendar_ids = {cal["google_calendar_id"] for cal in user_calendars}
    
    try:
        # If a specific calendar is requested, verify it's not hidden
        if calendar_id:
            if calendar_id not in visible_calendar_ids:
                return {
                    "status": "error",
                    "error": f"Calendar {calendar_id} not found or is hidden.",
                    "count": 0,
                    "events": [],
                }
            calendars_to_search = [{"id": calendar_id}]
        else:
            # Use the visible calendars Supabase already has instead of listing them
            # from Google first. Only this account's calendars can be searched with
            # its token, and free/busy-only calendars have no searchable details.
            calendars_to_search = [
                {"id": cal["google_calendar_id"], "summary": cal.get("name")}
                for cal in user_calendars
                if cal.get("google_account_id") == account.get("id")
                and cal.get("access_role") != "freeBusyReader"
            ]
        
        # Search across all calendars
        all_formatted_events = []
        errors = []
        
        # Distribute max_results across calendars (roughly equal per calendar)
        # But ensure we don't exceed the total max_results
        per_calendar_max = max(10, max_results // max(1, len(calendars_to_search)))
        
        semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY_LIMIT)
        
        async def search_single_calendar(cal_id: str) -> Dict[str, Any]:
            """Search a single calendar.
            
            Creates a fresh wrapper for thread-safety: googleapiclient service
            objects are not thread-safe when used concurrently via asyncio.to_thread().
            """
            async with semaphore:
                fresh_wrapper = GoogleCalendarWrapper(
                    GoogleCalendarCredentials(
                        access_token=search_credentials.access_token,
                        refresh_token=search_credentials.refresh_token,
                        credentials_json_path=search_credentials.credentials_json_path,
                    )
                )
                return await fresh_wrapper.search_events(
                    query=query,
                    calendar_id=cal_id,
                    time_min=time_min,
                    time_max=time_max,
                    max_results=per_calendar_max,
                )
        
        calendar_queries: List[tuple[Dict[str, Any], str]] = [
            (calendar, calendar.get("id"))
            for calendar in calendars_to_search
            if calendar.get("id")
        ]
        
        # Execute all calendar searches in parallel (gather preserves input order)
        parallel_start = time_module.time()
        log_start("backend.agent_calendar_service.search_events_for_user.parallel_searches", details=f"calendar_count={len(calendar_queries)}")
        results = await asyncio.gather(
            *[search_single_calendar(cal_id) for _, cal_id in calendar_queries],
            return_exceptions=True,
        )
        parallel_duration = time_module.time() - parallel_start
        log_step("backend.agent_calendar_service.search_events_for_user.parallel_searches", parallel_duration, details=f"calendar_count={len(calendar_queries)}")
        
        for (calendar, cal_id), result in zip(calendar_queries, results):
            if isinstance(result, GoogleCalendarAPIError):
                # Log error but continue with other calendars
                error_msg = f"Error searching calendar {cal_id}: {str(result)}"
                logger.warning(f"Calendar search error user_id={user_id} calendar={cal_id}: {str(result)}")
                errors.append(error_msg)
                continue
            if isinstance(result, BaseException):
                # Any other failure fails the whole search, as the sequential loop did
                raise result
            
            # Format the response to include event details
            events = result.get("items", [])
            calendar_name = calendar.get("summary", cal_id)
            
            for event in events:
                all_formatted_events.append({
                    "event_id": event.get("id"),
                    "calendar_id": cal_id,
                    "calendar_name": calendar_name,
                    "summary": event.get("summary", "No title"),
                    "description": event.get("description", ""),
                    "location": event.get("location", ""),
                    "start": event.get("start", {}).get("dateTime") or event.get("start", {}).get("date"),
                    "end": event.get("end", {}).get("dateTime") or event.get("end", {}).get("date"),
                    "attendees": [
                        {
                            "email": att.get("email"),
                            "displayName": att.get("displayName"),
                        }
                        for att in event.get("attendees", [])
                    ],
                    "organizer": {
                        "email": event.get("organizer", {}).get("email"),
                        "displayName": event.get("organizer", {}).get("displayName"),
                    } if event.get("organizer") else None,
                })
        
        # Sort events by start time (earliest first)
        all_formatted_events.sort(
            key=lambda x: x.get("start", ""),
            reverse=False
        )
        
        # Limit to max_results
        if len(all_formatted_events) > max_results:
            all_formatted_events = all_formatted_events[:max_results]
        
        response = {
            "status": "success",
            "count": len(all_formatted_events),
            "events": all_formatted_events,
            "calendars_searched": len(calendars_to_search),
        }
        
        # Include errors if any occurred (but search was still partially successful)
        if errors:
            response["warnings"] = errors
            
        return response
        
    except GoogleCalendarAPIError as e:
        logger.error(f"Failed to search events user_id={user_id}: {e}")
        return {
            "status": "error",
            "error": str(e),
            "count": 0,
            "events": [],
        }
    except Exception as e:
        logger.error(
            f"Unexpected error searching events user_id={user_id}: {e}",
            exc_info=True,
        )
        return {
            "status": "error",
            "error": f"Unexpected error: {str(e)}",
            "count": 0,
            "events": [],
        }

