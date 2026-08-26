"""Trigger the calendar listener tick from a Render cron job."""

from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    backend_url = (os.environ.get("BACKEND_URL") or "").rstrip("/")
    cron_secret = os.environ.get("CRON_SECRET") or ""
    if not backend_url or not cron_secret:
        print("BACKEND_URL and CRON_SECRET are required", file=sys.stderr)
        return 1
    url = f"{backend_url}/api/v1/calendar-listener/tick"
    response = httpx.post(
        url,
        headers={"X-Cron-Secret": cron_secret},
        timeout=90.0,
    )
    print(f"tick status={response.status_code} body={response.text[:500]}")
    response.raise_for_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
