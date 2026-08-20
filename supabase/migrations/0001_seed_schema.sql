-- Seed schema matching the actual database structure
-- Users, Google Accounts, and Calendars tables

-- Enable required extensions
create extension if not exists "pgcrypto";

-- Users table anchored to Supabase auth.users (phone OTP auth)
create table if not exists public.users (
    id uuid not null,
    phone text not null,
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint users_pkey primary key (id),
    constraint users_phone_key unique (phone),
    constraint users_id_fkey foreign key (id) references auth.users (id) on delete cascade
);

-- Trigger function to keep updated_at current
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := timezone('utc'::text, now());
    return new;
end;
$$;

-- Create trigger for users table
do $$
begin
    if not exists (
        select 1 from pg_trigger 
        where tgname = 'handle_users_updated_at' 
        and tgrelid = 'public.users'::regclass
    ) then
        create trigger handle_users_updated_at
            before update on public.users
            for each row
            execute function public.set_updated_at();
    end if;
end $$;

-- Google accounts linked to a user (one-to-many)
create table if not exists public.google_accounts (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    google_user_id text not null,
    email text not null,
    display_name text,
    avatar_url text,
    access_token text,
    refresh_token text,
    expires_at timestamptz,
    metadata jsonb default '{}'::jsonb,
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint google_accounts_pkey primary key (id),
    constraint google_accounts_user_id_google_user_id_key unique (user_id, google_user_id),
    constraint google_accounts_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade
);

-- Create trigger for google_accounts table
do $$
begin
    if not exists (
        select 1 from pg_trigger 
        where tgname = 'handle_google_accounts_updated_at' 
        and tgrelid = 'public.google_accounts'::regclass
    ) then
        create trigger handle_google_accounts_updated_at
            before update on public.google_accounts
            for each row
            execute function public.set_updated_at();
    end if;
end $$;

-- Calendars table
create table if not exists public.calendars (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    google_calendar_id text not null,
    name text not null,
    description text,
    color text,
    is_primary boolean default false,
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint calendars_pkey primary key (id),
    constraint calendars_user_id_google_calendar_id_key unique (user_id, google_calendar_id),
    constraint calendars_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade
);

-- Create indexes for calendars
create index if not exists idx_calendars_user_id on public.calendars using btree (user_id);
create index if not exists idx_calendars_google_id on public.calendars using btree (google_calendar_id);
create index if not exists idx_calendars_is_primary on public.calendars using btree (user_id, is_primary);

-- Create trigger for calendars table
do $$
begin
    if not exists (
        select 1 from pg_trigger 
        where tgname = 'handle_calendars_updated_at' 
        and tgrelid = 'public.calendars'::regclass
    ) then
        create trigger handle_calendars_updated_at
            before update on public.calendars
            for each row
            execute function public.set_updated_at();
    end if;
end $$;

-- Row Level Security configuration
alter table public.users enable row level security;
alter table public.google_accounts enable row level security;
alter table public.calendars enable row level security;

-- Users table policies
drop policy if exists "Users can view their own profile" on public.users;
create policy "Users can view their own profile"
    on public.users
    for select
    using (auth.uid() = id);

drop policy if exists "Users can update their own profile" on public.users;
create policy "Users can update their own profile"
    on public.users
    for update
    using (auth.uid() = id);

drop policy if exists "Users can insert their own profile" on public.users;
create policy "Users can insert their own profile"
    on public.users
    for insert
    with check (auth.uid() = id);

-- Google accounts table policies
drop policy if exists "Users can view their own Google accounts" on public.google_accounts;
create policy "Users can view their own Google accounts"
    on public.google_accounts
    for select
    using (auth.uid() = user_id);

drop policy if exists "Users can insert their own Google accounts" on public.google_accounts;
create policy "Users can insert their own Google accounts"
    on public.google_accounts
    for insert
    with check (auth.uid() = user_id);

drop policy if exists "Users can update their own Google accounts" on public.google_accounts;
create policy "Users can update their own Google accounts"
    on public.google_accounts
    for update
    using (auth.uid() = user_id);

drop policy if exists "Users can delete their own Google accounts" on public.google_accounts;
create policy "Users can delete their own Google accounts"
    on public.google_accounts
    for delete
    using (auth.uid() = user_id);

-- Calendars table policies
drop policy if exists "Users can view their own calendars" on public.calendars;
create policy "Users can view their own calendars"
    on public.calendars
    for select
    using (auth.uid() = user_id);

drop policy if exists "Users can insert their own calendars" on public.calendars;
create policy "Users can insert their own calendars"
    on public.calendars
    for insert
    with check (auth.uid() = user_id);

drop policy if exists "Users can update their own calendars" on public.calendars;
create policy "Users can update their own calendars"
    on public.calendars
    for update
    using (auth.uid() = user_id);

drop policy if exists "Users can delete their own calendars" on public.calendars;
create policy "Users can delete their own calendars"
    on public.calendars
    for delete
    using (auth.uid() = user_id);
