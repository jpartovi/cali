"""Run the Cali agent graph inside the backend process.

Calling the agent through a LangGraph server (`runs.wait`) cost 0.6-0.8s per request
in run creation, queueing and the wait handshake. Running the compiled graph here
skips that hop. The graph's nodes are synchronous, so LangGraph runs them in worker
threads and the event loop stays free to serve the agent's own tool calls, which
still go to this backend's /agent/calendars endpoints over HTTP.

The agent reads its settings from the environment: OPENAI_API_KEY, AGENT_FIND_EVENT,
and LANGSMITH_TRACING / LANGSMITH_PROJECT / LANGSMITH_API_KEY for traces.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# Tool calls go to this process directly rather than out through the public BACKEND_URL
os.environ.setdefault("AGENT_BACKEND_URL", f"http://127.0.0.1:{os.getenv('PORT', '8000')}")

# The Docker image copies the agent package next to the backend code (/app/agent).
# Running from a checkout, it sits one level up, next to backend/.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if not (Path(__file__).resolve().parents[1] / "agent").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

# Normal flows finish in ~15 graph steps; fail fast instead of looping on a repeated tool error
RECURSION_LIMIT = 30

_lock = threading.Lock()
_graph = None


def get_agent_graph():
    """The compiled agent graph (imported on first use; importing it loads LangChain and OpenAI)."""
    global _graph
    if _graph is None:
        with _lock:
            if _graph is None:
                from agent.main import cali_graph

                _graph = cali_graph
    return _graph


def relative_dates_sheet(now: datetime, timezone_name: str) -> str:
    """The agent's relative-dates cheat sheet ("tomorrow", "next Friday", ...) without its ISO ranges.

    Anything else that reads the user's words (the Jev event finder) uses this so it resolves
    dates the same way the agent does.
    """
    from agent.time_reference import generate_time_reference

    return re.sub(r" \(\d{4}-\d\d-\d\dT[^)]*\)", "", generate_time_reference(now, timezone_name))


async def run_agent(input_state: Dict[str, Any]) -> Dict[str, Any]:
    """Run one stateless agent request and return the graph's output state."""
    return await get_agent_graph().ainvoke(
        input_state,
        config={"recursion_limit": RECURSION_LIMIT, "run_name": "agent"},
    )
