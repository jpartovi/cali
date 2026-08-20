"""Validation module for agent requests.

Provides a flexible validation system where different request types can have
different validation steps. Validators are registered in a VALIDATORS dictionary
and run in order when a request is validated.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable

from agent.schemas.agent_response import AgentResponseType
from agent.calendar_client import create_calendar_client

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Helper to run async functions synchronously (same as in tools.py)."""
    try:
        # Try to get existing event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is running, create new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(_run_async_in_thread, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        # No event loop, create new one
        return _run_async_in_thread(coro)


def _run_async_in_thread(coro):
    """Run async function in a new thread with its own event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# Validator function type: takes (result, auth) and returns None if valid, error message if invalid
Validator = Callable[[Dict[str, Any], Dict[str, Any]], Optional[str]]


def check_calendar_write_permission(calendar_id: str, auth: Dict[str, Any]) -> bool:
    """
    Check if user has write access to a calendar.
    
    Args:
        calendar_id: Google Calendar ID to check
        auth: Authentication context with user info
        
    Returns:
        True if user has "writer" or "owner" role, False otherwise
    """
    try:
        client = create_calendar_client()
        # Get all calendars (which are already filtered to writable ones by the API)
        # Use _run_async since list_calendars is async
        calendars = _run_async(client.list_calendars(auth=auth))
        
        # Find the calendar by ID
        for calendar in calendars:
            if calendar.get("id") == calendar_id:
                access_role = calendar.get("access_role")
                # Check if access_role is writer or owner
                if access_role in {"writer", "owner"}:
                    return True
                return False
        
        # Calendar not found - assume no write permission
        logger.warning(f"Calendar {calendar_id} not found in user's calendars")
        return False
        
    except Exception as e:
        logger.error(f"Error checking calendar write permission for {calendar_id}: {e}", exc_info=True)
        # On error, assume no permission (fail safe)
        return False


def validate_write_permissions(result: Dict[str, Any], auth: Dict[str, Any]) -> Optional[str]:
    """
    Validate that the calendar has write permissions for create/update/delete operations.
    
    Args:
        result: External tool result containing request metadata
        auth: Authentication context
        
    Returns:
        None if valid, error message string if invalid
    """
    result_type = result.get("type")
    metadata = result.get("metadata", {})
    calendar_id = metadata.get("calendar_id")
    
    if not calendar_id:
        return "Validation failed: calendar_id is missing from request metadata."
    
    # Check write permission
    has_write_permission = check_calendar_write_permission(calendar_id, auth)
    
    if not has_write_permission:
        # Try to get calendar name for better error message
        calendar_name = "unknown calendar"
        try:
            client = create_calendar_client()
            calendars = _run_async(client.list_calendars(auth=auth))
            for calendar in calendars:
                if calendar.get("id") == calendar_id:
                    calendar_name = calendar.get("name") or calendar_id
                    break
        except Exception:
            pass  # Use default calendar_name
        
        return (
            f"Validation failed: Calendar '{calendar_id}' ({calendar_name}) is read-only. "
            f"You need write permissions to create/update/delete events. "
            f"Use list_calendars() to find a calendar with write access and retry."
        )
    
    return None  # Validation passed


# Validator registry: maps request types to their validation steps
VALIDATORS: Dict[AgentResponseType, List[Validator]] = {
    AgentResponseType.CREATE_EVENT: [validate_write_permissions],
    AgentResponseType.UPDATE_EVENT: [validate_write_permissions],
    AgentResponseType.DELETE_EVENT: [validate_write_permissions],
    # Future: Add validators for other types
    # AgentResponseType.SHOW_SCHEDULE: [validate_schedule_time_range],
    # AgentResponseType.UPDATE_EVENT: [validate_write_permissions, validate_event_conflicts],
}


def validate_request(result: Dict[str, Any], auth: Dict[str, Any]) -> Optional[str]:
    """
    Validate a request by running all registered validators for its type.
    
    Args:
        result: External tool result containing request metadata
        auth: Authentication context
        
    Returns:
        None if all validators pass, error message string from first failing validator
    """
    result_type_str = result.get("type")
    if not result_type_str:
        return "Validation failed: request type is missing."
    
    try:
        result_type = AgentResponseType(result_type_str)
    except ValueError:
        # Unknown request type - skip validation (pass through)
        logger.warning(f"Unknown request type for validation: {result_type_str}")
        return None
    
    # Get validators for this request type
    validators = VALIDATORS.get(result_type, [])
    
    # If no validators registered, request is valid (pass through)
    if not validators:
        return None
    
    # Run all validators in order - return first error found
    for validator in validators:
        try:
            error = validator(result, auth)
            if error:
                logger.info(f"Validation failed for {result_type}: {error}")
                return error
        except Exception as e:
            logger.error(f"Error running validator {validator.__name__} for {result_type}: {e}", exc_info=True)
            # On validator error, fail validation (fail safe)
            return f"Validation error in {validator.__name__}: {str(e)}"
    
    # All validators passed
    return None
