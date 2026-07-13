1. **(.todo/plans/1-fix-study-detail-interactivity.md)**:

### Name

Fix: Study-detail page unstyled/non-interactive under /workbench subpath

### Status

PR OPEN (REVIEW_REQUIRED) — https://github.com/vivarium-collective/vivarium-workbench/pull/465; merged into demo-v2ecoli (861aefa). k8s image rebuilt+deployed to sms-api-stanford-test (e74b644), curl-verified. Remaining: human browser click-through only (see NEXT_STEPS.md); PR merge to main is hygiene-only, non-blocking.

---

## 2. **(.todo/plans/2-fix-csrf-origin-guard-reverse-proxy.md)**:

### Name

Fix: CSRF/origin guard 403s all POST/DELETE behind ALB reverse-proxy subpath deployment

Linked tasks: independent of #1 and #3 (different subsystem, no shared files, no ordering dependency); shares the broader demo-v2ecoli e2e-walkthrough context with both.

### Status: PENDING

Plan written 2026-07-13, root-caused and fix-designed via Explore + Plan agents, verified against live source (`lib/csrf.py`, `lib/env_compat.py`, `api/app.py`, `cli.py`). No code written yet — awaiting "proceed".

---

## 3. **(.todo/plans/3-fix-composite-resolve-unhandled-errors.md)**:

### Name

Fix: composite-resolve swallows real exceptions; colony (pymunk) composite 500s unobservably

Linked tasks: independent of #1 and #2 (different subsystem, no shared files, no ordering dependency); shares the broader demo-v2ecoli e2e-walkthrough context with both.

### Status: PENDING

Plan written 2026-07-13, root-caused and fix-designed via Explore + Plan agents, verified against live source (`lib/composite_resolve.py`, `api/app.py`). Tiered: Tier 1 (logging) + Tier 2 (guard unvalidated seams) ready to implement; Tier 2a (dependency fix, if needed) explicitly gated on deployed-log evidence not yet available. No code written yet — awaiting "proceed".

---


