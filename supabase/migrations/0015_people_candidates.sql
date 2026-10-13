-- People a request might be talking about: contacts and people the user has had events
-- with, whose names appear in the request text. The agent's people pre-pass
-- (services/people_finder.py) asks Jev which of these the request actually means.

create extension if not exists pg_trgm;

-- Every name a contact goes by, in one indexed column, so each word of a request is one
-- index lookup instead of a scan over the whole address book
alter table public.contacts
    add column if not exists search_names text
    generated always as (
        lower(
            coalesce(given_name, '') || ' ' || coalesce(family_name, '') || ' '
            || coalesce(nickname, '') || ' ' || coalesce(display_name, '')
        )
    ) stored;

create index if not exists contacts_search_names_trgm_idx
    on public.contacts using gin (search_names gin_trgm_ops);

create index if not exists contacts_user_invite_email_idx
    on public.contacts using btree (user_id, invite_email)
    where invite_email is not null;

-- Who was on each of the user's events. Snapshots only cover a short window and get
-- pruned; this keeps the history, so "Henry" can mean the Henry the user actually meets.
-- Filled by a trigger on calendar_event_snapshots and by scripts/backfill_event_people.py.
create table if not exists public.calendar_event_people (
    user_id uuid not null,
    google_calendar_id text not null,
    google_event_id text not null,
    email text not null,
    display_name text,
    start_at timestamptz,
    -- The user organized the event, i.e. invited this person
    organizer_self boolean not null default false,
    updated_at timestamptz not null default timezone('utc'::text, now()),
    constraint calendar_event_people_pkey primary key (user_id, google_calendar_id, google_event_id, email),
    constraint calendar_event_people_user_id_fkey foreign key (user_id) references public.users (id) on delete cascade,
    constraint calendar_event_people_email_lower_check check (email = lower(email))
);

create index if not exists calendar_event_people_user_email_idx
    on public.calendar_event_people using btree (user_id, email);

alter table public.calendar_event_people enable row level security;

drop policy if exists "Users can view their own event people" on public.calendar_event_people;
create policy "Users can view their own event people"
    on public.calendar_event_people for select
    using (auth.uid() = user_id);

drop policy if exists "service_role_all_calendar_event_people" on public.calendar_event_people;
create policy "service_role_all_calendar_event_people"
    on public.calendar_event_people for all to service_role
    using (true) with check (true);

-- Attendees worth remembering: real people, not the user, rooms or group calendars
create or replace function public.event_people_from_attendees(p_attendees jsonb)
returns table (email text, display_name text)
language sql
immutable
set search_path = public
as $$
    select distinct on (lower(a->>'email'))
        lower(a->>'email'),
        nullif(btrim(a->>'displayName'), '')
    from jsonb_array_elements(coalesce(p_attendees, '[]'::jsonb)) a
    where coalesce(a->>'email', '') <> ''
        and coalesce((a->>'self')::boolean, false) = false
        and coalesce((a->>'resource')::boolean, false) = false
        and lower(a->>'email') not like '%@resource.calendar.google.com'
        and lower(a->>'email') not like '%@group.calendar.google.com'
        and lower(a->>'email') not like '%@group.v.calendar.google.com'
    order by lower(a->>'email'), nullif(btrim(a->>'displayName'), '') nulls last;
$$;

create or replace function public.sync_calendar_event_people()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if new.status = 'cancelled' then
        delete from public.calendar_event_people p
        where p.user_id = new.user_id
            and p.google_calendar_id = new.google_calendar_id
            and p.google_event_id = new.google_event_id;
        return new;
    end if;

    -- People taken off the event
    delete from public.calendar_event_people p
    where p.user_id = new.user_id
        and p.google_calendar_id = new.google_calendar_id
        and p.google_event_id = new.google_event_id
        and p.email not in (select e.email from public.event_people_from_attendees(new.attendees) e);

    insert into public.calendar_event_people
        (user_id, google_calendar_id, google_event_id, email, display_name, start_at, organizer_self)
    select new.user_id, new.google_calendar_id, new.google_event_id, e.email, e.display_name, new.start_at, new.organizer_self
    from public.event_people_from_attendees(new.attendees) e
    on conflict (user_id, google_calendar_id, google_event_id, email) do update
        set display_name = coalesce(excluded.display_name, calendar_event_people.display_name),
            start_at = excluded.start_at,
            organizer_self = excluded.organizer_self,
            updated_at = timezone('utc'::text, now());
    return new;
