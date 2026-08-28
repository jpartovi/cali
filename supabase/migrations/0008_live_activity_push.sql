-- Live Activity push-to-start tokens and started instances

create table if not exists public.live_activity_devices (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    push_to_start_token text not null,
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint live_activity_devices_pkey primary key (id),
    constraint live_activity_devices_user_id_key unique (user_id),
    constraint live_activity_devices_push_to_start_token_key unique (push_to_start_token),
    constraint live_activity_devices_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade
);

do $$
begin
    if not exists (
        select 1 from pg_trigger
        where tgname = 'handle_live_activity_devices_updated_at'
        and tgrelid = 'public.live_activity_devices'::regclass
    ) then
        create trigger handle_live_activity_devices_updated_at
            before update on public.live_activity_devices
            for each row
            execute function public.set_updated_at();
    end if;
end $$;

create table if not exists public.live_activity_instances (
    id uuid not null default gen_random_uuid(),
    user_id uuid not null,
    event_id text not null,
    title text not null,
    end_at timestamptz not null,
    activity_push_token text,
    status text not null default 'started',
    created_at timestamptz not null default timezone('utc'::text, now()),
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint live_activity_instances_pkey primary key (id),
    constraint live_activity_instances_status_check check (status in ('started', 'ended')),
    constraint live_activity_instances_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade
);

create unique index if not exists live_activity_instances_started_user_event
    on public.live_activity_instances (user_id, event_id)
    where status = 'started';

create index if not exists live_activity_instances_user_status
    on public.live_activity_instances (user_id, status);

do $$
begin
    if not exists (
        select 1 from pg_trigger
        where tgname = 'handle_live_activity_instances_updated_at'
        and tgrelid = 'public.live_activity_instances'::regclass
    ) then
        create trigger handle_live_activity_instances_updated_at
            before update on public.live_activity_instances
            for each row
            execute function public.set_updated_at();
    end if;
end $$;
