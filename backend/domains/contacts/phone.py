"""E.164 phone normalization shared by Apple and Google imports."""

from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException

DEFAULT_REGION = "US"


def to_e164(raw: str | None, default_region: str = DEFAULT_REGION) -> str | None:
    """Parse a phone string to E.164, or None if it is not a valid number."""
    if not raw:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parsed = phonenumbers.parse(stripped, default_region)
    except NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
