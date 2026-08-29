-- Remember events whose Live Activity the user dismissed (photo taken or X)

create table if not exists public.live_activity_dismissals (
    user_id uuid not null,
    event_id text not null,
    created_at timestamptz not null default timezone('utc'::text, now()),
    constraint live_activity_dismissals_pkey primary key (user_id, event_id),
    constraint live_activity_dismissals_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade
);
