"""Short-lived in-process cache of `users` rows.

Every authenticated request read the user's row (and then the timezone column
again) from Supabase, ~0.4-0.6s of blocking I/O before any work started. The JWT
is still verified on every request; only the row lookup is cached. Changes made
outside this process (e.g. the app updating the timezone) show up within TTL_SECONDS.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple

TTL_SECONDS = 300.0

_lock = threading.Lock()
_rows: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def get_cached_user(user_id: str) -> Optional[Dict[str, Any]]:
    """The user's row if it was cached within the last TTL_SECONDS, else None."""
    with _lock:
        entry = _rows.get(user_id)
        if entry is None:
            return None
        cached_at, row = entry
        if time.monotonic() - cached_at > TTL_SECONDS:
            _rows.pop(user_id, None)
            return None
        return row


def cache_user(row: Dict[str, Any]) -> None:
    """Remember a full `users` row (must include its id)."""
    user_id = row.get("id")
    if user_id:
        with _lock:
            _rows[user_id] = (time.monotonic(), row)