end;
$$;

drop trigger if exists sync_calendar_event_people on public.calendar_event_snapshots;
create trigger sync_calendar_event_people
    after insert or update of attendees, status, start_at, organizer_self
    on public.calendar_event_snapshots
    for each row
    execute function public.sync_calendar_event_people();

-- Seed from the snapshots already stored (directly, so no snapshot rows change)
insert into public.calendar_event_people
    (user_id, google_calendar_id, google_event_id, email, display_name, start_at, organizer_self)
select s.user_id, s.google_calendar_id, s.google_event_id, e.email, e.display_name, s.start_at, s.organizer_self
from public.calendar_event_snapshots s
cross join lateral public.event_people_from_attendees(s.attendees) e
where s.status <> 'cancelled'
on conflict do nothing;

drop function if exists public.people_candidates(uuid, text, real, integer);

create or replace function public.people_candidates(
    p_user_id uuid,
    p_text text,
    p_min_score real default 0.5,
    p_limit integer default 15
)
returns table (
    contact_id uuid,
    email text,
    display_name text,
    nickname text,
    in_contacts boolean,
    events_together integer,
    invited_count integer,
    last_together_at timestamptz,
    next_together_at timestamptz,
    -- Recency-weighted events together (each event counts less the further it is from today)
    affinity real,
    score real
)
language plpgsql
stable
security invoker
set search_path = public
as $$
begin
    -- The <<% operator below matches at this strict word similarity or higher
    perform set_config('pg_trgm.strict_word_similarity_threshold', p_min_score::text, true);

    return query
    with words as (
        select distinct w
        from regexp_split_to_table(lower(coalesce(p_text, '')), '[^[:alnum:]]+') as w
        where length(w) >= 2
    ),
    -- Everyone the user has had an event with in the last year (or has one coming up), one row per email
    history as (
        select
            ep.email,
            max(ep.display_name) as name,
            count(*)::integer as events_together,
            count(*) filter (where ep.organizer_self)::integer as invited_count,
            max(ep.start_at) filter (where ep.start_at <= now()) as last_together_at,
            min(ep.start_at) filter (where ep.start_at > now()) as next_together_at,
            sum(exp(-abs(extract(epoch from (now() - ep.start_at))) / 86400.0 / 30.0))::real as affinity,
            -- No display name: "henry.smith@x.com" still reads as "henry smith"
            lower(coalesce(max(ep.display_name), '') || ' ' || regexp_replace(split_part(ep.email, '@', 1), '[^[:alnum:]]+', ' ', 'g')) as names
        from public.calendar_event_people ep
        where ep.user_id = p_user_id
            and ep.start_at > now() - interval '365 days'
        group by ep.email
    ),
    contact_hits as (
        select c.id, max(strict_word_similarity(words.w, c.search_names)) as score
        from words
        join public.contacts c
            on c.user_id = p_user_id
            and words.w <<% c.search_names
        group by c.id
    ),
    history_hits as (
        select h.email, max(strict_word_similarity(words.w, h.names)) as score
        from words
        join history h on words.w <<% h.names
        where not exists (
            select 1 from public.contacts c
            where c.user_id = p_user_id and c.invite_email = h.email
        )
        group by h.email
    ),
    people as (
        select
            c.id as contact_id,
            c.invite_email as email,
            c.display_name,
            c.nickname,
            true as in_contacts,
            coalesce(h.events_together, 0) as events_together,
            coalesce(h.invited_count, 0) as invited_count,
            h.last_together_at,
            h.next_together_at,
            coalesce(h.affinity, 0)::real as affinity,
            ch.score
        from contact_hits ch
        join public.contacts c on c.id = ch.id
        left join history h on h.email = c.invite_email
        union all
        select
            null::uuid,
            h.email,
            coalesce(h.name, h.email),
            null::text,
            false,
            h.events_together,
            h.invited_count,
            h.last_together_at,
            h.next_together_at,
            h.affinity,
            hh.score
        from history_hits hh
        join history h on h.email = hh.email
    )
    select p.contact_id, p.email, p.display_name, p.nickname, p.in_contacts, p.events_together,
        p.invited_count, p.last_together_at, p.next_together_at, p.affinity, p.score
    from people p
    order by p.score desc, p.affinity desc, p.display_name
    limit p_limit;
end;
$$;

revoke all on function public.people_candidates(uuid, text, real, integer) from public, anon;
grant execute on function public.people_candidates(uuid, text, real, integer) to authenticated, service_role;
