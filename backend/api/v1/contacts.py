"""Apple contact import, list and email routes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import AuthenticatedUser, get_current_user
from domains.contacts.schemas import (
    AddContactEmailRequest,
    AppleImportRequest,
    AppleImportResponse,
    ContactResponse,
)
from domains.contacts.service import ContactsService
from utils.errors import SupabaseStorageError

router = APIRouter(prefix="/contacts", tags=["contacts"])
logger = logging.getLogger(__name__)


@router.post("/import/apple", response_model=AppleImportResponse)
async def import_apple_contacts(
    payload: AppleImportRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AppleImportResponse:
    service = ContactsService()
    try:
        return await asyncio.to_thread(
            service.import_apple,
            current_user.id,
            payload.contacts,
            replace=payload.replace,
            kept_identifiers=payload.kept_identifiers,
        )
    except SupabaseStorageError as exc:
        logger.error("Apple contact import failed user_id=%s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to import Apple contacts.",
        ) from exc


@router.get("/", response_model=list[ContactResponse])
async def list_contacts(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> list[ContactResponse]:
    service = ContactsService()
    try:
        return service.list_contacts(current_user.id)
    except SupabaseStorageError as exc:
        logger.error("Contact list failed user_id=%s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list contacts.",
        ) from exc


@router.post("/{contact_id}/emails", response_model=ContactResponse)
async def add_contact_email(
    contact_id: str,
    payload: AddContactEmailRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> ContactResponse:
    service = ContactsService()
    try:
        contact = await asyncio.to_thread(service.add_email, current_user.id, contact_id, payload.email)
    except SupabaseStorageError as exc:
        logger.error("Contact email add failed user_id=%s contact_id=%s: %s", current_user.id, contact_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add the contact's email.",
        ) from exc
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found.")
    return contact
