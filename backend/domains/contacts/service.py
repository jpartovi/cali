"""Contact import, merge, and search."""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from domains.calendars.repository import CalendarRepository
from domains.calendars.service import CalendarService
from domains.contacts.people import (
    GooglePeopleAPIError,
    GooglePeopleAuthError,
    GooglePerson,
    account_has_contacts_scopes,
    fetch_connections,
    fetch_other_contacts,
)
from domains.contacts.phone import to_e164
from domains.contacts.repository import ContactsRepository
from domains.contacts.schemas import (
    AppleContactImport,
    AppleImportResponse,
    ContactEmailResponse,
    ContactPhoneResponse,
    ContactResponse,
    ContactSource,
    GoogleAccountImportResult,
    GoogleImportResponse,
    ImportedEmail,
    ImportedPhone,
)
from utils.errors import GoogleCalendarAuthError, SupabaseStorageError

logger = logging.getLogger(__name__)

CONNECTIONS_SYNC_TOKEN_KEY = "people_connections_sync_token"
OTHER_CONTACTS_SYNC_TOKEN_KEY = "other_contacts_sync_token"


def compute_display_name(
    given_name: str | None,
    family_name: str | None,
    nickname: str | None,
    emails: Sequence[str] | None = None,
) -> str:
    parts = " ".join(part.strip() for part in (given_name, family_name) if part and part.strip())
    if parts:
        return parts
    if nickname and nickname.strip():
        return nickname.strip()
    if emails:
        first = emails[0]
        local = first.split("@", 1)[0].strip()
        if local:
            return local
    return "Unknown"


def _add_source(sources: Iterable[str] | None, source: str) -> List[str]:
    values = list(sources or [])
    if source not in values:
        values.append(source)
    return values


def _contact_needs_apple_patch(existing: Dict[str, Any], patch: Dict[str, Any]) -> bool:
    for key in ("given_name", "family_name", "nickname", "display_name", "apple_identifier"):
        if existing.get(key) != patch.get(key):
            return True
    return "apple" not in list(existing.get("sources") or [])


