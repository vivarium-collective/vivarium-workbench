# Integrated AI chat + provider login for vivarium-workbench

## Context
Today every AI capability lives outside the workbench, in viva-superpowers Claude Code skills that `curl` the HTTP API (`docs/ai-onboarding.md` §4.1, "the Workbench is AI-free"). Users without Claude Code can't use AI with the dashboard at all. Goal: an in-dashboard **Chat** tab plus an **AI provider** login section, where users bring their own provider/model and the model can do anything a user can do by hand, with every change approved by the user. Alex has approved revising the "AI-free" principle.

Decisions made (AskUserQuestion, 2026-09-29):
- **pydantic-ai-slim** for providers, tool loop and approvals
- keys stored **by mode**: OS keyring on a local bind, memory-only per session otherwise
- **every non-GET action needs approval**
- shipped as an **optional `[chat]` extra**

## Design

### 1. Action surface = the app's own live OpenAPI (no hand-kept catalog)
`/openapi.json` is served (`api/app.py:540`, `create_app`). Of the 136 mutating routes, about 80% take typed pydantic bodies from `lib/models.py`. Three generic tools are built from `app.openapi()` at runtime, not from a fixture:
- `list_operations(tag?, query?)` returns operationId, method, path, summary and tag. Exposing all 265 routes as separate tools would flood the context.
- `describe_operation(operation_id)` returns the resolved parameter and request-body JSON schema.
- `call_operation(operation_id, path_params, query, body)`:
  - It runs **in-process** through `httpx.AsyncClient(transport=httpx.ASGITransport(app))`.
  - It forwards the tab's `X-VW-Session` header so it targets the user's workspace (`app.py:607-669`).
  - It sends no `Origin`, so the CSRF check passes by its existing rule (`lib/csrf.py`).
  - Read-only mode already removes mutating routes (`_apply_readonly_filter`, `app.py:525`), so the tools respect it without extra code.
  - Non-GET calls raise `pydantic_ai.ApprovalRequired` unless `ctx.tool_call_approved`. This is the argument-conditional approval from pydantic-ai's deferred-tools docs.
- **Excluded operations**: tags `Auth` and `Static & shell`, `include_in_schema=False` routes, `/api/chat/*`, `/api/ai/*`, `/api/source/switch`, `/api/branch/push`, `/api/work-push`, and the workspace-switch routes. The list lives in one frozenset in `lib/ai_tools.py`.
- **System prompt**: a short fixed prompt plus a live `GET /api/workspace-manifest` snapshot, the orientation call `docs/ai-onboarding.md` §3 prescribes.

### 2. Stateless chat turn with approval pause (`lib/ai_chat.py`)
- `Agent(model, output_type=[str, DeferredToolRequests], tools=[...])`.
- The browser holds the transcript: pydantic-ai messages serialized with `ModelMessagesTypeAdapter` and kept in `sessionStorage`. The server stores no conversation.
- `POST /api/chat/turn`, body `{messages, prompt | deferred_results}`, streams **NDJSON** frames over a `fetch` + `ReadableStream`. `EventSource` can't POST or send `X-VW-Session`. Frame types:
  - `text-delta`
  - `tool-call` / `tool-result` (for reads)
  - `approval-required` with `{tool_call_id, method, path, body}`
  - `done` with `{messages}`
  - `error`
- Approve/Deny re-POSTs `deferred_results` (`ToolApproved()` / `ToolDenied(reason)`) with the returned messages.
- **Audit**: `.pbg/events.jsonl` can't be used, because `investigation_contracts.EVENT_TYPES` allows only 4 types. Each executed mutating call instead appends one line to `<ws>/.pbg/ai-actions.jsonl`: `ts`, `session`, `provider`, `model`, `operation_id`, `method`, `path`, `status`, `approved: true`. It uses the same fsync append as `lib/event_log.append`.

### 3. Provider credentials (`lib/ai_auth.py`, mirroring `lib/github_auth.py`)
- **Providers**:
  - `anthropic`, `openai`, `google`: API key
  - `openai-compatible`: base URL plus an optional key, covering Ollama, OpenRouter and vLLM
  - `bedrock`: uses the ambient AWS credentials; boto3 is already a core dependency
