-- Event descriptions in snapshots, so the agent's read_schedule can be served from
-- calendar_event_snapshots instead of a live Google listing.
alter table public.calendar_event_snapshots
    add column if not exists description text;
