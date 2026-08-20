"""Mock data generators for calendar agent tools."""

from datetime import datetime, timedelta
from typing import List, Dict, Any


def generate_mock_calendars() -> List[Dict[str, Any]]:
    """Generate a mock list of calendars."""
    return [
        {
            "id": "cal_primary_123",
            "name": "Primary Calendar",
            "summary": "Primary Calendar",
            "description": "My primary calendar",
            "timezone": "America/Los_Angeles",
            "color": "#7986CB",
            "is_primary": True,
        },
        {
            "id": "cal_work_456",
            "name": "Work Calendar",
            "summary": "Work Calendar",
            "description": "Work events",
            "timezone": "America/Los_Angeles",
            "color": "#F4511E",
            "is_primary": False,
        },
        {
            "id": "cal_personal_789",
            "name": "Personal Calendar",
            "summary": "Personal Calendar",
            "description": "Personal events",
            "timezone": "America/Los_Angeles",
            "color": "#33B679",
            "is_primary": False,
        },
    ]


# Fixed events for 1/14/2026 in PST
FIXED_EVENTS_2026_01_14 = [
    {
        "id": "event_001",
        "summary": "Team Meeting",
        "description": "Mock event: Team Meeting",
        "status": "confirmed",
        "start": {
            "dateTime": "2026-01-14T09:00:00-08:00",
            "timeZone": "America/Los_Angeles",
        },
        "end": {
            "dateTime": "2026-01-14T10:00:00-08:00",
            "timeZone": "America/Los_Angeles",
        },
        "calendar_id": "cal_primary_123",
        "calendar_name": "Primary Calendar",
        "html_link": "https://calendar.google.com/event?eid=event_001",
        "hangout_link": None,
        "updated": "2026-01-14T00:00:00-08:00",
    },
    {
        "id": "event_002",
        "summary": "Lunch",
        "description": "Mock event: Lunch",
        "status": "confirmed",
        "start": {
            "dateTime": "2026-01-14T12:00:00-08:00",
            "timeZone": "America/Los_Angeles",
        },
        "end": {
            "dateTime": "2026-01-14T13:00:00-08:00",
            "timeZone": "America/Los_Angeles",
        },
        "calendar_id": "cal_primary_123",
        "calendar_name": "Primary Calendar",
        "html_link": "https://calendar.google.com/event?eid=event_002",
        "hangout_link": None,
        "updated": "2026-01-14T00:00:00-08:00",
    },
    {
        "id": "event_003",
        "summary": "Doctor Appointment",
        "description": "Mock event: Doctor Appointment",
        "status": "confirmed",
        "start": {
            "dateTime": "2026-01-14T15:00:00-08:00",
            "timeZone": "America/Los_Angeles",
        },
        "end": {
            "dateTime": "2026-01-14T16:00:00-08:00",
            "timeZone": "America/Los_Angeles",
        },
        "calendar_id": "cal_primary_123",
        "calendar_name": "Primary Calendar",
        "html_link": "https://calendar.google.com/event?eid=event_003",
        "hangout_link": None,
        "updated": "2026-01-14T00:00:00-08:00",
    },
]


def generate_mock_event(
    event_id: str = None,
    calendar_id: str = None,
    summary: str = None,
    start_time: datetime = None,
    end_time: datetime = None,
) -> Dict[str, Any]:
    """Generate a mock calendar event."""
    if event_id is None:
        event_id = "event_default_001"
    if calendar_id is None:
        calendars = generate_mock_calendars()
        calendar_id = calendars[0]["id"]
    
    if start_time is None:
        # Default to 1/14/2026 10:00 AM PST
        start_time = datetime(2026, 1, 14, 10, 0, 0)
    if end_time is None:
        end_time = start_time + timedelta(hours=1)
    
    if summary is None:
        summary = "Default Event"
    
    return {
        "id": event_id,
        "summary": summary,
        "description": f"Mock event: {summary}",
        "status": "confirmed",
        "start": {
            "dateTime": start_time.isoformat(),
            "timeZone": "America/Los_Angeles",
        },
        "end": {
            "dateTime": end_time.isoformat(),
            "timeZone": "America/Los_Angeles",
        },
        "calendar_id": calendar_id,
        "calendar_name": "Primary Calendar",
        "html_link": f"https://calendar.google.com/event?eid={event_id}",
        "hangout_link": None,
        "updated": datetime(2026, 1, 14, 0, 0, 0).isoformat(),
    }


def generate_mock_events(
    start_time: datetime,
    end_time: datetime,
    count: int = 3,
    keywords: List[str] = None,
) -> List[Dict[str, Any]]:
    """Generate mock calendar events within a time window.
    
    Returns a fixed set of events on 1/14/2026 in PST, filtered by the requested time range.
    """
    # Parse the fixed events' start times to check if they fall within the requested range
    filtered_events = []
    
    for event in FIXED_EVENTS_2026_01_14:
        # Parse the event's start time from the ISO string
        event_start_str = event["start"]["dateTime"]
        # Handle both "Z" (UTC) and timezone offset formats
        if event_start_str.endswith("Z"):
            event_start = datetime.fromisoformat(event_start_str.replace("Z", "+00:00"))
        else:
            event_start = datetime.fromisoformat(event_start_str)
        
        # Check if event falls within the requested time range
        # Normalize both to UTC for comparison if needed, or compare directly if both are timezone-aware
        if start_time <= event_start < end_time:
            # If keywords are provided, filter by matching summaries
            if keywords:
                event_summary_lower = event["summary"].lower()
                if any(kw.lower() in event_summary_lower for kw in keywords):
                    filtered_events.append(event)
            else:
                filtered_events.append(event)
    
    # Sort by start time to ensure consistent ordering
    filtered_events.sort(key=lambda e: e["start"]["dateTime"])
    
    # Return up to 'count' events (or all if fewer than count)
    return filtered_events[:count] if count else filtered_events
