create extension if not exists pgcrypto;

create table public.profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    display_name text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table public.analysis_runs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    asset_type text not null check (asset_type in ('stock', 'fund')),
    asset_code text not null,
    asset_name text,
    request_params jsonb not null default '{}'::jsonb,
    result jsonb not null,
    created_at timestamptz not null default now()
);

create table public.chat_sessions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    title text not null,
    asset_type text check (asset_type in ('stock', 'fund')),
    asset_code text,
    asset_name text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table public.chat_messages (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    session_id uuid not null references public.chat_sessions(id) on delete cascade,
    role text not null check (role in ('user', 'assistant')),
    content text not null,
    citations jsonb not null default '[]'::jsonb,
    analysis_run_id uuid references public.analysis_runs(id) on delete set null,
    created_at timestamptz not null default now()
);

create table public.watchlists (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    name text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table public.watchlist_items (
    id uuid primary key default gen_random_uuid(),
    watchlist_id uuid not null references public.watchlists(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    asset_type text not null check (asset_type in ('stock', 'fund')),
    asset_code text not null,
    asset_name text,
    created_at timestamptz not null default now(),
    unique (watchlist_id, asset_type, asset_code)
);

create index analysis_runs_user_created_idx
    on public.analysis_runs(user_id, created_at desc);
create index chat_sessions_user_updated_idx
    on public.chat_sessions(user_id, updated_at desc);
create index chat_messages_session_created_idx
    on public.chat_messages(session_id, created_at asc);
create index watchlists_user_created_idx
    on public.watchlists(user_id, created_at desc);
create index watchlist_items_watchlist_created_idx
    on public.watchlist_items(watchlist_id, created_at desc);

alter table public.profiles enable row level security;
alter table public.analysis_runs enable row level security;
alter table public.chat_sessions enable row level security;
alter table public.chat_messages enable row level security;
alter table public.watchlists enable row level security;
alter table public.watchlist_items enable row level security;

create policy "profiles_select_own" on public.profiles
    for select using ((select auth.uid()) = id);
create policy "profiles_update_own" on public.profiles
    for update using ((select auth.uid()) = id) with check ((select auth.uid()) = id);

create policy "analysis_runs_own" on public.analysis_runs
    for all using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);
create policy "chat_sessions_own" on public.chat_sessions
    for all using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);
create policy "chat_messages_own" on public.chat_messages
    for all using (
        (select auth.uid()) = user_id
        and exists (
            select 1 from public.chat_sessions
            where chat_sessions.id = chat_messages.session_id
              and chat_sessions.user_id = (select auth.uid())
        )
    )
    with check (
        (select auth.uid()) = user_id
        and exists (
            select 1 from public.chat_sessions
            where chat_sessions.id = chat_messages.session_id
              and chat_sessions.user_id = (select auth.uid())
        )
    );
create policy "watchlists_own" on public.watchlists
    for all using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);
create policy "watchlist_items_own" on public.watchlist_items
    for all using (
        (select auth.uid()) = user_id
        and exists (
            select 1 from public.watchlists
            where watchlists.id = watchlist_items.watchlist_id
              and watchlists.user_id = (select auth.uid())
        )
    )
    with check (
        (select auth.uid()) = user_id
        and exists (
            select 1 from public.watchlists
            where watchlists.id = watchlist_items.watchlist_id
              and watchlists.user_id = (select auth.uid())
        )
    );

revoke all on table public.profiles from anon;
revoke all on table public.analysis_runs from anon;
revoke all on table public.chat_sessions from anon;
revoke all on table public.chat_messages from anon;
revoke all on table public.watchlists from anon;
revoke all on table public.watchlist_items from anon;

grant select, update on table public.profiles to authenticated;
grant select, insert, update, delete on table public.analysis_runs to authenticated;
grant select, insert, update, delete on table public.chat_sessions to authenticated;
grant select, insert, update, delete on table public.chat_messages to authenticated;
grant select, insert, update, delete on table public.watchlists to authenticated;
grant select, insert, update, delete on table public.watchlist_items to authenticated;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger profiles_set_updated_at
before update on public.profiles
for each row execute function public.set_updated_at();

create trigger chat_sessions_set_updated_at
before update on public.chat_sessions
for each row execute function public.set_updated_at();

create trigger watchlists_set_updated_at
before update on public.watchlists
for each row execute function public.set_updated_at();

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
    insert into public.profiles (id, display_name)
    values (new.id, coalesce(new.raw_user_meta_data ->> 'display_name', split_part(new.email, '@', 1)));
    return new;
end;
$$;

create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_user();
