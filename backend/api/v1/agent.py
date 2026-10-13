"""Agent endpoint for invoking the LangGraph calendar agent."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, Body
from pydantic import BaseModel
from langgraph_sdk import get_client

from schemas.agent_response import (
    AgentResponse,
    ErrorResponse,
    ShowEventResponse,
    ShowScheduleResponse,
    CreateEventResponse,
    UpdateEventResponse,
    DeleteEventResponse,
    NoActionResponse,
)
from pydantic import ValidationError
from schemas.user import AuthenticatedUser
from core.dependencies import get_current_user, get_user_timezone
from core.config import get_settings
from core.timing_logger import log_step, log_start
from domains.transcription.service import TranscriptionService
from services import agent_calendar_service
from services.agent_runner import RECURSION_LIMIT, run_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

# Initialize transcription service
transcription_service = TranscriptionService()


# The pre-pass must never hold up a request for long; past this, the agent runs without a kind
REQUEST_CLASSIFIER_TIMEOUT_SECONDS = 2.0


async def _classify_request_kind(query_text: str) -> Optional[str]:
    """Stage 1 pre-pass: the request kind when Jev is sure of it, else None (agent uses its full prompt)."""
    if not get_settings().agent_request_classifier:
        return None
    from services.request_classifier import classify_request

    start = time.time()
    try:
        result = await asyncio.wait_for(classify_request(query_text), timeout=REQUEST_CLASSIFIER_TIMEOUT_SECONDS)
    except Exception as e:
        logger.warning(f"Request classification skipped: {type(e).__name__}: {e}")
        log_step("backend.api.action.classify", time.time() - start, details=f"skipped={type(e).__name__}")
        return None
    kind = result["kind"] if result["sure"] and result["kind"] != "other" else None
    log_step(
        "backend.api.action.classify",
        time.time() - start,
        details=f"kind={result['kind']} p={result['probability']} used={kind is not None} jev_ms={result['jev_ms']}",
    )
    logger.info(
        f"Request kind: {result['kind']} p={result['probability']} used={kind is not None} "
        f"seconds={time.time() - start:.2f} jev_ms={result['jev_ms']} probabilities={result['probabilities']}"
    )
    return kind


# Stage 2: only these kinds act on an existing event, so only they look one up
EVENT_KINDS = ("find_event", "update_event", "delete_event")
# Default window when the request names no time ("cancel my haircut")
PREPASS_EVENT_DAYS_BACK = 1
PREPASS_EVENT_DAYS_AHEAD = 30
# Searched first from calendar_event_snapshots (the calendar listener's horizon); Google only on a miss
PREPASS_SNAPSHOT_DAYS_AHEAD = 13
PREPASS_EVENT_TIMEOUT_SECONDS = 4.0


async def _find_prepass_event(user_id: str, query_text: str, request_kind: Optional[str], user_timezone: str) -> Optional[dict]:
    """Stage 2 pre-pass: Jev's likely events for find/update/delete requests, else None."""
    if request_kind not in EVENT_KINDS or not get_settings().agent_prepass_event:
        return None
    from datetime import timedelta
    from services.event_finder import find_event_for_user, snapshot_events_for_user

    today = datetime.now(ZoneInfo(user_timezone)).date()
    start_date = today - timedelta(days=PREPASS_EVENT_DAYS_BACK)
    end_date = today + timedelta(days=PREPASS_EVENT_DAYS_AHEAD)
    snapshot_end = today + timedelta(days=PREPASS_SNAPSHOT_DAYS_AHEAD)
    start = time.time()
    source = "snapshots"

    async def lookup() -> dict:
        nonlocal source, end_date
        # 1) The listener's copy of the next two weeks: a database read, no Google calls
        events = await asyncio.to_thread(snapshot_events_for_user, user_id, start_date, snapshot_end, user_timezone)
        if events:
            found = await find_event_for_user(
                user_id=user_id, description=query_text, start_date=start_date,
                end_date=snapshot_end, timezone_name=user_timezone, events=events,
            )
            if found["status"] in ("match", "ambiguous"):
                end_date = snapshot_end
                return found
        # 2) Nothing there: search the full window from Google
        source = "google"
        return await find_event_for_user(
            user_id=user_id, description=query_text, start_date=start_date,
            end_date=end_date, timezone_name=user_timezone,
        )

    try:
        result = await asyncio.wait_for(lookup(), timeout=PREPASS_EVENT_TIMEOUT_SECONDS)
    except Exception as e:
        logger.warning(f"Pre-pass event lookup skipped: {type(e).__name__}: {e}")
        log_step("backend.api.action.prepass_event", time.time() - start, details=f"skipped={type(e).__name__}")
        return None
    log_step(
        "backend.api.action.prepass_event",
        time.time() - start,
        details=f"status={result['status']} source={source} events={result['event_count']} jev_ms={result['jev_ms']}",
    )
    if result["status"] == "too_many":
        return None  # Nothing useful to show; the agent searches on its own
    top = result.get("top") or []
    logger.info(
        f"Pre-pass event: status={result['status']} source={source} events={result['event_count']} "
        f"seconds={time.time() - start:.2f} top={[(e.get('summary'), e.get('probability')) for e in top]}"
    )
    return {
        "status": result["status"],
        "searched_from": start_date.isoformat(),
        "searched_to": end_date.isoformat(),
        "event_count": result["event_count"],
        "top": top,
    }


