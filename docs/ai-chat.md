# Built-in AI chat

An **AI panel docked on the right** of the dashboard (toggle it with **AI** in the left rail;
there is no chat page). You bring your own provider and model; the assistant can do anything
you can do by hand through the dashboard's own HTTP API, and **every change asks for your
approval first**. Its layout and controls follow marimo's "AI" panel.

This is optional: `pip install 'vivarium-workbench[chat]'`. Without the extra the rest of the
tool is unchanged, `GET /api/ai/status` reports `available: false`, and every other chat route
answers `503 chat extra not installed`. The `viva-superpowers` Claude Code skills remain the way
to drive the workbench from Claude Code; this is the path for users without it.

## Using it

The panel, top to bottom:

- **Header** — "AI" and a close ×. **Toolbar** — `+` new chat · provider status plug (red until a
  provider + model are usable) · **gear = AI Settings** · clock = previous chats.
- **Gear → AI Settings**: pick a provider, paste a key (or, for `openai-compatible`, a base URL —
  Ollama, vLLM, OpenRouter), type a model, **Save & test**. The server proves the key with one
  real 1-token request before storing it. Keys already in the server's environment
  (`ANTHROPIC_API_KEY`, …) are detected on a loopback server; Bedrock uses ambient AWS
  credentials. The settings live in the panel, not on the Account page.
- **Composer footer** — **mode** · **model** · capabilities · `@` context · attach · send:
  - **Mode** (marimo's four, mapped to what the workbench can do; default **Manual**):
    *Manual* = pure chat, the model gets **no tools**; *Ask* = read-only tools (write operations
    are hidden from the model and refused); *Agent* = read and write tools, **every change still
    pauses for your approval**; *Code Mode (beta)* is listed but disabled (there is no kernel).
  - **Model** dropdown: switch among the models you have used (per configured provider), or
    "Add or edit models…" (opens Settings).
  - **Capabilities** (sliders): a switch for the live *workspace summary* injected into every
    message (costs tokens), plus live counts of what the assistant can reach.
  - **`@`** (button, or type `@`): mention a study or composite — inserted as
    `@study/<name>` / `@composite/<name>`, which the model is told how to resolve.
  - **Attach**: text files (≤ 100 KB each, ≤ 5, ≤ 200 KB total) are inlined into your message.
- **Messages** — your message is a bordered monospace box (click it to **edit and resend** from
  that point); replies are borderless markdown with a hover **Copy**; reasoning shows as a
  *Thinking* / *View reasoning (N chars)* accordion; reads show as compact tool rows, and every
  change is an amber **Approval required** card (method, path, body; **Deny** / **Approve**).
  Errors are a red banner with a separate **Retry**. Messages sent while a turn is running are
  **queued** (dashed, spinner) and sent when it finishes. A **Stop** strip appears while streaming.
- **Previous chats** — searchable, grouped by date (Today / Yesterday / Previous 7 days / Older);
  stored in this tab's `sessionStorage` only (they contain workspace data, so nothing persists
  beyond the tab).
- **Connect your own agent** — the new-thread callout explains the Claude Code alternative.

Deliberately not offered: web search (the workbench has no such tool), image attachments, and an
auto-approve ("Agent runs changes on its own") mode — every non-GET needs your approval.

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
- **Manifest.** Unless switched off in Capabilities, each turn injects a live
  `GET /api/workspace-manifest` snapshot as instructions (the orientation call
  `ai-onboarding.md` §3 prescribes). The request also carries the chosen `mode`
  (`manual` | `ask` | `agent`) and `include_manifest`; reasoning streams as `reasoning-delta` frames.

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
call was approved (`ctx.tool_call_approved`). Approval is **single-use** and the
audit is **intent-first**: before an approved mutation is dispatched, an `intent`
line is written to `<workspace>/.pbg/ai-actions.jsonl` (fsync'd, like
`lib/event_log`; `events.jsonl` can't be used — its schema admits four event
types) and a `result` line follows (`status`, `outcome: completed | interrupted`).
Consequences:

- replaying the same approval (same `tool_call_id`, same call, same issuing model
  response) is refused with "already executed" — a duplicated tab or a stale approval
  card can't re-run it; a provider that *reuses* ids across turns is fine because the
  issuing response's timestamp is part of the claim;
- a mutation whose caller is cancelled (Stop, tab close) is still on record
  (`outcome: interrupted`) — the sync route handler finishes in its thread;
- if the log can't be written the change is **refused**, not run unaudited; if
  only the result line fails the tool result carries an `audit_warning`;
- each line has `ts, session, provider, model, tool_call_id, digest, operation_id,
  method, path, approved`. `session` is a 12-hex-char **hash** of the session key:
  the raw key scopes hosted credentials and this file is served by the workspace
  catch-all (and swept up by `git add -A` commits), so it must never be written.

