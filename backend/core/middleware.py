"""HTTP request/response logging middleware."""

from __future__ import annotations

import logging
import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Paths to skip logging (health checks, etc.)
SKIP_LOGGING_PATHS = {"/healthz", "/"}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log HTTP requests and responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip logging for health check endpoints
        if request.url.path in SKIP_LOGGING_PATHS:
            return await call_next(request)

        start_time = time.time()
        
        # Log request (user_id not available yet - dependencies run after middleware)
        method = request.method
        path = request.url.path
        query_string = str(request.url.query) if request.url.query else ""
        full_path = f"{path}?{query_string}" if query_string else path
        
        logger.info(f"{method} {full_path}")
        
        # Process request
        try:
            response = await call_next(request)
        except Exception as e:
            # Log exception and re-raise
            # Try to get user_id from request.state (set by dependency if auth succeeded)
            user_id = getattr(request.state, "user_id", None)
            duration = time.time() - start_time
            logger.error(
                f"{method} {full_path} user_id={user_id or 'unknown'} "
                f"ERROR {duration:.3f}s: {type(e).__name__}: {str(e)}",
                exc_info=True,
            )
            raise
        
        # Log response (user_id available now if authentication succeeded)
        duration = time.time() - start_time
        status_code = response.status_code
        status_text = "OK" if 200 <= status_code < 300 else "ERROR" if status_code >= 400 else "REDIRECT"
        
        # Extract user_id from request.state if available (set by get_current_user dependency)
        user_id = getattr(request.state, "user_id", None)
        
        logger.info(
            f"{method} {full_path} user_id={user_id or 'unknown'} "
            f"{status_code} {status_text} {duration:.3f}s"
        )
        
        return response
