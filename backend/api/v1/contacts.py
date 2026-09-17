"""Apple contact import and list routes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import AuthenticatedUser, get_current_user
from domains.contacts.schemas import (
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
