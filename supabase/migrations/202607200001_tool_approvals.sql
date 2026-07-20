create table public.tool_approvals (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    session_id uuid not null references public.chat_sessions(id) on delete cascade,
    tool_name text not null,
    tool_alias text not null,
    tool_call_id text not null,
    risk_level text not null check (
        risk_level in ('read', 'compute', 'write', 'external_side_effect', 'destructive')
    ),
    execution_target text not null check (
        execution_target in ('trusted_local', 'sandbox_required', 'remote_mcp')
    ),
    status text not null default 'pending' check (
        status in ('pending', 'approved', 'denied', 'executed', 'failed', 'expired')
    ),
    reason text not null,
    input_summary jsonb not null default '{}'::jsonb,
    arguments_digest text not null check (arguments_digest ~ '^[0-9a-f]{64}$'),
    checkpoint jsonb not null,
    citations jsonb not null default '[]'::jsonb,
    signature text not null check (signature ~ '^[0-9a-f]{64}$'),
    expires_at timestamptz not null,
    resolved_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index tool_approvals_user_created_idx
    on public.tool_approvals(user_id, created_at desc);
create index tool_approvals_pending_session_idx
    on public.tool_approvals(session_id, expires_at)
    where status = 'pending';

alter table public.tool_approvals enable row level security;

create policy "tool_approvals_select_own" on public.tool_approvals
    for select using (
        (select auth.uid()) = user_id
        and exists (
            select 1 from public.chat_sessions
            where chat_sessions.id = tool_approvals.session_id
              and chat_sessions.user_id = (select auth.uid())
        )
    );

create policy "tool_approvals_insert_own" on public.tool_approvals
    for insert with check (
        (select auth.uid()) = user_id
        and exists (
            select 1 from public.chat_sessions
            where chat_sessions.id = tool_approvals.session_id
              and chat_sessions.user_id = (select auth.uid())
        )
    );

create policy "tool_approvals_update_own" on public.tool_approvals
    for update using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

revoke all on table public.tool_approvals from anon;
grant select, insert, update on table public.tool_approvals to authenticated;

create or replace function public.guard_tool_approval_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.user_id is distinct from old.user_id
       or new.session_id is distinct from old.session_id
       or new.tool_name is distinct from old.tool_name
       or new.tool_alias is distinct from old.tool_alias
       or new.tool_call_id is distinct from old.tool_call_id
       or new.risk_level is distinct from old.risk_level
       or new.execution_target is distinct from old.execution_target
       or new.reason is distinct from old.reason
       or new.input_summary is distinct from old.input_summary
       or new.arguments_digest is distinct from old.arguments_digest
       or new.checkpoint is distinct from old.checkpoint
       or new.citations is distinct from old.citations
       or new.signature is distinct from old.signature
       or new.expires_at is distinct from old.expires_at then
        raise exception 'tool approval immutable fields cannot be changed';
    end if;

    if not (
        (old.status = 'pending' and new.status in ('approved', 'denied', 'expired'))
        or (old.status = 'approved' and new.status in ('executed', 'failed'))
    ) then
        raise exception 'invalid tool approval status transition';
    end if;
    return new;
end;
$$;

create trigger tool_approvals_guard_update
before update on public.tool_approvals
for each row execute function public.guard_tool_approval_update();

create trigger tool_approvals_set_updated_at
before update on public.tool_approvals
for each row execute function public.set_updated_at();
