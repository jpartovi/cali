-- Days outside the listener's live window (yesterday through 14 days ahead) that have been
-- loaded into calendar_event_snapshots on demand, per calendar. The Google watch keeps those
-- rows in sync like the live window, so a covered day with no rows really has no events.
-- starts_at/ends_at are the day's bounds in the user's timezone when it was loaded.

create table if not exists public.calendar_snapshot_coverage (
    user_id uuid not null,
    google_calendar_id text not null,
    day date not null,
    starts_at timestamptz not null,
    ends_at timestamptz not null,
    fetched_at timestamptz not null default timezone('utc'::text, now()),
    last_viewed_at timestamptz not null default timezone('utc'::text, now()),
    constraint calendar_snapshot_coverage_pkey primary key (user_id, google_calendar_id, day),
    constraint calendar_snapshot_coverage_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade
);

create index if not exists calendar_snapshot_coverage_bounds
    on public.calendar_snapshot_coverage using btree (user_id, google_calendar_id, starts_at);

create index if not exists calendar_snapshot_coverage_last_viewed
    on public.calendar_snapshot_coverage using btree (last_viewed_at);

alter table public.calendar_snapshot_coverage enable row level security;

drop policy if exists "Users can view their own snapshot coverage" on public.calendar_snapshot_coverage;
create policy "Users can view their own snapshot coverage"
    on public.calendar_snapshot_coverage
    for select
    using (auth.uid() = user_id);

drop policy if exists "service_role_all_calendar_snapshot_coverage" on public.calendar_snapshot_coverage;
create policy "service_role_all_calendar_snapshot_coverage"
    on public.calendar_snapshot_coverage
    for all to service_role using (true) with check (true);

-- The app catches up on rows changed since it last looked, and pruning checks start_at
create index if not exists calendar_event_snapshots_user_updated
    on public.calendar_event_snapshots using btree (user_id, updated_at);

create index if not exists calendar_event_snapshots_start_at
    on public.calendar_event_snapshots using btree (start_at);

-- Drop coverage nobody has looked at lately, then rows outside the live window that no
-- remaining coverage holds onto
create or replace function public.prune_calendar_snapshots(
    keep_from timestamptz,
    keep_until timestamptz,
    viewed_after timestamptz
)
returns void
language sql
security definer
set search_path = public
as $$
    delete from public.calendar_snapshot_coverage
    where last_viewed_at < viewed_after;

    delete from public.calendar_event_snapshots s
    where (s.end_at < keep_from or s.start_at >= keep_until)
      and not exists (
          select 1
          from public.calendar_snapshot_coverage c
          where c.user_id = s.user_id
            and c.google_calendar_id = s.google_calendar_id
            and c.starts_at < s.end_at
            and c.ends_at > s.start_at
      );
$$;

revoke all on function public.prune_calendar_snapshots(timestamptz, timestamptz, timestamptz) from public, anon, authenticated;
grant execute on function public.prune_calendar_snapshots(timestamptz, timestamptz, timestamptz) to service_role;
