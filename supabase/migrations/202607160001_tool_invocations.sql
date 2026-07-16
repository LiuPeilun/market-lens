create table public.tool_invocations (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    session_id uuid references public.chat_sessions(id) on delete set null,
    tool_name text not null,
    capability text not null,
    risk_level text not null check (
        risk_level in ('read', 'compute', 'write', 'external_side_effect', 'destructive')
    ),
    execution_target text not null check (
        execution_target in ('trusted_local', 'sandbox_required', 'remote_mcp')
    ),
    policy_decision text not null check (
        policy_decision in ('allow', 'confirmation_required', 'deny')
    ),
    status text not null check (
        status in ('success', 'denied', 'confirmation_required', 'error')
    ),
    duration_ms integer not null check (duration_ms >= 0),
    input_summary jsonb not null default '{}'::jsonb,
    error_code text,
    created_at timestamptz not null default now()
);

create index tool_invocations_user_created_idx
    on public.tool_invocations(user_id, created_at desc);
create index tool_invocations_session_created_idx
    on public.tool_invocations(session_id, created_at asc)
    where session_id is not null;

alter table public.tool_invocations enable row level security;

create policy "tool_invocations_select_own" on public.tool_invocations
    for select using ((select auth.uid()) = user_id);

create policy "tool_invocations_insert_own" on public.tool_invocations
    for insert with check (
        (select auth.uid()) = user_id
        and (
            session_id is null
            or exists (
                select 1 from public.chat_sessions
                where chat_sessions.id = tool_invocations.session_id
                  and chat_sessions.user_id = (select auth.uid())
            )
        )
    );

revoke all on table public.tool_invocations from anon;
grant select, insert on table public.tool_invocations to authenticated;
