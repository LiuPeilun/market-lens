create table public.workspace_files (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    session_id uuid not null references public.chat_sessions(id) on delete cascade,
    path text not null check (
        length(path) between 1 and 240
        and path !~ '(^/|\\|(^|/)\.\.(/|$))'
    ),
    content text not null check (octet_length(content) <= 200000),
    size_bytes integer generated always as (octet_length(content)) stored,
    content_type text not null default 'text/plain',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (session_id, path)
);

create index workspace_files_session_path_idx
    on public.workspace_files(session_id, path);

alter table public.workspace_files enable row level security;

create policy "workspace_files_own" on public.workspace_files
    for all using (
        (select auth.uid()) = user_id
        and exists (
            select 1 from public.chat_sessions
            where chat_sessions.id = workspace_files.session_id
              and chat_sessions.user_id = (select auth.uid())
        )
    )
    with check (
        (select auth.uid()) = user_id
        and exists (
            select 1 from public.chat_sessions
            where chat_sessions.id = workspace_files.session_id
              and chat_sessions.user_id = (select auth.uid())
        )
    );

revoke all on table public.workspace_files from anon;
grant select, insert, update on table public.workspace_files to authenticated;

create trigger workspace_files_set_updated_at
before update on public.workspace_files
for each row execute function public.set_updated_at();
