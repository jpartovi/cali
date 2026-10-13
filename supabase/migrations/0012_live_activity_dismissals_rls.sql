-- 0009 created this table locally; Noon never got it, so closed-app
-- Live Activity starts 500ed in has_dismissal. Idempotent for both.

create table if not exists public.live_activity_dismissals (
    user_id uuid not null,
    event_id text not null,
    created_at timestamptz not null default timezone('utc'::text, now()),
    constraint live_activity_dismissals_pkey primary key (user_id, event_id),
    constraint live_activity_dismissals_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade
);

alter table public.live_activity_dismissals enable row level security;

drop policy if exists "service_role_all_live_activity_dismissals" on public.live_activity_dismissals;
create policy "service_role_all_live_activity_dismissals"
    on public.live_activity_dismissals
    for all
    to service_role
    using (true)
    with check (true);
