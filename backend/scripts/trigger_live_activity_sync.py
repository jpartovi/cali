"""Trigger the live-activity sync endpoint (used by the Render cron job)."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    base = os.environ.get("BACKEND_URL", "").rstrip("/")
    secret = os.environ.get("CRON_SECRET", "")
    if not base or not secret:
        print("BACKEND_URL and CRON_SECRET are required", file=sys.stderr)
        return 1

    request = urllib.request.Request(
        f"{base}/api/v1/live-activities/sync",
        method="POST",
        headers={"X-Cron-Secret": secret},
    )
    try:
        with urllib.request.urlopen(request, timeout=50) as response:
            body = response.read().decode("utf-8", errors="replace")
            print(body)
            return 0
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
