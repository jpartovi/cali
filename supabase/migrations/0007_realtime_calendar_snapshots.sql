-- Publish schedule projection tables so the iOS app can subscribe while open.

do $$
begin
    if not exists (
        select 1
        from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'calendar_event_snapshots'
    ) then
        execute 'alter publication supabase_realtime add table public.calendar_event_snapshots';
    end if;

    if not exists (
        select 1
        from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'calendars'
    ) then
        execute 'alter publication supabase_realtime add table public.calendars';
    end if;
end $$;

alter table public.calendar_event_snapshots replica identity full;
alter table public.calendars replica identity full;
