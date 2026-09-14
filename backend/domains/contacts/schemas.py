"""Contact import and search schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

ContactSource = Literal["apple", "google", "manual"]


class ImportedEmail(BaseModel):
    email: str = Field(..., min_length=3)
    label: Optional[str] = None
    is_primary: bool = False

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class ImportedPhone(BaseModel):
    phone: str = Field(..., min_length=1)
    label: Optional[str] = None
    is_primary: bool = False

    @field_validator("phone")
    @classmethod
    def strip_phone(cls, value: str) -> str:
        return value.strip()


class AppleContactImport(BaseModel):
    apple_identifier: str = Field(..., min_length=1)
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    nickname: Optional[str] = None
    emails: list[ImportedEmail] = Field(default_factory=list)
    phones: list[ImportedPhone] = Field(default_factory=list)


class AppleImportRequest(BaseModel):
    contacts: list[AppleContactImport] = Field(..., max_length=250)


class AppleImportResponse(BaseModel):
    imported: int
    merged: int
    emails_added: int
    phones_added: int


class GoogleAccountImportResult(BaseModel):
    account_id: str
    email: Optional[str] = None
    created: int = 0
    enriched: int = 0
    emails_added: int = 0
    phones_added: int = 0
    needs_reauth: bool = False
    error: Optional[str] = None


class GoogleImportResponse(BaseModel):
    accounts: list[GoogleAccountImportResult]
    needs_reauth: bool
    needs_reauth_account_ids: list[str] = Field(default_factory=list)


class ContactEmailResponse(BaseModel):
    email: str
    label: Optional[str] = None
    is_primary: bool
    source: ContactSource


class ContactPhoneResponse(BaseModel):
    phone_raw: str
    phone_e164: Optional[str] = None
    label: Optional[str] = None
    is_primary: bool
    source: ContactSource


class ContactResponse(BaseModel):
    id: str
    user_id: str
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    nickname: Optional[str] = None
    display_name: str
    apple_identifier: Optional[str] = None
    google_resource_name: Optional[str] = None
    sources: list[str] = Field(default_factory=list)
    emails: list[ContactEmailResponse] = Field(default_factory=list)
    phones: list[ContactPhoneResponse] = Field(default_factory=list)
    last_imported_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
