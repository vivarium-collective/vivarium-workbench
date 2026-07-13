# Checkpoint: Approved plan for todo #4 (remote GovCloud demo e2e) — ready to execute WS-A → WS-F

**Updated:** 2026-07-13 (planning session). Supersedes the prior mid-diagnosis
checkpoint. The two blocking demo bugs are now root-caused and folded into a
single approved umbrella plan, **`.todo/plans/4-remote-govcloud-demo-e2e.md`**
(full mirror at `~/.claude/plans/giggly-hatching-globe.md`). No implementation
code was written this session — this checkpoint hands the plan to the next agent
to begin executing.

## Session goal

Produce a comprehensive, approved plan to close every remaining gap in the
`demo-v2ecoli` demo — fix Bug 2 (CSRF 403) + Bug 3 (Composite Explorer 500),
rewrite `WALKTHROUGH.md` to the **remote-first** protocol, verify the full e2e
GovCloud walkthrough is reproducible from the WALKTHROUGH alone, then merge +
release. **The demo now targets the REMOTE `/workbench` deployment** via
`sms-proxy.sh -s smsvpctest` → `http://localhost:8080/workbench`, NOT local serve.

## Progress table

| Item | Status | Notes |
|---|---|---|
| Sync docs to reality (NEXT_STEPS, .todo/*) | ✅ Done | Done earlier this session; MANIFEST + plans #2/#3 advanced to deployed+unverified. |
| Root-cause Bug 3 | ✅ Done (CONFIRMED) | `grep -c bigraph-loom ~/vivarium-app/v2ecoli/uv.lock` = **0** → combined image never installs `bigraph_loom` → `ModuleNotFoundError` on always-visible loom panel. |
| Root-cause Bug 2 | 🔄 NARROWED | AWS ALB omits `X-Forwarded-Host` → `--trust-proxy` is a no-op, falls back to raw `Host`. One live header capture still pending (WS-B step 1). |
| Write approved plan as todo #4 | ✅ Done | `.todo/plans/4-remote-govcloud-demo-e2e.md` + MANIFEST entry #4. |
| WS-A Bug 3 Dockerfile fix | ❌ PENDING | Next-session start point. |
| WS-B Bug 2 diagnose → allowlist | ❌ PENDING | Diagnostic first, then code + manifest. |
| WS-C WALKTHROUGH remote rewrite | 🔄 Barely started | User hand-edited line 145 (`8771`→`8080`), uncommitted. Full rewrite pending. |
| WS-D build→deploy→verify cycle | ❌ PENDING | Per fix round. |
| WS-E full e2e verification (acceptance gate) | ❌ PENDING | |
| WS-F merge + release | ❌ PENDING | Only after WS-E passes. |

## Key files touched THIS session (docs only — no code)

- `.todo/plans/4-remote-govcloud-demo-e2e.md` — **NEW**, the umbrella plan. Read this first.
- `.todo/MANIFEST.md` — added item #4; advanced #1 (✅ confirmed), #2 (❌ deployed-but-broken), #3 (❌ deployed, bigraph_loom evidence).
- `.todo/plans/2-*.md`, `3-*.md` — statuses advanced from "PENDING/plan-only" to deployed+failed-verification, with "Post-deploy diagnosis" / "Tier 2a — now unblocked" sections + dated progress trails.
- `NEXT_STEPS.md` — rewritten to the current 2-bugs-open reality (was about the resolved subpath fix).
- `SAVE_SLOT.md` — this file.
- `demos/v2ecoli/WALKTHROUGH.md` — **user hand-edit** (line 145 URL), uncommitted, NOT mine; a partial WS-C start.

## Key design decisions (the next agent must honor these)

1. **Bug 2 fix = production-grade allowed-origins allowlist** (user chose "best production-grade feasible solution"). Add `allowed_origins` to `lib/csrf.py::is_request_allowed` (exact-match short-circuit) + `VIVARIUM_WORKBENCH_ALLOWED_ORIGINS` env (via `lib/env_compat`) + repeatable `--allowed-origin` CLI flag + wire through `api/app.py::_csrf_mw` (~L493). Set the env to `http://localhost:8080` in the sms-api workbench Deployment. Preserves the CSRF guard; header-independent. NOT a blanket disable.
2. **Bug 3 fix = dashboard Dockerfile**, NOT v2ecoli. `bigraph-loom` is the workbench's dep (`pyproject.toml:47`); v2ecoli is a plain pbg-template instance (per memory `[[project_v2ecoli_as_pbg_instance]]`). Add an explicit `uv pip install --no-deps "bigraph-loom @ git+…@main"` overlay + broaden the `Dockerfile:70` sanity import to `import bigraph_loom`.
3. **Branch encapsulation** (per user): all work on `demo-v2ecoli` (./) + `patch/db-filter` (`~/sms/sms-api`) ONLY. `v2ecoli` main unchanged. Merge to `main` + version bump/release ONLY after the full e2e remote walkthrough is verified reproducible from WALKTHROUGH alone.
4. **Diagnostic-first for Bug 2**: capture the real `Origin`/`Host`/`X-Forwarded-*` arriving at the pod (curl -v through the live tunnel, or a temp `_csrf_mw` debug log) BEFORE finalizing the fix — the allowlist is correct regardless, but this confirms whether the raw Host already matches (403 from elsewhere).
5. **WALKTHROUGH local flow → Appendix G** (user chose "keep as fallback appendix"), remote flow becomes canonical §0/§1.

## Verification

- **Build**: no build step (pure Python + static assets).
- **Tests**: NOT run this session (deliberately — planning only; the full suite's prior `F` failures were never triaged and re-baselining is WS-E). Per-fix suites to run during execution: `uv run pytest tests/test_csrf_lib.py tests/test_csrf_origin_guard.py tests/test_api_app.py -k csrf`.
- **Infra confirmed live this session**: SSM tunnel is UP (`sms-proxy.sh -s smsvpctest`, PID 73960 / session-manager-plugin PID 74053 → `internal-smsvpc-…elb.amazonaws.com:80` ↔ `localhost:8080`). Routing analysis confirmed: raw SSM TCP forward, ALB path-routes `/workbench`.

## Next steps (priority order — start of next session)

1. **WS-B step 1 (diagnostic, cheapest + unblocks the biggest decision)** — with the live tunnel (still up), capture headers: `curl -v -X POST -H 'Origin: http://localhost:8080' http://localhost:8080/workbench/api/<a-safe-POST>`; or add a one-line debug log in `api/app.py::_csrf_mw` (~L490) of `origin`/`host`/`x-forwarded-host`, redeploy via WS-D, re-hit. Decides whether raw Host mismatches (→ allowlist) or the 403 is elsewhere.
2. **WS-A (Bug 3, fully local, deterministic)** — edit `Dockerfile`: add the `bigraph-loom` overlay install (mirror pbg-ptools at `Dockerfile:63-66`) + broaden the `Dockerfile:70` sanity import; run the anti-whack-a-mole lazy-import audit (grep `vivarium_workbench/` for other workbench-only deferred imports vs v2ecoli's lock).
3. **WS-B step 2-4 (Bug 2 code + manifest)** — implement the allowlist in `lib/csrf.py`/`lib/env_compat.py`/`api/app.py`/`cli.py` (+ `uvicorn.run(..., proxy_headers=True, forwarded_allow_ips="*")` in `startup.py:120`), add tests, add the env to `~/sms/sms-api` `kustomize/base/workbench/workbench.yaml` on `patch/db-filter`.
4. **WS-D** — `gh workflow run build-and-push.yml --ref demo-v2ecoli` → note SHA → bump workbench `newTag` in `sms-api-stanford-test/kustomization.yaml` → `kubectl apply -k` → `rollout status`.
5. **WS-C** — rewrite WALKTHROUGH remote-first (the user already started line 145).
6. **WS-E** then **WS-F** per the plan.

## Deploy drift to reconcile (on `patch/db-filter`)

- Committed workbench Deployment (`kustomize/base/workbench/workbench.yaml`) has `--base-path /workbench` but **no `--trust-proxy`** (was live-patched only). Image overlay pins **`0.1.1`**, not the live dev SHA `481b3f2`.
- Suggest-commits protocol applies: agent stages, user commits via a shown one-liner. Do-not-commit list: CLAUDE.md, AGENTS.md, Makefile, todo.md, .pr-body-*.md. (`.todo/*`, NEXT_STEPS.md, SAVE_SLOT.md are OK to stage.)

## Quick reference

```bash
# Tunnel (currently UP — verify before assuming)
ps aux | grep sms-proxy | grep -v grep
AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
  ~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest    # → localhost:8080/workbench

# Cluster
export AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
  KUBECONFIG=/Users/alexanderpatrie/.kube/kube_stanford_test.yml
kubectl -n sms-api-stanford-test logs -f deploy/workbench
kubectl -n sms-api-stanford-test rollout status deploy/workbench

# Build + deploy cycle (dev iteration)
gh workflow run build-and-push.yml --ref demo-v2ecoli   # → git-SHA-tagged image
#  then bump newTag in ~/sms/sms-api kustomize/overlays/sms-api-stanford-test/kustomization.yaml
kubectl apply -k ~/sms/sms-api/kustomize/overlays/sms-api-stanford-test

# Per-fix tests
uv run pytest tests/test_csrf_lib.py tests/test_csrf_origin_guard.py tests/test_api_app.py -k csrf

# Branch state: demo-v2ecoli (./ HEAD 9cf1658), patch/db-filter (~/sms/sms-api), main (~/vivarium-app/v2ecoli)
```

## Related

- `.todo/plans/4-remote-govcloud-demo-e2e.md` — **the plan** (read first)
- `.todo/MANIFEST.md`, `.todo/plans/2-*.md`, `3-*.md`
- `~/.claude/plans/giggly-hatching-globe.md` — plan mirror
- PR #465 (workbench→main, open), sms-api PR #169 (open; its `--trust-proxy` may be superseded by the allowlist)
