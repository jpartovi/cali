-- Sound-alike name matches must also be spelled a little alike (trigram similarity 0.2+), so common
-- words that happen to sound like a name ("right" / "Redda") stop turning up as candidates.

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
        select hit.id, max(hit.score) as score
        from (
            select c.id, strict_word_similarity(words.w, c.search_names) as score
            from words
            join public.contacts c
                on c.user_id = p_user_id
                and words.w <<% c.search_names
            union all
            -- Sound-alike spellings ("Maton" for Matan), mostly from speech-to-text. Ranked below
            -- spelling matches; Jev decides whether the request means them.
            select c.id, (p_min_score * 0.9)::real
            from words
            join public.contacts c on c.user_id = p_user_id
            cross join lateral regexp_split_to_table(lower(c.search_names), '[^[:alnum:]]+') as name_word
            where length(words.w) >= 4
                and length(name_word) >= 4
                and extensions.dmetaphone(words.w) = extensions.dmetaphone(name_word)
                -- and spelled at least a little alike ("right" sounds like "Redda" but shares almost no letters)
                and similarity(words.w, name_word) >= 0.2
        ) hit
        group by hit.id
    ),
    history_hits as (
        select hit.email, max(hit.score) as score
        from (
            select h.email, strict_word_similarity(words.w, h.names) as score
            from words
            join history h on words.w <<% h.names
            union all
            -- Sound-alike spellings, as for contacts
            select h.email, (p_min_score * 0.9)::real
            from words
            join history h on true
            cross join lateral regexp_split_to_table(h.names, '[^[:alnum:]]+') as name_word
            where length(words.w) >= 4
                and length(name_word) >= 4
                and extensions.dmetaphone(words.w) = extensions.dmetaphone(name_word)
                -- and spelled at least a little alike ("right" sounds like "Redda" but shares almost no letters)
                and similarity(words.w, name_word) >= 0.2
        ) hit
        -- Attendees who are a contact (under any of the contact's emails) come in through contact_hits
        where not exists (
            select 1 from public.contacts c
            where c.user_id = p_user_id and c.emails @> array[hit.email]
        )
        group by hit.email
    ),
    people as (
        select
            c.id as contact_id,
            coalesce(c.invite_email, c.emails[1]) as email,
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
        -- History under every email on the contact card (e.g. a school and a personal address)
        left join lateral (
            select
                sum(hx.events_together)::integer as events_together,
                sum(hx.invited_count)::integer as invited_count,
                max(hx.last_together_at) as last_together_at,
                min(hx.next_together_at) as next_together_at,
                sum(hx.affinity)::real as affinity
            from history hx
            where hx.email = any(c.emails)
        ) h on true
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
