"""Decide whether a calendar event should prompt for a photo.

The evaluator is a stub until the LLM classifier lands. Every event is photo-worthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class PhotoMomentInput:
    title: Optional[str]
    location: Optional[str]
    attendee_count: int
    is_all_day: bool
    start_at: Optional[datetime]
    end_at: Optional[datetime]


def evaluate_photo_worthiness(event: PhotoMomentInput) -> bool:
    return True