If a provider call fails *after* an approved change ran, the stream emits an
`error` frame followed by a `done` frame with `incomplete: true` carrying the
transcript so far, so the browser's history has no dangling tool call. The browser
persists a compact record of the turn in flight (`{prompt}` or `{deferred_results}`; the
messages come from the stored transcript, so nothing is stored twice), set by the same
state transition that starts the turn — so a reload at any point recovers. **Retry**
(also offered after a Stop mid-resume or a reload) re-sends it: for a resume that is safe
precisely because approvals are single-use. A retry of a change that already ran is
refused *with the recorded outcome* (`status N`, or "no result recorded / interrupted —
check with a read"), so the model learns what happened.

If a transcript still ends on an unresolved tool call (lost record, hostile or stale
state), a new prompt no longer wedges the chat: the server closes each dangling call
with a synthetic "outcome unknown — check with a read" result. The action is **not**
executed.

`ai-actions.jsonl` is workspace state like any other file: if a workspace commits it,
a branch switch reverts it (and with it the single-use claims). Add it to the
workspace's `.gitignore` if that matters to you.

> **Approval is a verb test, not a side-effect test.** Reads (`GET`) run without
> approval; a `GET` handler that writes (e.g. `GET /api/audit-report?rerun=1`)
> therefore runs unapproved. The exclusion list below removes the worst
> offenders; the rest is inherent to trusting HTTP semantics.

### Exclusion list (`lib/ai_tools.py`)

Applied once, when the index is built; listing and calling both resolve ids only
through that index, so a forged `operation_id` in a tampered transcript cannot
reach an excluded route:

- tags `Auth` (GitHub identity) and `AI` (its own credentials); tag `Downloads`
  for **reads** only (binary/HTML bodies — Downloads-tagged POSTs such as
  `figures-build` stay available, with approval);
- path prefixes `/api/source/` (switch, remote build, materialize), `/api/workspaces`
  (the switcher catalog, add/forget/cleanup, **start/stop** of other servers),
  `/api/chat/`, `/api/ai/`, `/api/events` (SSE never terminates);
- remote writes / remote identity: `/api/branch/push`, `/api/work-push`, `/api/work-create-pr`;
- software installs on the host (arbitrary code execution):
  `/api/catalog-install`, `/api/catalog-uninstall`, `/api/import-install`, `/api/system-deps-install`;
- binary reads tagged elsewhere: `/api/simulation-run-download`, `/api/study-analysis-zip`,
  `/api/composite-run/{run_id}/download`.

Deliberately still available (approval-gated): local git commits
(`/api/dirty-commit-all` — its card shows an empty body, so read the audit log to
see what was swept) and run launches. Large responses are truncated (20k chars);
non-JSON responses are summarised.

## Credentials (`lib/ai_auth.py`)

Where a key lives depends on how the server is bound:

| server | storage | notes |
|---|---|---|
| loopback bind (`127.0.0.1`, `localhost`, `::1`), no proxy flags, no base path | OS keyring, service `vivarium-workbench-llm` (process memory if no usable backend) | provider/model choice in `~/.config/vivarium-workbench/ai.yaml` (never `workspace.yaml`, which is git-tracked scientific record). The request's `Host` must itself be loopback — a DNS-rebound page (Host == Origin == the attacker's name) gets 403 on `/api/ai/*` and `/api/chat/*` |
| anything else — hosted pod, `0.0.0.0`, **any** `--trust-proxy` / `--allowed-origin` / `--base-path`, or an unknown bind | **process memory only**, per `X-VW-Session`, never disk or keyring | selection is per-session memory too; `openai-compatible` `base_url` must be public `https` (SSRF guard, incl. NAT64/IPv4-mapped/scoped addresses); **the server's own env keys (`ANTHROPIC_API_KEY`, …) and AWS role are NOT lent to sessions** unless the operator sets `VIVARIUM_WORKBENCH_CHAT_ALLOW_SERVER_CREDENTIALS=1` |

On a loopback server the ambient environment keys and AWS credentials are picked
up as a convenience ("from the server environment"). Re-saving an
`openai-compatible` endpoint without retyping the key keeps the saved key.

Keys are never returned by any route; `mask_key` scrubs key-shaped strings and the
exact stored value from every error string that could carry one.

## Threat model (what is and isn't defended)

- **Prompt injection via workspace content or tool results**: results are data
  (the system prompt says so) and, more importantly, nothing mutating runs
  without a human approving the exact method/path/body shown.
- **Tampered client transcript**: the user could already call the API directly,
  so approving a tampered call grants nothing new; the exclusion list is still
  enforced server-side.
- **Session keys are routing ids, not auth.** Anyone holding a hosted session's
  key can use the credentials saved under it, so the key is kept out of the
  publicly served audit file (hashed) and must be treated as a secret by operators.
- **Hosted servers are anonymous**: without the operator opt-in above, visitors
  can only use keys they bring themselves; with it, every visitor can spend the
  server's credentials (and choose the model) — enable only behind your own auth.
- **Residual**: the hosted `base_url` check resolves DNS once (a rebinding race is
  theoretically possible; redirects are not followed by the SDK); approved
  operations that are themselves powerful (starting runs — read-only servers keep
  those) remain reachable with approval; there is no cumulative per-session token
  cap (each turn is limited to 30 model requests / 40 tool calls, prompt ≤ 20k chars).

## Files

`lib/ai_auth.py`, `lib/ai_views.py`, `lib/ai_tools.py`, `lib/ai_chat.py`,
`static/chat-core.js` (DOM-free logic, unit-tested under node), `static/chat.js` (the panel),
`static/chat.css`, `static/ai-login.js` (the settings sheet). Tests: `tests/test_ai_auth.py`,
`test_ai_tools.py`, `test_ai_chat.py` (includes a contract test feeding real
server frames through the real client reducer), `tests/js/test_chat_core.js`.
The AI panel and its rail toggle are hidden in the published read-only snapshot.
