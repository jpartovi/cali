"""Apple contact import and list schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class AppleContactImport(BaseModel):
    apple_identifier: str = Field(..., min_length=1)
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    nickname: Optional[str] = None
    emails: list[str] = Field(default_factory=list)

    @field_validator("emails")
    @classmethod
    def normalize_emails(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for raw in value:
            email = raw.strip().lower()
            if email and email not in seen:
                seen.append(email)
        return seen


class AppleImportRequest(BaseModel):
    contacts: list[AppleContactImport] = Field(..., max_length=250)


class AppleImportResponse(BaseModel):
    imported: int
    updated: int


class ContactResponse(BaseModel):
    id: str
    user_id: str
    apple_identifier: str
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    nickname: Optional[str] = None
    display_name: str
    invite_email: Optional[str] = None
    last_imported_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
