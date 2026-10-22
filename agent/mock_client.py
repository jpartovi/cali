"""Mock calendar client implementation.

Wraps existing mock data generation functions in the CalendarClient interface.
This file can be easily deleted when removing mock functionality.
"""

from typing import List, Dict, Any, Optional
from agent.calendar_client import CalendarClient
from agent.mocks import (
    generate_mock_events,
    generate_mock_event,
    generate_mock_calendars,
)


class MockClient(CalendarClient):
    """
    Mock implementation of CalendarClient using mock data generators.
    
    NOTE: This is mock code that can be deleted when removing mock functionality.
    All mock-related code is in this file and agent/mocks.py
    """
    
    async def read_schedule(
        self,
        start_time: str,
        end_time: str,
        auth: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Read events from mock schedule within a time window."""
        from datetime import datetime
        
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        
        events = generate_mock_events(start_dt, end_dt, count=3)
        
        # Return minimal details with both id and calendar_id (required)
        return [
            {
                "id": event["id"],
                "summary": event["summary"],
                "start": event["start"],
                "end": event["end"],
                "calendar_id": event["calendar_id"],
            }
            for event in events
        ]

    async def search_events(
        self,
        keywords: str,
        start_time: str,
        end_time: str,
        auth: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for mock events matching keywords within a time window."""
        from datetime import datetime
        
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        
        keyword_list = keywords.split()
        events = generate_mock_events(start_dt, end_dt, count=2, keywords=keyword_list)
        
        # Return events with both id and calendar_id (required)
        return [
            {
                "id": event["id"],
                "summary": event["summary"],
                "start": event["start"],
                "end": event["end"],
                "calendar_id": event["calendar_id"],
            }
            for event in events
        ]


    async def find_event(
        self,
        description: str,
        start_time: str,
        end_time: str,
        auth: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Mock: treat the description as keywords and match only a single hit."""
        events = await self.search_events(description, start_time, end_time, auth=auth)
        if len(events) == 1:
            return {"status": "match", "event": {**events[0], "probability": 1.0}, "candidates": []}
        if events:
            share = round(1 / len(events), 3)
            return {"status": "ambiguous", "event": None, "candidates": [{**e, "probability": share} for e in events[:3]]}
        return {"status": "none", "event": None, "candidates": []}

    async def find_free_time(
        self,
        start_time: str,
        end_time: str,
        duration_minutes: int,
        buffer_minutes: int = 0,
        earliest_time: Optional[str] = None,
        latest_time: Optional[str] = None,
        auth: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Mock: the free-time search runs in the backend, so report nothing found."""
        return {"blocks": [], "block_count": 0, "note": "Free time search isn't available with the mock calendar."}

    async def find_people(
        self,
        names: str,
        auth: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Mock: the people lookup runs in the backend, so report nobody found."""
        return {"status": "none", "people": [], "ambiguous": [], "maybe": []}

    async def read_event(
        self,
        event_id: str,
        calendar_id: str,
        auth: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Read detailed information about a specific mock event."""
        event = generate_mock_event(event_id=event_id, calendar_id=calendar_id)
        # Ensure both id and calendar_id are present (required)
        if "id" not in event:
            event["id"] = event_id
        if "calendar_id" not in event:
            event["calendar_id"] = calendar_id
        return event

    async def list_calendars(
        self,
        auth: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """List all mock calendars."""
        return generate_mock_calendars()
