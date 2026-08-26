-- Calendar listener: Google watch channels, event projection, due jobs, trigger idempotency

create table if not exists public.calendar_watches (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    google_account_id uuid not null,
    kind text not null,
    google_calendar_id text,
    channel_id text not null,
    resource_id text,
    expiration timestamptz,
    sync_token text,
    last_synced_at timestamptz,
    sync_lock_at timestamptz,
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint calendar_watches_pkey primary key (id),
    constraint calendar_watches_kind_check check (kind in ('events', 'calendar_list')),
    constraint calendar_watches_channel_id_key unique (channel_id),
    constraint calendar_watches_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade,
    constraint calendar_watches_google_account_id_fkey foreign key (google_account_id) references public.google_accounts (id) on delete cascade
);

create unique index if not exists calendar_watches_account_kind_calendar
    on public.calendar_watches (google_account_id, kind, coalesce(google_calendar_id, ''));

create index if not exists calendar_watches_user_id
    on public.calendar_watches using btree (user_id);

create index if not exists calendar_watches_expiration
    on public.calendar_watches using btree (expiration);

do $$
begin
    if not exists (
        select 1 from pg_trigger
        where tgname = 'handle_calendar_watches_updated_at'
        and tgrelid = 'public.calendar_watches'::regclass
    ) then
        create trigger handle_calendar_watches_updated_at
            before update on public.calendar_watches
            for each row
            execute function public.set_updated_at();
    end if;
end $$;

create table if not exists public.calendar_event_snapshots (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    google_account_id uuid not null,
    google_calendar_id text not null,
    google_event_id text not null,
    ical_uid text,
    etag text,
    status text not null default 'confirmed',
    google_updated_at timestamptz,
    recurring_event_id text,
    title text,
    location text,
    start_at timestamptz,
    end_at timestamptz,
    is_all_day boolean not null default false,
    timezone text,
    organizer_email text,
    organizer_self boolean not null default false,
    self_response text,
    attendees jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint calendar_event_snapshots_pkey primary key (id),
    constraint calendar_event_snapshots_status_check check (status in ('confirmed', 'tentative', 'cancelled')),
    constraint calendar_event_snapshots_user_calendar_event_key unique (user_id, google_calendar_id, google_event_id),
    constraint calendar_event_snapshots_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade,
    constraint calendar_event_snapshots_google_account_id_fkey foreign key (google_account_id) references public.google_accounts (id) on delete cascade
);

create index if not exists calendar_event_snapshots_user_start
    on public.calendar_event_snapshots using btree (user_id, start_at);

create index if not exists calendar_event_snapshots_recurring
    on public.calendar_event_snapshots using btree (user_id, google_calendar_id, recurring_event_id);

create index if not exists calendar_event_snapshots_end_at
    on public.calendar_event_snapshots using btree (end_at);

do $$
begin
    if not exists (
        select 1 from pg_trigger
        where tgname = 'handle_calendar_event_snapshots_updated_at'
        and tgrelid = 'public.calendar_event_snapshots'::regclass
    ) then
        create trigger handle_calendar_event_snapshots_updated_at
            before update on public.calendar_event_snapshots
            for each row
            execute function public.set_updated_at();
    end if;
end $$;

create table if not exists public.calendar_jobs (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    google_calendar_id text,
    google_event_id text,
    kind text not null,
    run_at timestamptz not null,
    status text not null default 'pending',
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint calendar_jobs_pkey primary key (id),
    constraint calendar_jobs_kind_check check (kind in ('event_start', 'event_end', 'watch_renew')),
    constraint calendar_jobs_status_check check (status in ('pending', 'done', 'cancelled')),
    constraint calendar_jobs_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade
);

create unique index if not exists calendar_jobs_pending_event_kind
    on public.calendar_jobs (user_id, coalesce(google_event_id, ''), kind)
    where status = 'pending' and kind in ('event_start', 'event_end');

create unique index if not exists calendar_jobs_pending_watch_renew
    on public.calendar_jobs (user_id, (payload->>'watch_id'), kind)
    where status = 'pending' and kind = 'watch_renew';

create index if not exists calendar_jobs_due
    on public.calendar_jobs using btree (status, run_at);

do $$
begin
    if not exists (
        select 1 from pg_trigger
        where tgname = 'handle_calendar_jobs_updated_at'
        and tgrelid = 'public.calendar_jobs'::regclass
    ) then
        create trigger handle_calendar_jobs_updated_at
            before update on public.calendar_jobs
            for each row
            execute function public.set_updated_at();
    end if;
end $$;

create table if not exists public.processed_change_keys (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    google_event_id text not null,
    kind text not null,
    fingerprint text not null,
    created_at timestamptz not null default timezone('utc'::text, now()),
    constraint processed_change_keys_pkey primary key (id),
    constraint processed_change_keys_unique unique (user_id, google_event_id, kind, fingerprint),
    constraint processed_change_keys_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade
);

create index if not exists processed_change_keys_created_at
    on public.processed_change_keys using btree (created_at);

create table if not exists public.calendar_write_suppressions (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    google_calendar_id text not null,
    google_event_id text not null,
    etag text,
    created_at timestamptz not null default timezone('utc'::text, now()),
    constraint calendar_write_suppressions_pkey primary key (id),
    constraint calendar_write_suppressions_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade
);

create index if not exists calendar_write_suppressions_lookup
    on public.calendar_write_suppressions using btree (user_id, google_calendar_id, google_event_id, created_at);

alter table public.calendar_watches enable row level security;
alter table public.calendar_event_snapshots enable row level security;
alter table public.calendar_jobs enable row level security;
alter table public.processed_change_keys enable row level security;
alter table public.calendar_write_suppressions enable row level security;

drop policy if exists "Users can view their own event snapshots" on public.calendar_event_snapshots;
create policy "Users can view their own event snapshots"
    on public.calendar_event_snapshots
    for select
    using (auth.uid() = user_id);
