# Plan 7 — Production-grade progress feedback for long-running UI-triggered processes (first case: the pinned-build run card)

## Name

Feat: sleek, UX-friendly visual feedback (progress bar + spinner) for any
long-running UI-triggered process, starting with "Run against pinned build" in
the Simulations tab of an investigation study
(`http://localhost:8080/workbench#investigations`).

Linked tasks: builds on #5 (pinned-build remote runs) — the run card whose submit
now fans out to ParCa → Ray MNP → land is exactly the process that currently gives
the user no rich progress signal. Independent of the Segment 7 work (#6). Source:
`.todo/_backlog.md` item (b). Dashboard-only (`vivarium-dashboard@demo-v2ecoli`);
no sms-api / v2ecoli changes required for the first iteration (it consumes the
polling/SSE the backend already exposes).

## Status: 📋 PLANNED — approved to create as a todo item; NOT yet implemented

Awaits the literal "proceed" before code is written (todo protocol). Captured now
so the backlog Prompt Queue is drained into a tracked plan.

## Problem

Clicking "Run against pinned build" kicks off a multi-minute remote pipeline
(submit → Batch SUBMITTED/RUNNABLE = Ray MNP provisioning ≈ 8 min → STARTING →
RUNNING ≈ 5 min → completed → landed). Today the card gives thin feedback, so a
demo viewer can't tell a slow-but-healthy run from a stuck one. This is the first
and most visible instance of a general gap: **no attractive, robust visual
feedback for long-running UI-triggered processes.**

## Desired outcome

- A combined **progress bar + spinner** treatment on the run card that maps the
  known backend phases to visible stages: submitted → provisioning (Ray) →
  ParCa → running → landing → done, with a sensible indeterminate state while a
  phase has no numeric progress.
- Degrades gracefully in the published/read-only bundle (no live backend → no
  live progress).
- Reusable: the component/pattern should be extractable so the next long-running
  action (exports, publish, migrate) can adopt it without a rewrite.

## Workstreams

### WS-1 — Inventory the existing signal
1. Enumerate what the pinned-build path already emits for progress: the polling
   endpoint(s) + any SSE stream, and the phase vocabulary
   (`_BATCH_STATE_MAP`, `phase:"built"`, ParCa dependency, `remote_origin`).
2. Decide the phase→stage mapping and which stages are determinate vs
   indeterminate.

### WS-2 — Component
1. Build a small, dependency-free progress component (vanilla JS + CSS, matching
   the no-bundler frontend) — progress bar for determinate stages, spinner for
   indeterminate, accessible (ARIA live region, `prefers-reduced-motion`).
2. Wire it into the "Run against pinned build" card in the Simulations tab.

### WS-3 — Graceful degradation + reuse
1. No-op / static state under the snapshot data source.
2. Factor the component so a second call site can reuse it.

### WS-4 — Verify
1. Drive a live pinned-build run through the tunnel and watch the card walk the
   stages end-to-end (submit → Ray → ParCa → running → landed).
2. Confirm no regression to the existing submit/land flow.

## Notes / references

- `SAVE_SLOT.md` "Ray/queued mechanism" + "Pinned-build live facts" sections have
  the authoritative phase timings and state map.
- Frontend is vanilla JS, no bundler — keep the component self-contained.
- memory `[[project_pinned_build_remote_runs]]`.
