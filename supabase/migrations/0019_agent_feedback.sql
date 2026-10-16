-- Issues and ideas flagged from the app: bad agent results (tied to their LangSmith trace by
-- request_id) and general UI notes (request_id null)

create table if not exists public.agent_feedback (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    request_id uuid,
    query text,
    response jsonb,
    note text,
    app_version text,
    app_build text,
    device text,
    os_version text,
    status text not null default 'open',
    created_at timestamptz not null default timezone('utc'::text, now()),
    constraint agent_feedback_pkey primary key (id),
    constraint agent_feedback_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade,
    constraint agent_feedback_status_check check (status in ('open', 'reviewed', 'fixed', 'wontfix'))
);

create index if not exists agent_feedback_user_created_idx
    on public.agent_feedback using btree (user_id, created_at desc);

create index if not exists agent_feedback_status_idx
    on public.agent_feedback using btree (status);

alter table public.agent_feedback enable row level security;

drop policy if exists "Users can view their own feedback" on public.agent_feedback;
create policy "Users can view their own feedback"
    on public.agent_feedback for select
    using (auth.uid() = user_id);

drop policy if exists "Users can insert their own feedback" on public.agent_feedback;
create policy "Users can insert their own feedback"
    on public.agent_feedback for insert
    with check (auth.uid() = user_id);

drop policy if exists "service_role_all_agent_feedback" on public.agent_feedback;
create policy "service_role_all_agent_feedback"
    on public.agent_feedback for all to service_role
    using (true) with check (true);
