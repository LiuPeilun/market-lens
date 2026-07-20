# Tool approvals

Market Lens pauses LLM orchestration before a tool classified as `write` or
`external_side_effect` is executed. The API persists a pending approval and returns an
`approval_required` SSE event. The user can approve or deny that single invocation through
`POST /api/tool-approvals/{id}/stream`; the response resumes the original stream.

An approval is bound to the authenticated user, chat session, tool name, exact canonical argument
digest, LLM checkpoint, and expiration time. The server signs these fields with HMAC-SHA256. The
database trigger makes signed fields immutable and only permits these state transitions:

```text
pending -> approved -> executed
pending -> approved -> failed
pending -> denied
pending -> expired
```

Set a stable secret in every API process so approvals survive restarts and work across workers:

```dotenv
MARKET_LENS_TOOL_APPROVAL_SIGNING_KEY=<random secret with at least 32 bytes of entropy>
MARKET_LENS_TOOL_APPROVAL_TTL_SECONDS=600
```

Without an explicit signing key, development uses a random process-local key. Existing approvals
then become invalid after an API restart, which fails closed.

## Sandboxed Python

`code.run_python` is the first approval-gated execution tool. It accepts Python source and a timeout
of at most 30 seconds. It always uses the configured Docker or Daytona `SandboxRunner`, has no
network access, receives no host files or credentials, and has fixed CPU, memory, process, and
output limits. A missing sandbox backend keeps the tool hidden from the LLM.

## Virtual workspace

Filesystem tools never receive a host path. Each authenticated chat session has an RLS-protected
Supabase text workspace. `workspace.list_files` and `workspace.read_file` are read-only;
`workspace.write_file` requires one-time approval. Paths are relative, reject traversal and control
characters, and file content is capped at 200 KB. Delete and host filesystem tools are not exposed.
