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
    contacts: list[AppleContactImport] = Field(default_factory=list, max_length=250)
    replace: bool = False
    kept_identifiers: list[str] = Field(default_factory=list, max_length=10000)

    @field_validator("kept_identifiers")
    @classmethod
    def normalize_kept_identifiers(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for raw in value:
            identifier = raw.strip()
            if identifier and identifier not in seen:
                seen.append(identifier)
        return seen


class AddContactEmailRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        local, _, domain = email.partition("@")
        if not local or "." not in domain or any(ch.isspace() for ch in email):
            raise ValueError("Not an email address")
        return email


class AppleImportResponse(BaseModel):
    imported: int
    updated: int
    deleted: int = 0


class ContactResponse(BaseModel):
    id: str
    user_id: str
    apple_identifier: str
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    nickname: Optional[str] = None
    display_name: str
    invite_email: Optional[str] = None
    emails: list[str] = Field(default_factory=list)
    last_imported_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