PREPASS_TIME_TIMEOUT_SECONDS = 4.0


async def _find_prepass_time(user_id: str, query_text: str, request_kind: Optional[str], user_timezone: str) -> Optional[dict]:
    """Create pre-pass: Jev's suggested free times for a new event without an exact time, else None."""
    if request_kind != "create_event" or not get_settings().agent_prepass_time:
        return None
    from services.time_finder import find_time_for_new_event

    start = time.time()
    try:
        result = await asyncio.wait_for(
            find_time_for_new_event(user_id, query_text, user_timezone),
            timeout=PREPASS_TIME_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.warning(f"Pre-pass time lookup skipped: {type(e).__name__}: {e}")
        log_step("backend.api.action.prepass_time", time.time() - start, details=f"skipped={type(e).__name__}")
        return None
    log_step("backend.api.action.prepass_time", time.time() - start, details=f"status={result['status']} jev_ms={result['jev_ms']}")
    logger.info(
        f"Pre-pass time: status={result['status']} duration={result['duration_minutes']} buffer={result['buffer_minutes']} "
        f"days={result['days']} slots={result.get('slot_count')} seconds={time.time() - start:.2f} "
        f"top={[(s['when'], s['probability']) for s in result.get('top', [])]}"
    )
    # Only a "slots" or "none" answer tells the agent something it couldn't work out itself
    return result if result["status"] in ("slots", "none") else None


PREPASS_PEOPLE_TIMEOUT_SECONDS = 4.0


async def _find_prepass_people(user_id: str, query_text: str, user_timezone: str) -> Optional[dict]:
    """People pre-pass: who the request is talking about (any request kind), else None."""
    if not get_settings().agent_prepass_people:
        return None
    from services.people_finder import find_people_for_user, people_summary

    start = time.time()
    try:
        result = await asyncio.wait_for(
            find_people_for_user(user_id, query_text, user_timezone),
            timeout=PREPASS_PEOPLE_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.warning(f"Pre-pass people lookup skipped: {type(e).__name__}: {e}")
        log_step("backend.api.action.prepass_people", time.time() - start, details=f"skipped={type(e).__name__}")
        return None
    log_step(
        "backend.api.action.prepass_people",
        time.time() - start,
        details=f"status={result['status']} candidates={result['candidate_count']} db_ms={result['db_ms']} jev_ms={result['jev_ms']}",
    )
    logger.info(f"Pre-pass people: status={result['status']} seconds={time.time() - start:.2f} {people_summary(result)}")
    return result if result["status"] == "found" else None


def _writable_calendars(user_id: str) -> list:
    """Calendars the user can write to, in the agent's list_calendars format.

    Same source and filter as GET /agent/calendars. Sent with the request so the
    agent's write validation doesn't have to call back to the backend for it.
    """
    from domains.calendars.repository import CalendarRepository

    return [
        {
            "id": cal.get("google_calendar_id"),
            "name": cal.get("name"),
            "access_role": cal.get("access_role"),
            "is_primary": cal.get("is_primary", False),
        }
        for cal in CalendarRepository().get_calendars(user_id)
        if cal.get("access_role") in {"writer", "owner"}
    ]


async def _run_remote_agent(settings, input_state: dict, user_id: str) -> dict:
    """Run the agent on the LangGraph server at LANGGRAPH_AGENT_URL (AGENT_IN_PROCESS=false)."""
    is_local_agent = urlparse(settings.langgraph_agent_url).hostname in {"localhost", "127.0.0.1"}
    api_key = settings.langsmith_api_key or settings.langgraph_api_key
    if api_key is None and not is_local_agent:
        logger.error(f"LangGraph agent API key is missing for remote agent URL user_id={user_id}")
        raise HTTPException(
            status_code=500,
            detail="Agent service is not configured with LangSmith authentication credentials."
        )

    client = get_client(url=settings.langgraph_agent_url, api_key=api_key)
    return await client.runs.wait(
        thread_id=None,
        assistant_id="agent",
        input=input_state,
        config={"recursion_limit": RECURSION_LIMIT},
    )


class AgentActionRequest(BaseModel):
    """Request model for agent action endpoint."""
    query: str


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(..., description="Audio file to transcribe"),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Transcribe an audio file to text using Deepgram.
    
    Returns the transcribed text as JSON.
    """
    endpoint_start = time.time()
    log_start("backend.api.transcribe", details=f"user_id={current_user.id} filename={file.filename}")
    try:
        # Validate file
        if not file or not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")

        # Transcribe audio file
        try:
            # Reset file pointer to beginning in case it was read already
            await file.seek(0)
            transcribe_start = time.time()
            transcribed_text = await transcription_service.transcribe(
                file=file.file, filename=file.filename, mime_type=file.content_type
            )
            transcribe_duration = time.time() - transcribe_start
            log_step("backend.api.transcribe.transcription_service", transcribe_duration)
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail=f"Transcription error: {str(e)}"
            )
        except Exception as e:
            # Log full error details for debugging (verbose internal logging)
            logger.error(
                f"Transcription failed user_id={current_user.id} file={file.filename}: {e}",
                exc_info=True,
            )
            # Return brief, user-friendly message (not technical details)
            raise HTTPException(
                status_code=500, detail="An error occurred while transcribing audio. Please try again."
            )

        if not transcribed_text or not transcribed_text.strip():
            raise HTTPException(
                status_code=400,
                detail="Transcription resulted in empty text.",
            )

        endpoint_duration = time.time() - endpoint_start
        log_step("backend.api.transcribe", endpoint_duration, details=f"text_length={len(transcribed_text)}")
        return {"text": transcribed_text}

    except HTTPException:
        raise
    except Exception as e:
        # Log full error details for debugging (verbose internal logging)
        logger.error(
            f"Transcription endpoint failed user_id={current_user.id}: {e}",
            exc_info=True,
        )
        # Return brief, user-friendly message (not technical details)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while transcribing audio. Please try again."
        )


@router.post("/action")
async def agent_action(
    request: Request,
    body: AgentActionRequest = Body(..., description="Text query to process"),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Invoke the LangGraph calendar agent with a text query.

    The text query is passed to the agent which will classify intent and extract
    metadata for calendar operations.
    """
    endpoint_start = time.time()
    query_text = body.query
    log_start("backend.api.action", details=f"user_id={current_user.id} query_length={len(query_text)}")
    try:
        # Extract Supabase access token from Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid authentication token",
            )
        supabase_access_token = auth_header.replace("Bearer ", "", 1)

        # Prepare auth data with Supabase token
        auth = {
            "user_id": current_user.id,
            "supabase_access_token": supabase_access_token,
        }

        # Validate query text
        if not query_text or not query_text.strip():
            raise HTTPException(
                status_code=400,
                detail="Query text is required and cannot be empty.",
            )

        settings = get_settings()

        # Get user timezone from users table
        timezone_start = time.time()
        user_timezone = get_user_timezone(current_user.id)
        timezone_duration = time.time() - timezone_start
        log_step("backend.api.action.get_timezone", timezone_duration)
        
        # Convert to user's timezone for current time calculation
        current_utc = datetime.now(timezone.utc)
        user_tz = ZoneInfo(user_timezone)
        current_user_time = current_utc.astimezone(user_tz)
        current_time_str = current_user_time.isoformat()
        current_day_of_week = current_user_time.strftime("%A")  # Full day name

        # The calendar list (a database read) runs while Jev classifies the request; the people
        # pre-pass doesn't need the kind, so it runs alongside both stages
        calendars_task = asyncio.create_task(asyncio.to_thread(_writable_calendars, current_user.id))
        people_task = asyncio.create_task(_find_prepass_people(current_user.id, query_text, user_timezone))
        request_kind = await _classify_request_kind(query_text)
        prepass_event = await _find_prepass_event(current_user.id, query_text, request_kind, user_timezone)
        prepass_time = await _find_prepass_time(current_user.id, query_text, request_kind, user_timezone)
        prepass_people = await people_task  # Never raises: failures come back as None
        try:
            auth["writable_calendars"] = await calendars_task
        except Exception as e:
            logger.warning(f"Writable calendar lookup failed; the agent will look them up itself: {e}")

        input_state = {
            "query": query_text,
            "auth": auth,
            "request_kind": request_kind,
            "prepass_event": prepass_event,
            "prepass_time": prepass_time,
            "prepass_people": prepass_people,
            "success": False,
            "type": None,
            "metadata": {},
            "messages": [],
            "tool_results": {},
            "terminated": False,
            "current_time": current_time_str,
            "timezone": user_timezone,
            "current_day_of_week": current_day_of_week,
        }

        logger.info(
            f"Invoking agent user_id={current_user.id} query_length={len(query_text)}"
        )

        # Invoke and wait for completion
        langgraph_start = time.time()
        if settings.agent_in_process:
            result = await run_agent(input_state)
        else:
            result = await _run_remote_agent(settings, input_state, current_user.id)
        langgraph_duration = time.time() - langgraph_start
        log_step("backend.api.action.langgraph_invoke", langgraph_duration, details=f"response_type={result.get('type')}")

        logger.info(
            f"Agent completed user_id={current_user.id} "
            f"type={result.get('type')} success={result.get('success')}"
        )

        # Validate and parse agent response using Pydantic models
        parse_start = time.time()
        try:
            if "message" in result:
                # Error response
                error_response = ErrorResponse.model_validate(result)
                parse_duration = time.time() - parse_start
                log_step("backend.api.action.parse_response", parse_duration, details="result=error")
                endpoint_duration = time.time() - endpoint_start
                log_step("backend.api.action", endpoint_duration)
                return error_response.model_dump()
            elif "type" in result:
                # Success response - parse based on type
                response_type = result.get("type")
                if response_type == "show-event":
                    response = ShowEventResponse.model_validate(result)
                elif response_type == "show-schedule":
                    response = ShowScheduleResponse.model_validate(result)
                elif response_type == "create-event":
                    response = CreateEventResponse.model_validate(result)
                elif response_type == "update-event":
                    response = UpdateEventResponse.model_validate(result)
                elif response_type == "delete-event":
                    response = DeleteEventResponse.model_validate(result)
                elif response_type == "no-action":
                    response = NoActionResponse.model_validate(result)
                else:
                    logger.warning(
                        f"Unknown response type user_id={current_user.id} type={response_type}"
                    )
                    error_response = ErrorResponse(
                        message=f"Unknown response type from agent: {response_type}"
                    )
                    parse_duration = time.time() - parse_start
                    log_step("backend.api.action.parse_response", parse_duration, details=f"result=unknown_type type={response_type}")
                    endpoint_duration = time.time() - endpoint_start
                    log_step("backend.api.action", endpoint_duration)
                    return error_response.model_dump()
                parse_duration = time.time() - parse_start
                log_step("backend.api.action.parse_response", parse_duration, details=f"result=success type={response_type}")
                endpoint_duration = time.time() - endpoint_start
                log_step("backend.api.action", endpoint_duration)
                return response.model_dump()
            else:
                # Fallback for unexpected responses - treat as error
                # This is an agent mistake, not a user error
                # Log full details for debugging (verbose internal logging)
                logger.warning(
                    f"Unexpected result format user_id={current_user.id} "
                    f"keys={list(result.keys())}"
                )
                # Return brief, user-friendly message (not technical details)
                error_response = ErrorResponse(
                    message="Agent failed to handle request precisely. Please try rephrasing your request."
                )
                parse_duration = time.time() - parse_start
                log_step("backend.api.action.parse_response", parse_duration, details="result=unexpected_format")
                endpoint_duration = time.time() - endpoint_start
                log_step("backend.api.action", endpoint_duration)
                return error_response.model_dump()
        except ValidationError as e:
            # This is an agent mistake (invalid response format), not a user error
            # Log full details for debugging (verbose internal logging)
            logger.error(
                f"Response validation failed user_id={current_user.id}: {e}",
                exc_info=True,
            )
            # Return brief, user-friendly message (not technical details)
            error_response = ErrorResponse(
                message="Agent failed to handle request precisely. Please try rephrasing your request."
            )
            endpoint_duration = time.time() - endpoint_start
            log_step("backend.api.action", endpoint_duration, details="result=validation_error")
            return error_response.model_dump()

    except HTTPException:
        endpoint_duration = time.time() - endpoint_start
        log_step("backend.api.action", endpoint_duration, details="result=http_exception")
        raise
    except Exception as e:
        # Log full error details for debugging (verbose internal logging)
        logger.error(
            f"Agent invocation failed user_id={current_user.id}: {e}",
            exc_info=True,
        )
        # Return brief, user-friendly message (not technical details)
        endpoint_duration = time.time() - endpoint_start
        log_step("backend.api.action", endpoint_duration, details=f"error={str(e)[:80]}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while processing your request. Please try again."
        )
