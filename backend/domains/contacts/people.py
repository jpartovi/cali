"""Google People API client for contacts and other contacts."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from domains.contacts.phone import to_e164

logger = logging.getLogger(__name__)

CONTACTS_READONLY_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"
OTHER_CONTACTS_READONLY_SCOPE = "https://www.googleapis.com/auth/contacts.other.readonly"
REQUIRED_CONTACTS_SCOPES = (CONTACTS_READONLY_SCOPE, OTHER_CONTACTS_READONLY_SCOPE)

CONNECTIONS_ENDPOINT = "https://people.googleapis.com/v1/people/me/connections"
OTHER_CONTACTS_ENDPOINT = "https://people.googleapis.com/v1/otherContacts"
PERSON_FIELDS = "names,emailAddresses,phoneNumbers,nicknames,metadata"
OTHER_CONTACTS_READ_MASK = "names,emailAddresses,phoneNumbers,metadata"


class GooglePeopleAuthError(RuntimeError):
    """Raised when People API rejects the token or scopes."""


class GooglePeopleAPIError(RuntimeError):
    """Raised when People API returns an unexpected error."""


@dataclass
class GooglePerson:
    resource_name: str
    given_name: Optional[str]
    family_name: Optional[str]
    nickname: Optional[str]
    emails: List[Dict[str, Any]]
    phones: List[Dict[str, Any]]


@dataclass
class PeopleSyncPage:
    people: List[GooglePerson]
    next_sync_token: Optional[str]
    stale_sync_token: bool = False


def account_has_contacts_scopes(account: Dict[str, Any]) -> bool:
    metadata = account.get("metadata") or {}
    scopes = metadata.get("scopes") or []
    if isinstance(scopes, str):
        scopes = [segment for segment in scopes.split() if segment]
    granted = set(scopes)
    return all(scope in granted for scope in REQUIRED_CONTACTS_SCOPES)


def _primary_or_first(items: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    if not items:
        return None
    for item in items:
        metadata = item.get("metadata") or {}
        if metadata.get("primary"):
            return item
    return items[0]


def parse_person(raw: Dict[str, Any]) -> GooglePerson | None:
    resource_name = raw.get("resourceName")
    if not resource_name:
        return None

    names = raw.get("names") or []
    name = _primary_or_first(names) or {}
    nicknames = raw.get("nicknames") or []
    nickname_row = _primary_or_first(nicknames) or {}

    emails: List[Dict[str, Any]] = []
    seen_emails: set[str] = set()
    for item in raw.get("emailAddresses") or []:
        value = (item.get("value") or "").strip().lower()
        if not value or value in seen_emails:
            continue
        seen_emails.add(value)
        metadata = item.get("metadata") or {}
        emails.append(
            {
                "email": value,
                "label": item.get("type") or item.get("formattedType"),
                "is_primary": bool(metadata.get("primary")),
            }
        )

    phones: List[Dict[str, Any]] = []
    seen_phones: set[str] = set()
    for item in raw.get("phoneNumbers") or []:
        raw_value = (item.get("value") or item.get("canonicalForm") or "").strip()
        if not raw_value:
            continue
        e164 = item.get("canonicalForm") or to_e164(raw_value)
        key = e164 or raw_value
        if key in seen_phones:
            continue
        seen_phones.add(key)
        metadata = item.get("metadata") or {}
        phones.append(
            {
                "phone": raw_value,
                "phone_e164": e164 if e164 and str(e164).startswith("+") else to_e164(raw_value),
                "label": item.get("type") or item.get("formattedType"),
                "is_primary": bool(metadata.get("primary")),
            }
        )

    if not emails and not phones and not name.get("givenName") and not name.get("familyName"):
        return None

    return GooglePerson(
        resource_name=resource_name,
        given_name=name.get("givenName") or None,
        family_name=name.get("familyName") or None,
        nickname=nickname_row.get("value") or None,
        emails=emails,
        phones=phones,
    )


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: Dict[str, Any],
    access_token: str,
) -> Dict[str, Any]:
    response = await client.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    if response.status_code in (401, 403):
        raise GooglePeopleAuthError(
            f"People API unauthorized: {response.status_code} {response.text}"
        )
    if response.status_code == 410:
        return {"staleSyncToken": True}
    if response.status_code != httpx.codes.OK:
        raise GooglePeopleAPIError(
            f"People API failed: {response.status_code} {response.text}"
        )
    return response.json()


async def fetch_connections(
    access_token: str, sync_token: str | None = None
) -> PeopleSyncPage:
    people: List[GooglePerson] = []
    page_token: str | None = None
    next_sync_token: str | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            params: Dict[str, Any] = {
                "personFields": PERSON_FIELDS,
                "pageSize": 1000,
                "requestSyncToken": "true",
            }
            if sync_token and not page_token:
                params["syncToken"] = sync_token
            if page_token:
                params["pageToken"] = page_token
            data = await _get_json(client, CONNECTIONS_ENDPOINT, params, access_token)
            if data.get("staleSyncToken"):
                return PeopleSyncPage(people=[], next_sync_token=None, stale_sync_token=True)
            for raw in data.get("connections") or []:
                parsed = parse_person(raw)
                if parsed:
                    people.append(parsed)
            next_sync_token = data.get("nextSyncToken") or next_sync_token
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return PeopleSyncPage(people=people, next_sync_token=next_sync_token)


async def fetch_other_contacts(
    access_token: str, sync_token: str | None = None
) -> PeopleSyncPage:
    people: List[GooglePerson] = []
    page_token: str | None = None
    next_sync_token: str | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            params: Dict[str, Any] = {
                "readMask": OTHER_CONTACTS_READ_MASK,
                "pageSize": 1000,
                "requestSyncToken": "true",
            }
            if sync_token and not page_token:
                params["syncToken"] = sync_token
            if page_token:
                params["pageToken"] = page_token
            data = await _get_json(client, OTHER_CONTACTS_ENDPOINT, params, access_token)
            if data.get("staleSyncToken"):
                return PeopleSyncPage(people=[], next_sync_token=None, stale_sync_token=True)
            for raw in data.get("otherContacts") or []:
                parsed = parse_person(raw)
                if parsed:
                    people.append(parsed)
            next_sync_token = data.get("nextSyncToken") or next_sync_token
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return PeopleSyncPage(people=people, next_sync_token=next_sync_token)
