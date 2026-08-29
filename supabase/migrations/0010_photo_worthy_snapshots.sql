alter table public.calendar_event_snapshots
    add column if not exists is_photo_worthy boolean not null default true;