class ContactsService:
    """Merge Apple imports and Google People enrichment into the contacts store."""

    def __init__(
        self,
        repository: ContactsRepository | None = None,
        calendar_repository: CalendarRepository | None = None,
        calendar_service: CalendarService | None = None,
    ) -> None:
        self.repository = repository or ContactsRepository()
        self.calendar_repository = calendar_repository or CalendarRepository()
        self.calendar_service = calendar_service or CalendarService(
            repository=self.calendar_repository
        )

    def import_apple(self, user_id: str, contacts: List[AppleContactImport]) -> AppleImportResponse:
        imported = 0
        merged = 0
        emails_added = 0
        phones_added = 0

        apple_ids = [item.apple_identifier for item in contacts]
        emails = [email.email for item in contacts for email in item.emails]
        e164s: List[str] = []
        for item in contacts:
            for phone in item.phones:
                parsed = to_e164(phone.phone)
                if parsed:
                    e164s.append(parsed)

        by_apple = {
            row["apple_identifier"]: row
            for row in self.repository.get_by_apple_identifiers(user_id, apple_ids)
            if row.get("apple_identifier")
        }
        email_rows = self.repository.get_emails(user_id, emails)
        phone_rows = self.repository.get_phones_by_e164(user_id, e164s)
        contact_ids = list(
            {
                *[row["id"] for row in by_apple.values()],
                *[row["contact_id"] for row in email_rows],
                *[row["contact_id"] for row in phone_rows],
            }
        )
        by_id = {row["id"]: row for row in self.repository.get_by_ids(user_id, contact_ids)}
        email_to_contact = {
            row["email"]: by_id.get(row["contact_id"])
            for row in email_rows
            if row.get("contact_id") in by_id
        }
        e164_to_contact = {
            row["phone_e164"]: by_id.get(row["contact_id"])
            for row in phone_rows
            if row.get("phone_e164") and row.get("contact_id") in by_id
        }

        existing_emails_by_contact: Dict[str, set[str]] = {}
        existing_phones_by_contact: Dict[str, set[str]] = {}
        for row in self.repository.get_emails_for_contacts(user_id, contact_ids):
            existing_emails_by_contact.setdefault(row["contact_id"], set()).add(row["email"])
        for row in self.repository.get_phones_for_contacts(user_id, contact_ids):
            keys = existing_phones_by_contact.setdefault(row["contact_id"], set())
            keys.add(row["phone_raw"])
            if row.get("phone_e164"):
                keys.add(row["phone_e164"])

        now = self.repository.utc_now_iso()
        assignments: List[Tuple[AppleContactImport, Dict[str, Any] | None]] = []
        new_payloads: List[Dict[str, Any]] = []
        updates: List[Tuple[str, Dict[str, Any]]] = []

        for item in contacts:
            match = by_apple.get(item.apple_identifier)
            if match is None:
                for phone in item.phones:
                    parsed = to_e164(phone.phone)
                    if parsed and e164_to_contact.get(parsed):
                        match = e164_to_contact[parsed]
                        break
            if match is None:
                for email in item.emails:
                    if email_to_contact.get(email.email):
                        match = email_to_contact[email.email]
                        break

            display_name = compute_display_name(
                item.given_name,
                item.family_name,
                item.nickname,
                [email.email for email in item.emails],
            )
            if match is None:
                new_payloads.append(
                    {
                        "user_id": user_id,
                        "given_name": item.given_name,
                        "family_name": item.family_name,
                        "nickname": item.nickname,
                        "display_name": display_name,
                        "apple_identifier": item.apple_identifier,
                        "sources": ["apple"],
                        "last_imported_at": now,
                    }
                )
                assignments.append((item, None))
                imported += 1
            else:
                patch = {
                    "given_name": item.given_name,
                    "family_name": item.family_name,
                    "nickname": item.nickname,
                    "display_name": display_name,
                    "apple_identifier": item.apple_identifier,
                    "sources": _add_source(match.get("sources"), "apple"),
                    "last_imported_at": now,
                }
                if _contact_needs_apple_patch(match, patch):
                    updates.append((match["id"], patch))
                assignments.append((item, match))
                merged += 1
                by_id[match["id"]] = match
                by_apple[item.apple_identifier] = match

        inserted_by_apple = {
            row["apple_identifier"]: row
            for row in self.repository.insert_contacts(new_payloads)
            if row.get("apple_identifier")
        }
        for item, match in assignments:
            if match is not None:
                continue
            created = inserted_by_apple.get(item.apple_identifier)
            if created is None:
                raise SupabaseStorageError("Contact insert returned no row.")
            by_id[created["id"]] = created
            by_apple[item.apple_identifier] = created

        for contact_id, patch in updates:
            updated = self.repository.update_contact(user_id, contact_id, patch)
            by_id[contact_id] = updated
            if updated.get("apple_identifier"):
                by_apple[updated["apple_identifier"]] = updated

        email_rows: List[Dict[str, Any]] = []
        phone_rows: List[Dict[str, Any]] = []
        for item, match in assignments:
            contact = match or by_apple[item.apple_identifier]
            added_e, added_p = self._collect_channels(
                user_id=user_id,
                contact=contact,
                emails=item.emails,
                phones=item.phones,
                source="apple",
                existing_emails=existing_emails_by_contact.setdefault(contact["id"], set()),
                existing_phones=existing_phones_by_contact.setdefault(contact["id"], set()),
                email_to_contact=email_to_contact,
                e164_to_contact=e164_to_contact,
                email_rows=email_rows,
                phone_rows=phone_rows,
            )
            emails_added += added_e
            phones_added += added_p

        self.repository.insert_emails(email_rows)
        self.repository.insert_phones(phone_rows)

        return AppleImportResponse(
            imported=imported,
            merged=merged,
            emails_added=emails_added,
            phones_added=phones_added,
        )

    async def import_google(self, user_id: str) -> GoogleImportResponse:
        accounts = self.calendar_repository.get_accounts(user_id)
        results: List[GoogleAccountImportResult] = []
        reauth_ids: List[str] = []

        for account in accounts:
            account_id = account["id"]
            email = account.get("email")
            if not account_has_contacts_scopes(account):
                results.append(
                    GoogleAccountImportResult(
                        account_id=account_id,
                        email=email,
                        needs_reauth=True,
                        error="Missing Google Contacts scopes. Re-link this account.",
                    )
                )
                reauth_ids.append(account_id)
                continue

            try:
                access_token = await self.calendar_service._ensure_access_token(account)
            except GoogleCalendarAuthError as exc:
                results.append(
                    GoogleAccountImportResult(
                        account_id=account_id,
                        email=email,
                        needs_reauth=True,
                        error=str(exc),
                    )
                )
                reauth_ids.append(account_id)
                continue

            metadata = dict(account.get("metadata") or {})
            try:
                created, enriched, emails_added, phones_added, metadata = await self._sync_account(
                    user_id=user_id,
                    access_token=access_token,
                    metadata=metadata,
                )
            except GooglePeopleAuthError as exc:
                logger.warning("People API auth failed account_id=%s: %s", account_id, exc)
                results.append(
                    GoogleAccountImportResult(
                        account_id=account_id,
                        email=email,
                        needs_reauth=True,
                        error="Google Contacts access was denied. Re-link this account.",
                    )
                )
                reauth_ids.append(account_id)
                continue
            except (GooglePeopleAPIError, SupabaseStorageError) as exc:
                logger.error("People sync failed account_id=%s: %s", account_id, exc)
                results.append(
                    GoogleAccountImportResult(
                        account_id=account_id,
                        email=email,
                        error=str(exc),
                    )
                )
                continue

            try:
                self.repository.patch_google_account_metadata(user_id, account_id, metadata)
            except SupabaseStorageError:
                logger.exception("Failed to persist People sync tokens account_id=%s", account_id)

            results.append(
                GoogleAccountImportResult(
                    account_id=account_id,
                    email=email,
                    created=created,
                    enriched=enriched,
                    emails_added=emails_added,
                    phones_added=phones_added,
                )
            )

        return GoogleImportResponse(
            accounts=results,
            needs_reauth=bool(reauth_ids),
            needs_reauth_account_ids=reauth_ids,
        )

    def search(self, user_id: str, query: str) -> List[ContactResponse]:
        rows = self.repository.search_contacts(user_id, query)
        return self._hydrate(user_id, rows)

    def list_contacts(self, user_id: str) -> List[ContactResponse]:
        rows = self.repository.list_contacts(user_id)
        return self._hydrate(user_id, rows)

    async def _sync_account(
        self,
        user_id: str,
        access_token: str,
        metadata: Dict[str, Any],
    ) -> Tuple[int, int, int, int, Dict[str, Any]]:
        connections_token = metadata.get(CONNECTIONS_SYNC_TOKEN_KEY)
        other_token = metadata.get(OTHER_CONTACTS_SYNC_TOKEN_KEY)

        connections = await fetch_connections(access_token, connections_token)
        if connections.stale_sync_token:
            connections = await fetch_connections(access_token, None)
        other = await fetch_other_contacts(access_token, other_token)
        if other.stale_sync_token:
            other = await fetch_other_contacts(access_token, None)

        people = list(connections.people) + list(other.people)
        created = enriched = emails_added = phones_added = 0
        for person in people:
            was_new, added_e, added_p = self._upsert_google_person(user_id, person)
            if was_new:
                created += 1
            else:
                enriched += 1
            emails_added += added_e
            phones_added += added_p

        if connections.next_sync_token:
            metadata[CONNECTIONS_SYNC_TOKEN_KEY] = connections.next_sync_token
        if other.next_sync_token:
            metadata[OTHER_CONTACTS_SYNC_TOKEN_KEY] = other.next_sync_token
        return created, enriched, emails_added, phones_added, metadata

    def _upsert_google_person(
        self, user_id: str, person: GooglePerson
    ) -> Tuple[bool, int, int]:
        emails = [
            ImportedEmail(
                email=item["email"],
                label=item.get("label"),
                is_primary=bool(item.get("is_primary")),
            )
            for item in person.emails
        ]
        phones = [
            ImportedPhone(
                phone=item["phone"],
                label=item.get("label"),
                is_primary=bool(item.get("is_primary")),
            )
            for item in person.phones
        ]
        e164s = [to_e164(phone.phone) for phone in phones]
        e164s = [value for value in e164s if value]

        named = self.repository.get_by_google_resource_names(user_id, [person.resource_name])
        match = named[0] if named else None
        if match is None and e164s:
            phone_rows = self.repository.get_phones_by_e164(user_id, e164s)
            if phone_rows:
                found = self.repository.get_by_ids(user_id, [phone_rows[0]["contact_id"]])
                match = found[0] if found else None
        if match is None and emails:
            email_rows = self.repository.get_emails(user_id, [email.email for email in emails])
            if email_rows:
                found = self.repository.get_by_ids(user_id, [email_rows[0]["contact_id"]])
                match = found[0] if found else None

        now = self.repository.utc_now_iso()
        was_new = match is None
        if match is None:
            match = self.repository.insert_contact(
                {
                    "user_id": user_id,
                    "given_name": person.given_name,
                    "family_name": person.family_name,
                    "nickname": person.nickname,
                    "display_name": compute_display_name(
                        person.given_name,
                        person.family_name,
                        person.nickname,
                        [email.email for email in emails],
                    ),
                    "google_resource_name": person.resource_name,
                    "sources": ["google"],
                    "last_imported_at": now,
                }
            )
        else:
            patch: Dict[str, Any] = {
                "sources": _add_source(match.get("sources"), "google"),
                "last_imported_at": now,
            }
            existing_resource = match.get("google_resource_name")
            if not existing_resource:
                patch["google_resource_name"] = person.resource_name
            elif existing_resource != person.resource_name:
                # Another Google person is already linked; still add missing channels.
                patch.pop("google_resource_name", None)
            match = self.repository.update_contact(user_id, match["id"], patch)

        existing_emails = {
            row["email"]
            for row in self.repository.get_emails_for_contacts(user_id, [match["id"]])
        }
        existing_phones: set[str] = set()
        for row in self.repository.get_phones_for_contacts(user_id, [match["id"]]):
            existing_phones.add(row["phone_raw"])
            if row.get("phone_e164"):
                existing_phones.add(row["phone_e164"])

        email_rows: List[Dict[str, Any]] = []
        phone_rows: List[Dict[str, Any]] = []
        added_e, added_p = self._collect_channels(
            user_id=user_id,
            contact=match,
            emails=emails,
            phones=phones,
            source="google",
            existing_emails=existing_emails,
            existing_phones=existing_phones,
            email_to_contact={},
            e164_to_contact={},
            email_rows=email_rows,
            phone_rows=phone_rows,
        )
        self.repository.insert_emails(email_rows)
        self.repository.insert_phones(phone_rows)
        return was_new, added_e, added_p

    def _collect_channels(
        self,
        *,
        user_id: str,
        contact: Dict[str, Any],
        emails: Sequence[ImportedEmail],
        phones: Sequence[ImportedPhone],
        source: ContactSource,
        existing_emails: set[str],
        existing_phones: set[str],
        email_to_contact: Dict[str, Dict[str, Any] | None],
        e164_to_contact: Dict[str, Dict[str, Any] | None],
        email_rows: List[Dict[str, Any]],
        phone_rows: List[Dict[str, Any]],
    ) -> Tuple[int, int]:
        emails_added = 0
        phones_added = 0
        for email in emails:
            if email.email in existing_emails:
                continue
            existing_emails.add(email.email)
            email_to_contact[email.email] = contact
            email_rows.append(
                {
                    "user_id": user_id,
                    "contact_id": contact["id"],
                    "email": email.email,
                    "label": email.label,
                    "is_primary": email.is_primary,
                    "source": source,
                }
            )
            emails_added += 1

        for phone in phones:
            e164 = to_e164(phone.phone)
            if phone.phone in existing_phones or (e164 and e164 in existing_phones):
                continue
            existing_phones.add(phone.phone)
            if e164:
                existing_phones.add(e164)
                e164_to_contact[e164] = contact
            phone_rows.append(
                {
                    "user_id": user_id,
                    "contact_id": contact["id"],
                    "phone_raw": phone.phone,
                    "phone_e164": e164,
                    "label": phone.label,
                    "is_primary": phone.is_primary,
                    "source": source,
                }
            )
            phones_added += 1

        return emails_added, phones_added

    def _hydrate(self, user_id: str, contacts: List[Dict[str, Any]]) -> List[ContactResponse]:
        if not contacts:
            return []
        ids = [row["id"] for row in contacts]
        emails = self.repository.get_emails_for_contacts(user_id, ids)
        phones = self.repository.get_phones_for_contacts(user_id, ids)
        emails_by_id: Dict[str, List[ContactEmailResponse]] = {}
        phones_by_id: Dict[str, List[ContactPhoneResponse]] = {}
        for row in emails:
            emails_by_id.setdefault(row["contact_id"], []).append(
                ContactEmailResponse(
                    email=row["email"],
                    label=row.get("label"),
                    is_primary=bool(row.get("is_primary")),
                    source=row["source"],
                )
            )
        for row in phones:
            phones_by_id.setdefault(row["contact_id"], []).append(
                ContactPhoneResponse(
                    phone_raw=row["phone_raw"],
                    phone_e164=row.get("phone_e164"),
                    label=row.get("label"),
                    is_primary=bool(row.get("is_primary")),
                    source=row["source"],
                )
            )
        return [
            ContactResponse(
                id=row["id"],
                user_id=row["user_id"],
                given_name=row.get("given_name"),
                family_name=row.get("family_name"),
                nickname=row.get("nickname"),
                display_name=row["display_name"],
                apple_identifier=row.get("apple_identifier"),
                google_resource_name=row.get("google_resource_name"),
                sources=list(row.get("sources") or []),
                emails=emails_by_id.get(row["id"], []),
                phones=phones_by_id.get(row["id"], []),
                last_imported_at=row.get("last_imported_at"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in contacts
        ]
