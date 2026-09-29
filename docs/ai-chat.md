# Built-in AI chat

An in-dashboard **Chat** tab plus an **AI provider** card on the Account page.
You bring your own provider and model; the assistant can do anything you can do
by hand through the dashboard's own HTTP API, and **every change asks for your
approval first**.

This is optional: `pip install 'vivarium-workbench[chat]'`. Without the extra the
rest of the tool is unchanged, `GET /api/ai/status` reports `available: false`,
and every other chat route answers `503 chat extra not installed`. The
`viva-superpowers` Claude Code skills remain the way to drive the workbench from
Claude Code; this is the path for users without it.

## Using it

1. **Account → AI provider**: pick a provider, paste a key (or, for
   `openai-compatible`, a base URL — Ollama, vLLM, OpenRouter), type a model name,
   **Save & test**. The server proves the key with one real 1-token request
   before storing it. Keys already in the server's environment
   (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`) are detected and shown
   as "from the server environment"; Bedrock uses the server's ambient AWS
   credentials.
2. **Chat**: ask. Reads (`GET`) run immediately and show as compact tool rows.
   Anything else pauses on an **Approval required** card showing the method, path
   and request body; **Approve** runs it, **Deny** tells the model you declined.

## How it works

```
browser (chat.js, chat-core.js)          server
  transcript in sessionStorage   ──POST /api/chat/turn──▶  lib/ai_chat.py  (one pydantic-ai agent turn)
  ◀── NDJSON frames ────────────────────────────────────    │ tools: lib/ai_tools.py
                                                            ▼
                                        in-process httpx.ASGITransport(app) ──▶ the app's own routes
```

- **Stateless server.** The browser sends the transcript
  (`ModelMessagesTypeAdapter` JSON) with each turn; the server stores no
  conversation. A turn is either a new `prompt` or `deferred_results`
  (`{"approvals": {tool_call_id: true | {"denied": reason}}}`) resuming a paused
  one.
- **Frames** (`application/x-ndjson`): `text-delta`, `tool-call`, `tool-result`,
  `approval-required` (turn ends; resume with `deferred_results`), `done`
  (`{messages, pending_approval}`), `error` (masked). The response sets
  `Content-Encoding: identity` so `GZipMiddleware` doesn't buffer the stream.
- **Preflight errors** (no extra 503, no provider/credentials 409, bad request
  422) are ordinary JSON envelopes sent before streaming starts.
- **Manifest.** Each turn injects a live `GET /api/workspace-manifest` snapshot as
  instructions (the orientation call `ai-onboarding.md` §3 prescribes).

### Action surface = the app's own live OpenAPI

No hand-kept catalog. Three tools are built at runtime from `app.openapi()`:

| tool | purpose |
|---|---|
| `list_operations(tag?, query?)` | operation id, method, path, summary, tag (capped) |
| `describe_operation(operation_id)` | resolved parameter + request-body JSON schema |
| `call_operation(operation_id, path_params, query, body)` | run one, in-process, forwarding the tab's `X-VW-Session` so it targets the user's workspace |

Because calls go through the real app, read-only mode
(`VIVARIUM_WORKBENCH_READONLY`) removes mutating operations from the surface with
no extra code, and the error envelope / validation behave exactly as for the UI.
The in-process client sends no `Origin`, so the CSRF guard's existing rule
(absent `Origin` ⇒ allowed) applies; `/api/chat/turn` itself is guarded like any
other `POST`.

**Approval.** Every non-GET raises pydantic-ai's `ApprovalRequired` unless the
call was approved (`ctx.tool_call_approved`). Approved mutations append one line
to `<workspace>/.pbg/ai-actions.jsonl` — `ts, session, provider, model,
operation_id, method, path, status, approved` — fsync'd like `lib/event_log`
(`events.jsonl` can't be used: its schema admits four event types).

### Exclusion list (`lib/ai_tools.py`)

Applied once, when the index is built; listing and calling both resolve ids only
through that index, so a forged `operation_id` in a tampered transcript cannot
reach an excluded route:

- tags `Auth` (GitHub identity), `AI` (its own credentials), `Downloads` (binary/HTML bodies);
- path prefixes `/api/source/` (switch, remote build, materialize), `/api/workspaces/`
  (add/forget/cleanup, **start/stop** of other servers), `/api/chat*`, `/api/ai*`, `/api/events*` (SSE never terminates);
- `/api/branch/push`, `/api/work-push` (writes to remotes).

Large responses are truncated (20k chars) and non-JSON responses are summarised.

## Credentials (`lib/ai_auth.py`)

Where a key lives depends on how the server is bound:

| bind | storage | notes |
|---|---|---|
| loopback (`127.0.0.1`, `localhost`, `::1`) | OS keyring, service `vivarium-workbench-llm` (process memory if no usable backend) | provider/model choice in `~/.config/vivarium-workbench/ai.yaml` (never `workspace.yaml`, which is git-tracked scientific record) |
| anything else (hosted pod, `0.0.0.0`) | **process memory only**, per `X-VW-Session`, never disk or keyring | selection is per-session memory too; `openai-compatible` `base_url` must be public `https` (SSRF guard) |

Keys are never returned by any route; `mask_key` scrubs key-shaped strings and the
exact stored value from every error string that could carry one.

## Threat model (what is and isn't defended)

- **Prompt injection via workspace content or tool results**: results are data
  (the system prompt says so) and, more importantly, nothing mutating runs
  without a human approving the exact method/path/body shown.
- **Tampered client transcript**: the user could already call the API directly,
  so approving a tampered call grants nothing new; the exclusion list is still
  enforced server-side.
- **Residual**: the hosted `base_url` check resolves DNS once (a rebinding race
  is theoretically possible); approved operations that are themselves powerful
  (e.g. starting runs, which read-only servers keep) remain reachable with
  approval, consistent with the existing surface.

## Files

`lib/ai_auth.py`, `lib/ai_views.py`, `lib/ai_tools.py`, `lib/ai_chat.py`,
`static/chat-core.js` (DOM-free logic, unit-tested under node), `static/chat.js`,
`static/chat.css`, `static/ai-login.js`. Tests: `tests/test_ai_auth.py`,
`test_ai_tools.py`, `test_ai_chat.py` (includes a contract test feeding real
server frames through the real client reducer), `tests/js/test_chat_core.js`.
The chat UI and the AI card are hidden in the published read-only snapshot.