- **Environment keys**: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `GOOGLE_API_KEY` are detected and shown as "from environment".
- **Where keys are kept**:
  - Local bind (`127.0.0.1`/`localhost`/`::1`): a best-effort `keyring` under service `vivarium-workbench-llm`, reusing the optional-import pattern of `github_auth._keyring_*` (`:127-166`).
  - Any other bind (hosted pod, `0.0.0.0`): memory only, per `X-VW-Session` key, never written to disk.
  - `serve_fastapi` (`lib/startup.py:54`) records the bind host on `app.state` so `ai_auth` can tell the modes apart.
- **Checking a key**: a key is checked on save with one minimal real request to the chosen model, a 1-token completion. A bad key returns 401 to the UI.
- **Secrecy**: keys are never returned by any route. A `mask_key()` helper, modeled on `github_auth.mask_token` (`:111`), scrubs them from logs and error text.
- **Model choice**: the non-secret provider and model choice lives in `~/.config/vivarium-workbench/ai.yaml`, next to the existing hint-dir convention. It never goes in `workspace.yaml`, which is git-tracked scientific record.
- **Routes** (all in `api/app.py`, logic in `lib/`, pydantic models in `lib/models.py`):
  - `GET /api/ai/status` returns `{available, providers:[{id, configured, source}], selected, storage_mode}`.
  - `POST /api/ai/credentials` takes `{provider, api_key?, base_url?}`.
  - `DELETE /api/ai/credentials/{provider}`.
  - `POST /api/ai/select` takes `{provider, model}`.
  - `POST /api/chat/turn`.
  - All of these return 503 `{error:"chat extra not installed: pip install 'vivarium-workbench[chat]'"}` when pydantic-ai isn't importable.

### 4. Frontend
- **Login**: a new "AI provider" card in the existing `#page-github` "Account" section (`templates/index.html.j2:1644`), with a provider dropdown, key or base URL, model text field, Save/Test, Remove and a status chip. The code goes in new `static/ai-login.js`, patterned on `static/github-login.js`.
- **Chat tab**, following the 4-touch tab pattern:
  - a rail link `data-page="chat"` in `index.html.j2:855-953`
  - a `<section id="page-chat" class="page">`
  - `'chat'` added to the **live-only** `validPages` arrays in `walkthrough.js` (`:844-846`, `:873-875`), with a snapshot redirect in `_switchPage` (`:764-772`)
  - a `window._loadChat` hook in `_switchPage`
- **`static/chat.js`**:
  - a message list, an input, collapsible tool-call rows, and approval cards showing method, path and pretty-printed body
  - streams through `fetch`, which goes through `session.js`'s header override
  - after a completed mutation it dispatches the existing `pbg:state` refresh so the other tabs update
- **Hiding outside live mode**: `snapshot-readonly.css` hides the rail link and page under `body.snapshot`. The read-only server keeps chat, since its write routes are already filtered out.

### 5. Packaging, docs, version
- `pyproject.toml`: `chat = ["pydantic-ai-slim[anthropic,openai,google,bedrock]>=<pinned floor verified at impl>", "httpx>=0.27", "keyring>=24"]`.
- The `Dockerfile` installs `.[chat]`. `uv.lock` is refreshed.
- `docs/ai-onboarding.md` §4.1 is rewritten: the workbench now has a built-in, provider-agnostic chat, and the skills stay the Claude Code path. §4.3 gets a correction: not every write commits to git, and many FastAPI write paths defer committing.
- New `docs/ai-chat.md` covers the architecture, the exclusion list, key storage modes and the audit log.
- `docs/ARCHITECTURE.md` and CLAUDE.md get a one-line pointer.
- Version bump to `0.4.0`, a minor bump because the design principle changes.

## Critical files
- New: `vivarium_workbench/lib/ai_tools.py`, `lib/ai_chat.py`, `lib/ai_auth.py`, `static/chat.js`, `static/ai-login.js`, `docs/ai-chat.md`
- Edit: `api/app.py` (routes, readonly allow-list for `/api/ai/*` + `/api/chat/turn`), `lib/models.py`, `lib/startup.py`, `templates/index.html.j2`, `static/walkthrough.js`, `static/snapshot-readonly.css`, `pyproject.toml`, `Dockerfile`, `docs/ai-onboarding.md`
- Reused: `lib/csrf.py` rules, `session.js` header override, `github_auth` keyring/mask patterns, `event_log.append` fsync pattern, `create_app` + `get_workspace` override test pattern

