-- Thumbs up/down (good/bad) on agent results, with an optional note on what went wrong, replacing the free-form agent_feedback table (never used).
-- request_id is the run's LangSmith trace id; calendar_snapshot is the user's calendar
-- (from calendar_event_snapshots) when they rated, before any accepted change lands.

drop table if exists public.agent_feedback;

create table if not exists public.agent_ratings (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    request_id uuid not null,
    rating text not null,
    note text,
    query text,
    response jsonb,
    calendar_snapshot jsonb,
    app_version text,
    app_build text,
    status text not null default 'open',
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint agent_ratings_pkey primary key (id),
    constraint agent_ratings_user_request_key unique (user_id, request_id),
    constraint agent_ratings_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade,
    constraint agent_ratings_rating_check check (rating in ('good', 'bad')),
    constraint agent_ratings_status_check check (status in ('open', 'reviewed', 'fixed', 'wontfix'))
);

create index if not exists agent_ratings_rating_created_idx
    on public.agent_ratings using btree (rating, created_at desc);

do $$
begin
    if not exists (
        select 1 from pg_trigger
        where tgname = 'handle_agent_ratings_updated_at'
        and tgrelid = 'public.agent_ratings'::regclass
    ) then
        create trigger handle_agent_ratings_updated_at
            before update on public.agent_ratings
            for each row
            execute function public.set_updated_at();
    end if;
end $$;

alter table public.agent_ratings enable row level security;

drop policy if exists "Users can view their own ratings" on public.agent_ratings;
create policy "Users can view their own ratings"
    on public.agent_ratings for select
    using (auth.uid() = user_id);

drop policy if exists "service_role_all_agent_ratings" on public.agent_ratings;
create policy "service_role_all_agent_ratings"
    on public.agent_ratings for all to service_role
    using (true) with check (true);
