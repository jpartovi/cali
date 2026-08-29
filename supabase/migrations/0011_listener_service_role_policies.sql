-- Listener tables had RLS enabled with no write policies. Requests that are
-- not treated as service_role (or that lose BYPASSRLS) then fail with 403
-- and webhooks look like unknown channels. Grant the backend role explicitly.

do $$
declare
    tbl text;
begin
    foreach tbl in array array[
        'calendar_watches',
        'calendar_event_snapshots',
        'calendar_jobs',
        'processed_change_keys',
        'calendar_write_suppressions'
    ]
    loop
        execute format(
            'drop policy if exists "service_role_all_%s" on public.%I',
            tbl,
            tbl
        );
        execute format(
            'create policy "service_role_all_%s" on public.%I for all to service_role using (true) with check (true)',
            tbl,
            tbl
        );
    end loop;
end $$;