## Verification (no mocking of the thing in doubt)
- **Unit, real app** (`tests/test_ai_tools.py`), using `create_app()` with a `get_workspace` override on a `tests/_fixtures` workspace:
  - The operation index is built from the **real** `app.openapi()`, and every excluded route is absent.
  - A real GET through `call_operation` returns the same JSON as `TestClient`.
  - A POST raises `ApprovalRequired` and does not execute; with approval set it executes and appends to `ai-actions.jsonl`.
  - Read-only mode (`VIVARIUM_WORKBENCH_READONLY=1`) drops mutating operations from the index.
- **Agent loop** (`tests/test_ai_chat.py`): pydantic-ai's `FunctionModel`, which is scripted by the test, stands in **only for the remote LLM**. The tools, OpenAPI, app, approval pause/resume and audit all run for real. This proves:
  - read → approval-required → approve → mutation lands on disk
  - deny → no disk change
  - the NDJSON frame sequence
  - the transcript round-trips through `ModelMessagesTypeAdapter`
- **Credentials** (`tests/test_ai_auth.py`):
  - a local bind uses the keyring (the `keyring.backends.null`/fail backend is swapped in)
  - a non-local bind never touches disk or the keyring
  - status/list routes never echo a key
  - `mask_key` scrubs errors
  - 503 when the extra is missing
