"""Trigger the calendar listener tick from a Render cron job."""

from __future__ import annotations

import os
import sys
import time

import httpx

# A tick normally takes 2-15s. Requests sometimes stall at Render's edge or hit a
# deploy or a Supabase blip, so retry those instead of failing the run (each failed
# run sends an email). Only a run where every attempt fails exits non-zero.
ATTEMPTS = 3
ATTEMPT_TIMEOUT_S = 40.0
RETRY_DELAY_S = 5.0
TRANSIENT_STATUSES = {500, 502, 503, 504, 520, 521, 522, 523, 524, 529, 530}


def main() -> int:
    backend_url = (os.environ.get("BACKEND_URL") or "").rstrip("/")
    cron_secret = os.environ.get("CRON_SECRET") or ""
    if not backend_url or not cron_secret:
        print("BACKEND_URL and CRON_SECRET are required", file=sys.stderr)
        return 1
    url = f"{backend_url}/api/v1/calendar-listener/tick"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = httpx.post(
                url,
                headers={"X-Cron-Secret": cron_secret},
                timeout=ATTEMPT_TIMEOUT_S,
            )
        except httpx.TransportError as exc:
            print(f"tick attempt={attempt} error={type(exc).__name__}: {exc}")
        else:
            print(f"tick attempt={attempt} status={response.status_code} body={response.text[:500]}")
            if response.is_success:
                return 0
            if response.status_code not in TRANSIENT_STATUSES:
                return 1
        if attempt < ATTEMPTS:
            time.sleep(RETRY_DELAY_S)
    print(f"tick failed after {ATTEMPTS} attempts", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
