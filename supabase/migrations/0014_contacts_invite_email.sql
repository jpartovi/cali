-- Apple-only contacts: one row per card, plus invite_email.

delete from public.contacts
where apple_identifier is null;

drop table if exists public.contact_phones;
drop table if exists public.contact_emails;

drop index if exists public.contacts_user_google_resource_name_key;

alter table public.contacts
    drop column if exists google_resource_name;

alter table public.contacts
    drop column if exists sources;

alter table public.contacts
    add column if not exists invite_email text;

alter table public.contacts
    drop constraint if exists contacts_invite_email_lower_check;

alter table public.contacts
    add constraint contacts_invite_email_lower_check
    check (invite_email is null or invite_email = lower(invite_email));

alter table public.contacts
    alter column apple_identifier set not null;