- **Regression**: the full `uv run pytest` (timeout 600000) stays green, the published bundle has no chat link (a publish test asserts `body.snapshot` hides it), and mypy passes on the new typed modules.
- **Live probe (required before calling done)**:
  - Run `vivarium-workbench serve` on a scratch copy of a fixture workspace with a real key (Alex's `ANTHROPIC_API_KEY`).
  - In Chrome: log in via the Account card, then ask "list the studies" (auto read), then ask the AI to create a study (an approval card appears; approve).
  - Confirm the study appears in the Studies tab and in `ai-actions.jsonl`, and that Deny leaves the tree unchanged.
  - Record a GIF.
- **Adversarial review**: run `/adversarial` on the diff before the PR.

## Delivery (BOJ)
- One branch, `feat/ai-chat`, and one PR bundling code, tests, docs and the `0.4.0` bump.
- After merge: tag `v0.4.0`, then GitHub Release, then build, then deploy.
- Keep `CURRENT.md` updated at each atomic step.

## As built — deviations from this plan (verified against the source during implementation)
The design above shipped as described except:
- The live schema has **257** operations (not 265); there is no `Static & shell` tag (static routes are `include_in_schema=False`).
- The exclusion set is broader: tags `Auth`/`AI`/`Downloads`; prefixes `/api/source/`, `/api/workspaces/` (incl. process start/stop), `/api/chat*`, `/api/ai*`, `/api/events*` (SSE); plus `/api/branch/push`, `/api/work-push`. Applied once when the index is built, so a forged `operation_id` can't reach them.
- `pbg:state` is not a refresh hook (it only mirrors `workspace.yaml` changes over SSE and nothing listens). After an approved mutation `chat.js` calls the loaders the UI's own create flow uses (`_loadInvestigations`, `_loadInvestigationSets`, `_refreshGitStatus`).
- `GET /api/ai/status` answers 200 with `available: false` without the extra (the UI needs the install hint); the other chat routes are 503. `POST /api/ai/credentials` takes a `model` (the 1-token check needs one).
- Hosted (non-loopback) servers keep the provider/model selection in per-session memory, not `ai.yaml`, and restrict `openai-compatible` base URLs to public https (SSRF).
- `chat.js` resolves URLs through `DataSource.apiUrl` (hosted base path); the `asset_version` stamp in `lib/report.py` now covers the chat assets.
- CI test/type jobs install `--extra chat` so the chat tests run rather than skip. The Docker image (what hosted workbenches run) shipped with it too, then dropped it: chat stays out of hosted deployments until the chat hardening (#1238, #1247) is merged, released and deployed, and that release puts `--extra chat` back in the image.
See [../ai-chat.md](../ai-chat.md) for the shipped design.
- After the adversarial review: the audit is intent-first and approvals are single-use; the session key is hashed in the audit file; hosted servers don't lend their env/AWS credentials without an operator opt-in; keyring mode requires a loopback `Host` and any proxy flag/base path/unknown bind falls back to memory; the exclusion list also covers workspace listing, PR creation, host installs and binary reads; a failed turn after an approved mutation checkpoints the transcript (`done` with `incomplete: true`).
- Second review round: a saved `openai-compatible` key is reused only for an unchanged base URL; the claim digest includes the issuing response's timestamp; each turn runs in its own task (queue-fed) so a client disconnect is quiet; Retry works for resume bodies (single-use approvals make it safe) and survives a reload; Host with userinfo is rejected; a 403/other error from `/api/ai/status` is shown instead of the "install the extra" hint.
- Third review round: the retry record is compact, persisted, and set by the state transition that starts a turn (a reload mid-turn recovers); a new prompt over a dangling transcript is repaired server-side (synthetic "outcome unknown" result, nothing executed); the "already executed" refusal carries the recorded outcome.
- **UI redesign (user direction, after the first live try):** no dedicated Chat page and no Account-page card. The chat is a right-docked panel styled exactly after marimo's AI panel (screenshot: `integrated-chat-example.png`), toggled from an **AI** rail item; the gear opens the provider settings inside the panel. Every control marimo's panel has is implemented where the workbench has an equivalent: modes (Manual = no tools, Ask = read-only, Agent = read+write with approval, Code Mode listed but disabled), model dropdown, capabilities popover (workspace-summary switch + live counts), `@` context mentions, text-file attachments, searchable previous-chats history, editable user messages, queued messages, Stop strip, reasoning accordion, resizable panel. Server additions: `mode`/`include_manifest` on `/api/chat/turn`, `reasoning-delta` frames, `GET /api/ai/capabilities`.
- **Round 2 (user direction, after trying the panel live):**
  - **Providers**: Ollama (local, no key, default `http://localhost:11434/v1`; its URL lives in `ai.yaml`, not the keychain) and OpenCode Go (fixed `https://opencode.ai/zen/go/v1`, key) join OpenAI/Anthropic/Google/Bedrock/OpenAI-compatible, in marimo's order. An earlier cut had visible Discover / Add-model controls; they were removed — marimo has neither. The model field is marimo's dropdown over its own registry plus a custom-model box, except that Ollama's submenu lists the models actually installed (`GET /api/ai/ollama-models`), because a static catalogue names models a machine never pulled.
  - **Dropdown**: mode is a real listbox; model is marimo's provider→models submenu picker with an info card and a custom-model box, in both the footer and Settings. Menus render at the document level so a short panel can't clip them.
  - **Docking**: default is now **left** (like marimo). Drag the AI chip to the left/right/bottom edge, or use the header's Move-panel menu; three CSS variables (`--viv-ai-left|right|bottom`) keep the maximized composite card, the study iframes and `_fitEmbedToViewport` clear of the panel in every dock.
  - **Automation** ("everything except push"): the install operations (`catalog-install/uninstall`, `import-install`, `system-deps-install`) are now reachable behind approval. **Deviation from the round-2 plan**: `/api/source/*` and `/api/workspaces` stay excluded (re-binding the workspace breaks the turn's single-workspace/audit invariants) — the plan had proposed allowing their reads. Added `call_operation(select=)` with a shape summary for oversized results, `wait_seconds`, keep-alive `ping` frames, limits 100/150, an accurate run/poll playbook, and Approve all / Deny all. Two product gaps are pinned as strict xfail, not patched (YAML `source=` on `study-create`; in-process `study-run-*` on YAML composites).
  - **History** (user direction): chats persist in the browser on loopback servers (they were tab-only), with a per-tab current chat, cross-tab merge, and delete. Hosted servers keep them tab-only.
  - **Keychain hygiene**: status no longer reads every provider's keychain entry (7 macOS prompts per page load for anyone whose entry was created by another Python).
