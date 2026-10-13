"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1.router import router as v1_router
from core.logging import setup_logging, get_logger
from core.middleware import RequestLoggingMiddleware

# Configure centralized logging
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager."""
    from core.config import get_settings
    from domains.live_activities.service import register_live_activity_handler
    from services.agent_runner import get_agent_graph

    logger.info("Starting Cali backend API...")
    register_live_activity_handler()
    if get_settings().agent_in_process:
        # Load the agent now so the first request doesn't wait on importing LangChain
        get_agent_graph()
    yield
    logger.info("Shutting down Cali backend API...")


# Create FastAPI application
app = FastAPI(
    title="Cali Backend API",
    description="Backend API for Cali - handles authentication, Google Calendar integration, and agent services",
    version="0.1.0",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request logging middleware (must be after CORS to log authenticated requests)
app.add_middleware(RequestLoggingMiddleware)

# Include API router
app.include_router(v1_router, prefix="/api/v1")


@app.get("/healthz")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Cali Backend API",
        "version": "0.1.0",
        "docs": "/docs",
    }


if __name__ == "__main__":
    import os

    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
