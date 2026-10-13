#!/usr/bin/env python3
"""Print agent trajectories from LangSmith as readable steps.

  agent/.venv/bin/python scripts/agent_trace.py            # latest run
  agent/.venv/bin/python scripts/agent_trace.py -n 5       # list the 5 latest runs
  agent/.venv/bin/python scripts/agent_trace.py <trace_id> # a specific run

Reads LANGSMITH_API_KEY and LANGSMITH_PROJECT from the repo-root .env.
"""

from __future__ import annotations

import argparse
import json
import os
import warnings
from pathlib import Path

from langsmith import Client
from langsmith.utils import LangSmithNotFoundError

# list_runs() works until Jan 2027; move to client.runs.query() before then.
warnings.filterwarnings("ignore", category=DeprecationWarning, module=__name__)

ROOT = Path(__file__).resolve().parents[1]
MAX_FIELD = 600


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def short(value, limit: int = MAX_FIELD) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + f"… (+{len(text) - limit} chars)"


def seconds(run) -> str:
    if run.start_time and run.end_time:
        return f"{(run.end_time - run.start_time).total_seconds():.1f}s"
    return "running"


def tool_calls_from_llm(run) -> list[str]:
    calls = []
    for generation in (run.outputs or {}).get("generations", [[]])[0] or []:
        message = generation.get("message", {})
        kwargs = message.get("kwargs", message)
        for call in kwargs.get("tool_calls", []) or []:
            calls.append(f"{call.get('name')}({short(call.get('args'), 300)})")
    return calls


def print_trace(client: Client, root) -> None:
    query = (root.inputs or {}).get("query")
    print(f"trace {root.trace_id}  {root.start_time:%Y-%m-%d %H:%M:%S}  {seconds(root)}  tokens={root.total_tokens}")
    print(f"query: {query!r}")
    if root.error:
        print(f"ERROR: {short(root.error)}")

    runs = sorted(
        client.list_runs(trace_id=root.trace_id, run_type=None),
        key=lambda r: r.start_time,
    )
    step = 0
    for run in runs:
        if run.run_type == "llm":
            step += 1
            calls = tool_calls_from_llm(run)
            print(f"\n[{step}] model {seconds(run)} tokens={run.total_tokens}")
            for call in calls or ["(no tool call)"]:
                print(f"    → {call}")
            if run.error:
                print(f"    ERROR: {short(run.error)}")
        elif run.run_type == "tool":
            status = f"ERROR: {short(run.error, 300)}" if run.error else short((run.outputs or {}).get("output", run.outputs), 400)
            print(f"    ⤷ {run.name} {seconds(run)}: {status}")

    outputs = root.outputs or {}
    print(f"\nresult: type={outputs.get('type')} success={outputs.get('success')}")
    if outputs.get("metadata"):
        print(f"metadata: {short(outputs['metadata'])}")


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trace_id", nargs="?", help="trace to print (default: latest)")
    parser.add_argument("-n", type=int, help="list the N latest runs instead of printing one")
    args = parser.parse_args()

    client = Client()
    project = os.environ.get("LANGSMITH_PROJECT", "default")

    if args.trace_id:
        root = client.read_run(args.trace_id)
        print_trace(client, root)
        return

    try:
        roots = list(client.list_runs(project_name=project, is_root=True, limit=args.n or 1))
    except LangSmithNotFoundError:
        roots = []
    if not roots:
        print(f"No runs in project {project!r} yet.")
        return
    if args.n:
        for root in roots:
            outputs = root.outputs or {}
            print(
                f"{root.trace_id}  {root.start_time:%H:%M:%S}  {seconds(root):>7}  "
                f"{str(outputs.get('type')):<14} {(root.inputs or {}).get('query')!r}"
            )
        return
    print_trace(client, roots[0])


if __name__ == "__main__":
    main()
