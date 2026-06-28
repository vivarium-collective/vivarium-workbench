# Phase A — Domain Event Bus (RFC-0002)

**Date:** 2026-06-28
**Status:** Design — approved decisions, pending spec review
**Implements:** RFC-0001 Part II §4 + IV (Event Contract, emit-side of Provenance) · RFC-0002 Phase 0 (slice) + Phase A
**Repos touched:** `investigation-contracts` (NEW), `vivarium-dashboard` (emitter), `pbg-superpowers` (reactor)

## Goal

Prove the closed-loop nervous system end-to-end with **one event type — `FindingCreated`** — flowing from a computational-spine write, through a durable typed log, to an agentic-spine reactor, with the event envelope **typed as a bigraph-schema contract**. This single vertical slice de-risks the whole RFC-0002 architecture: the shared contracts package, a typed event, the durable append-only log, emit-after-commit, SSE replay/resume, and subscribe-and-react — without a 14-module retrofit.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| Contracts package home | **New standalone repo** `investigation-contracts` (clear name; "AIG" stays the *concept*). Depends only on `bigraph-schema`; both spines depend on it. Acyclic DAG. |
| Envelope representation | **Bigraph-schema type** (canonical) + a **pydantic mirror** for transport at API boundaries (mirrors the dashboard's `lib/models.py` pattern). |
| Event transport | **New** SSE endpoint `GET /api/events/log` tailing `workspace/.pbg/events.jsonl`. The existing polling `/api/events` (workspace-state, UI refresh) is **left untouched** — no regression. |
| Emit-after-commit seam | Scoped to the **one `POST /api/finding` path** for Phase A. The general chokepoint (wrapping the shared atomic-write for all 14 mutation modules) is deferred to Phase C. |
| EventClient consumption | **Tails the jsonl file directly** from a persisted cursor (headless-friendly; matches how pbg skills run). The SSE endpoint is built but the Phase-A client does not depend on it. |
| Phase-A handler behavior | **Logs + writes a reaction record** back into the workspace (a visible closed loop) — no real reasoning yet. |

## Current state (grounded)

- `vivarium_dashboard/lib/events.py` (97 lines) — `workspace_state_stream` **polls** `workspace.yaml` every 1 s and emits `event: state` with the whole file when it changes (UI-refresh only). Wrapped by `GET /api/events` (`api/app.py:2315`). **Leave this alone.**
- Writes span **14 `lib/*_mutations.py` modules** over shared helpers: `vivarium_dashboard/lib/atomic_io.py:atomic_write_text` and `pbg_superpowers.study_io.{atomic_write, save_yaml_atomic}`. (Confirms Phase A must stay minimal.)
- `pbg-superpowers` has **no** event client — built fresh.
- `bigraph-schema` is the type substrate (a `TypeSystem`/core with `register` + `_apply`/`_check`); the exact registration API is confirmed at implementation time.
- Dashboard FastAPI route + pydantic-model conventions (`lib/models.py`, `Depends(get_workspace)`, `Union[Model, JSONResponse]`) are the patterns to follow.

## Architecture

```
            bigraph-schema (type substrate)
                     │
                     ▼
        investigation-contracts  (NEW)
        provenance · event_envelope · finding   ← bigraph-schema types
        (+ pydantic mirror for transport)         the ONLY shared schema
              ┌──────────────┴───────────────┐
              ▼                              ▼
       pbg-superpowers               vivarium-dashboard
       EventClient (reactor)         emitter + tools
       tails events.jsonl   ◄──────  POST /api/finding → emit FindingCreated
       from a cursor                 GET  /api/events/log (SSE, new)
              └──────────► workspace/.pbg/events.jsonl ◄──────────┘
                            (durable append-only log)
```

### Component ① — `investigation-contracts` (new repo)

**Dependency:** `bigraph-schema` only. No I/O, no reasoning, no execution.

**Provides (bigraph-schema types, registered into a shared core builder):**
- `provenance` — `{actor: enum[human,agentic,computational], agent_id, timestamp, source_objects: list[reference], justification, tool, commit}`.
- `lifecycle_state` — a parameterized type carrying `_states` + `_transitions` (the thin transition-table layer); its `_apply` accepts only legal `from→to` transitions, rejecting illegal ones. This is the §2 caveat made concrete and is the type-substrate validation that the whole RFC leans on.
- `event_envelope` — `{event_id: string, type: event_type, occurred_at, actor, subject: reference, transition: {from, to}, provenance, payload: tree[any], schema_version: integer}`. `event_type` enum starts with `FindingCreated` (extensible).
- `finding` — minimal AIG node: inherits a small `investigation_node` base (`id, type, lifecycle_state, owner, provenance, validation_status`), adds `statement, runs: list[reference]`. `lifecycle_state` states `proposed→reviewed→accepted|rejected`.

**Also provides (transport adapter):**
- `models.py` — pydantic `EventEnvelope` + `Finding` mirroring the bigraph-schema types for FastAPI request/response validation.
- `validate_envelope(d: dict) -> tuple[bool, str|None]` — validates a dict against the bigraph-schema `event_envelope` type via the core; returns `(ok, error)`.
- `read_log(path, cursor: str|None, types: list[str]|None=None) -> list[dict]` — **the canonical jsonl reader**, imported by both spines (the dashboard SSE and the pbg `EventClient`). Returns envelopes after `cursor` (exclusive), optionally filtered by `type`. Tolerant: missing file → `[]`; malformed line → skipped, never fatal.
- `make_core()` — builds/returns the bigraph-schema core with the contract types registered (the shared core builder both spines call).
- `__version__` + the envelope's `schema_version` constant.

**Packaging:** `pyproject.toml` (PyPI-installable name `investigation-contracts`, importable as `investigation_contracts`), `requires-python >=3.11`, dep `bigraph-schema`. Installed `-e` into both spines' venvs for development. A `core.py build_core()` is **not** needed (no workspace processes); the type registry is the contract.

### Component ② — vivarium-dashboard (emitter)

- **`lib/event_log.py`** — the durable append-only **writer** over `workspace/.pbg/events.jsonl`:
  - `append(ws_root, envelope: dict) -> str` — validates via `investigation_contracts.validate_envelope`, assigns a monotonic `event_id` from a **sidecar counter** `workspace/.pbg/events.seq` (read int → increment → atomic write, zero-padded to a sortable width; O(1), no full-file scan, race-safe under a file lock), writes one JSON line (open `a`, `write`, `flush`, `fsync`), returns the `event_id`.
  - Reading is **not** redefined here — the dashboard reads the log via the canonical `investigation_contracts.read_log` (below).
  - Tolerant: a missing log → no-op-safe; the writer never partially writes a line (single `write` of one `json.dumps(...) + "\n"`).
- **`emit_event(ws_root, *, type, subject, transition, actor, provenance, payload) -> str`** — builds the envelope (stamps `occurred_at` after the caller's write, `schema_version`), calls `event_log.append`. **Contract: callers invoke this only after their atomic state write returns.**
- **`POST /api/finding`** (`api/app.py` + a small `lib/finding_views.py` worker):
  - Body (pydantic `FindingCreateBody`): `{study, statement, runs: list[str], hypothesis?: str}`.
  - Resolves the study (layout-aware, as `build_study_readouts` does), writes a minimal Finding node atomically (a `findings/<id>.yaml` under the study dir, or appended to `study.yaml findings[]` — **decision: a `findings/` entry file**, so findings are addressable nodes from day one), THEN `emit_event(type="FindingCreated", subject=<finding id>, transition={from:None,to:"proposed"}, actor="agentic", provenance=..., payload={study, statement})`.
  - Returns `{finding_id, event_id}` (200) or `JSONResponse` (4xx). Validates the Finding against `investigation_contracts` before writing (never-fabricate parity).
- **`GET /api/events/log`** (new SSE, separate from `/api/events`):
  - `StreamingResponse(media_type="text/event-stream")` tailing `events.jsonl`.
  - Query: `?since=<event_id>` (replay-from-cursor) + `?type=<event_type>` (filter). Honors the `Last-Event-ID` request header (resume) — `since` and `Last-Event-ID` resolve to the same cursor; the header wins if both present.
  - Each frame: `id: <event_id>\nevent: <type>\ndata: <envelope json>\n\n`. After replaying history past the cursor, polls the log for new lines (reuse the existing poll-interval pattern from `events.py`).

### Component ③ — pbg-superpowers (reactor)

- **`pbg_superpowers/event_client.py`** — `EventClient`:
  - `__init__(ws_root, consumer: str)` — cursor persisted at `workspace/.pbg/event_cursor.<consumer>` (a file holding the last handled `event_id`).
  - `on(event_type, handler)` — register a handler `fn(envelope: dict) -> None`.
  - `poll_once() -> int` — reads the log after the cursor via `investigation_contracts.read_log` (tailing the jsonl directly), dispatches each to matching handlers, advances + persists the cursor **after** each handled event (at-least-once). Returns count handled.
  - `run(poll_interval=1.0)` — loop calling `poll_once`. Headless; no live server required.
  - **At-least-once → handlers must be idempotent**, keyed by `event_id`.
- **One handler** (`_on_finding_created`): on `FindingCreated`, writes a small structured reaction record into `workspace/.pbg/reactions/<event_id>.yaml` (`{observed_event, finding_id, study, noted_at, next_action: "stub"}`) and logs it. Proves the agentic spine reacts and closes a visible loop, without real reasoning.

> To avoid reader drift, the **canonical jsonl reader lives in `investigation_contracts`** (`read_log(path, cursor, types)`), imported by both the dashboard SSE and the pbg `EventClient`. The dashboard's `event_log.py` owns only the *writer* (`append`) + `emit_event` + the FastAPI seam.

## Data flow

`agent/human → POST /api/finding` → dashboard writes `findings/<id>.yaml` (atomic) → `emit_event(FindingCreated)` appends to `events.jsonl` **after commit** → `EventClient.poll_once` reads from cursor → dispatches to `_on_finding_created` → reaction record written + cursor advanced.

## Error handling — the drift guard

- **Emit only after commit.** `emit_event` is called after `atomic_write` returns; a failed write emits nothing. If the *append* fails after a successful write, the YAML state is still truth and the next reconcile (future phase) recovers — the log is durable-but-best-effort; **state is the source of truth, the log is the notification**.
- **At-least-once.** A crash between handling and cursor-persist replays the event; handlers idempotent on `event_id` (the reaction file is keyed by `event_id` → overwrite-safe).
- **Validation.** Malformed envelopes are rejected by `investigation_contracts.validate_envelope` before append; malformed log lines are skipped by readers, never fatal.
- **Tolerant readers.** Missing log/cursor → empty/zero; never crash.

## Testing

**investigation-contracts**
- `validate_envelope` accepts a well-formed envelope, rejects one missing a required field / bad `event_type`.
- `finding` type validates a good node; an **illegal `lifecycle_state` transition is rejected** (`accepted→proposed`), a legal one accepted (`proposed→reviewed`) — proves the transition layer.
- `read_log(path, cursor, types)` round-trips + filters by type + respects cursor.

**vivarium-dashboard**
- `event_log.append` then `investigation_contracts.read_log` round-trips; monotonic `event_id` from the sidecar counter; malformed line skipped by the reader.
- `POST /api/finding`: writes the finding file AND the event appears in the log **after** (emit-after-commit) — and a simulated write failure emits **no** event.
- `GET /api/events/log`: replays from `?since=`, honors `Last-Event-ID`, filters by `?type=`, emits new events appended after connect. (TestClient + a bounded read.)
- Existing `/api/events` workspace-state stream untouched (regression check).

**pbg-superpowers**
- `EventClient.poll_once` reads from cursor, dispatches `FindingCreated` to the handler, advances + persists the cursor; **idempotent on replay** (re-poll from an old cursor re-handles but the reaction file is overwrite-stable).
- Handler writes the reaction record with the expected shape.

**End-to-end** (the de-risking test)
- In a tmp workspace: `POST /api/finding` → assert `events.jsonl` has one `FindingCreated` after the finding file exists → run `EventClient.poll_once` → assert the reaction record exists and the cursor advanced.

## Out of scope (later phases)

- Instrumenting the other 13 mutation modules / the general emit-after-commit chokepoint (Phase C).
- The other six event types (`StudyValidated`, `RunStarted`, `RunCompleted`, `BehaviorTestGraded`, `VisualizationGenerated`, `InvestigationPublished`) — add once the one-event slice is proven.
- The full typed AIG node promotion + evidence chain (Phase B).
- Multi-consumer fan-out, transactional mutations, conflict detection (Phase D).

## Rollout notes

- `investigation-contracts` installed `-e` into both the dashboard worktree venv and the pbg-superpowers venv for development.
- Dashboard changes land on `feat/phase-a-event-bus` (this worktree, off `origin/main`). pbg-superpowers changes on a sibling branch. The new repo gets its own initial commit + PR.
- Keep all three changes additive on the FastAPI seam; do not touch the legacy `server.py` or the existing workspace-state SSE.
