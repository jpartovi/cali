-- Add access_role column to calendars table
-- Stores Google Calendar access role: "reader", "writer", or "owner"
-- This is used to filter calendars for agent operations (only writable calendars should be shown)

alter table public.calendars
    add column if not exists access_role text;

-- Add comment explaining the column
comment on column public.calendars.access_role is 'Google Calendar access role: reader, writer, or owner. Used to determine if user can create/update/delete events in this calendar.';

-- Add index for filtering by access role
create index if not exists idx_calendars_access_role 
    on public.calendars using btree (access_role);
