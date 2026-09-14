-- Per-user contacts store (Apple person graph + Google email/phone enrichment)

create extension if not exists pg_trgm;

create table if not exists public.contacts (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    given_name text,
    family_name text,
    nickname text,
    display_name text not null,
    apple_identifier text,
    google_resource_name text,
    sources text[] not null default '{}'::text[],
    last_imported_at timestamptz,
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint contacts_pkey primary key (id),
    constraint contacts_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade,
    constraint contacts_sources_check check (sources <@ array['apple', 'google', 'manual']::text[])
);

create unique index if not exists contacts_user_apple_identifier_key
    on public.contacts (user_id, apple_identifier)
    where apple_identifier is not null;

create unique index if not exists contacts_user_google_resource_name_key
    on public.contacts (user_id, google_resource_name)
    where google_resource_name is not null;

create index if not exists contacts_user_id_idx
    on public.contacts using btree (user_id);

create index if not exists contacts_display_name_trgm_idx
    on public.contacts using gin (display_name gin_trgm_ops);

create index if not exists contacts_nickname_trgm_idx
    on public.contacts using gin (nickname gin_trgm_ops);

do $$
begin
    if not exists (
        select 1 from pg_trigger
        where tgname = 'handle_contacts_updated_at'
        and tgrelid = 'public.contacts'::regclass
    ) then
        create trigger handle_contacts_updated_at
            before update on public.contacts
            for each row
            execute function public.set_updated_at();
    end if;
end $$;

create table if not exists public.contact_emails (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    contact_id uuid not null,
    email text not null,
    label text,
    is_primary boolean not null default false,
    source text not null,
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint contact_emails_pkey primary key (id),
    constraint contact_emails_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade,
    constraint contact_emails_contact_id_fkey foreign key (contact_id) references public.contacts (id) on delete cascade,
    constraint contact_emails_source_check check (source in ('apple', 'google', 'manual')),
    constraint contact_emails_email_lower_check check (email = lower(email)),
    constraint contact_emails_contact_email_key unique (contact_id, email)
);

create index if not exists contact_emails_user_email_idx
    on public.contact_emails using btree (user_id, email);

create index if not exists contact_emails_contact_id_idx
    on public.contact_emails using btree (contact_id);

do $$
begin
    if not exists (
        select 1 from pg_trigger
        where tgname = 'handle_contact_emails_updated_at'
        and tgrelid = 'public.contact_emails'::regclass
    ) then
        create trigger handle_contact_emails_updated_at
            before update on public.contact_emails
            for each row
            execute function public.set_updated_at();
    end if;
end $$;

create table if not exists public.contact_phones (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    contact_id uuid not null,
    phone_raw text not null,
    phone_e164 text,
    label text,
    is_primary boolean not null default false,
    source text not null,
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint contact_phones_pkey primary key (id),
    constraint contact_phones_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade,
    constraint contact_phones_contact_id_fkey foreign key (contact_id) references public.contacts (id) on delete cascade,
    constraint contact_phones_source_check check (source in ('apple', 'google', 'manual')),
    constraint contact_phones_contact_raw_key unique (contact_id, phone_raw)
);

create unique index if not exists contact_phones_contact_e164_key
    on public.contact_phones (contact_id, phone_e164)
    where phone_e164 is not null;

create index if not exists contact_phones_user_e164_idx
    on public.contact_phones using btree (user_id, phone_e164)
    where phone_e164 is not null;

create index if not exists contact_phones_contact_id_idx
    on public.contact_phones using btree (contact_id);

do $$
begin
    if not exists (
        select 1 from pg_trigger
        where tgname = 'handle_contact_phones_updated_at'
        and tgrelid = 'public.contact_phones'::regclass
    ) then
        create trigger handle_contact_phones_updated_at
            before update on public.contact_phones
            for each row
            execute function public.set_updated_at();
    end if;
end $$;

alter table public.contacts enable row level security;
alter table public.contact_emails enable row level security;
alter table public.contact_phones enable row level security;

drop policy if exists "Users can view their own contacts" on public.contacts;
create policy "Users can view their own contacts"
    on public.contacts for select
    using (auth.uid() = user_id);

drop policy if exists "Users can insert their own contacts" on public.contacts;
create policy "Users can insert their own contacts"
    on public.contacts for insert
    with check (auth.uid() = user_id);

drop policy if exists "Users can update their own contacts" on public.contacts;
create policy "Users can update their own contacts"
    on public.contacts for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists "Users can delete their own contacts" on public.contacts;
create policy "Users can delete their own contacts"
    on public.contacts for delete
    using (auth.uid() = user_id);

drop policy if exists "Users can view their own contact emails" on public.contact_emails;
create policy "Users can view their own contact emails"
    on public.contact_emails for select
    using (auth.uid() = user_id);

drop policy if exists "Users can insert their own contact emails" on public.contact_emails;
create policy "Users can insert their own contact emails"
    on public.contact_emails for insert
    with check (auth.uid() = user_id);

drop policy if exists "Users can update their own contact emails" on public.contact_emails;
create policy "Users can update their own contact emails"
    on public.contact_emails for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists "Users can delete their own contact emails" on public.contact_emails;
create policy "Users can delete their own contact emails"
    on public.contact_emails for delete
    using (auth.uid() = user_id);

drop policy if exists "Users can view their own contact phones" on public.contact_phones;
create policy "Users can view their own contact phones"
    on public.contact_phones for select
    using (auth.uid() = user_id);

drop policy if exists "Users can insert their own contact phones" on public.contact_phones;
create policy "Users can insert their own contact phones"
    on public.contact_phones for insert
    with check (auth.uid() = user_id);

drop policy if exists "Users can update their own contact phones" on public.contact_phones;
create policy "Users can update their own contact phones"
    on public.contact_phones for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists "Users can delete their own contact phones" on public.contact_phones;
create policy "Users can delete their own contact phones"
    on public.contact_phones for delete
    using (auth.uid() = user_id);

drop policy if exists "service_role_all_contacts" on public.contacts;
create policy "service_role_all_contacts"
    on public.contacts for all to service_role
    using (true) with check (true);

drop policy if exists "service_role_all_contact_emails" on public.contact_emails;
create policy "service_role_all_contact_emails"
    on public.contact_emails for all to service_role
    using (true) with check (true);

drop policy if exists "service_role_all_contact_phones" on public.contact_phones;
create policy "service_role_all_contact_phones"
    on public.contact_phones for all to service_role
    using (true) with check (true);
