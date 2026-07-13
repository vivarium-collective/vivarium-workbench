## 4. **(.todo/plans/4-remote-govcloud-demo-e2e.md)**:

### Name

Close all gaps for a reproducible remote GovCloud dashboard demo (fix bugs #2 + #3; rewrite WALKTHROUGH remote-first; verify e2e; merge + release)

Linked tasks: **supersedes/absorbs the remaining open work in #2 and #3** (they are
the two blocking bugs inside this larger goal) and depends on #1 (already ✅). This
is the umbrella item that carries the demo to a merge-and-release finish.

### Status: PLANNED — approved plan, no code written yet

Comprehensive plan approved 2026-07-13. Root causes for both blocking bugs are
established (see below); execution proceeds via an iterative
build(gh-action)→deploy→verify cycle encapsulated on `demo-v2ecoli` (./) and
`patch/db-filter` (`~/sms/sms-api`) ONLY, until the full e2e remote walkthrough is
verified reproducible from `WALKTHROUGH.md` alone — then merge each branch to its
`main` and cut a proper version bump/release. Full source plan mirrored at
`~/.claude/plans/giggly-hatching-globe.md`.

### Goal / demo redefinition

The demo now runs against the **remote `/workbench` k8s deployment**
(`sms-api-stanford-test`), reached via `sms-proxy.sh -s smsvpctest` →
`http://localhost:8080/workbench` — NOT the old local `serve --port 8771`. Canonical
operator sequence the rewritten WALKTHROUGH must open with:
1. GovCloud auth: `stanford test` (AWS_PROFILE=stanford-sso, us-gov-west-1).
2. `cd <sms-cdk-clone>/scripts`.
3. `sms-proxy.sh -s smsvpctest`; confirm the proxy banner lists `/workbench`.
4. `open http://localhost:8080/workbench`.
5. Proceed to Segment 1 onward (current `WALKTHROUGH.md` line 142+).

### Branch encapsulation

- `demo-v2ecoli` (`~/vivarium-app/vivarium-dashboard`, ./) — dashboard code, `Dockerfile`, `demos/v2ecoli/WALKTHROUGH.md`, tests.
- `patch/db-filter` (`~/sms/sms-api`) — k8s manifests (workbench Deployment, overlay image tag/env).
- `main` (`~/vivarium-app/v2ecoli`) — **no changes anticipated** (loom dep belongs to the workbench, not v2ecoli); listed only for completeness.

### Root cause — Bug 3 (Composite Explorer 500) — CONFIRMED

`grep -c bigraph-loom ~/vivarium-app/v2ecoli/uv.lock` = **0**. The combined image
builds its env from v2ecoli's lock (`Dockerfile:43-45`), so the workbench-only dep
`bigraph-loom` (declared in *workbench's* `pyproject.toml:47`) is **never
installed**. `resolve_loom_asset()` (`lib/static_serving.py:112-124`) lazily does
`from bigraph_loom import asset_dir`, so the missing module passes the build-time
sanity import (`Dockerfile:70` imports `vivarium_workbench` but not `bigraph_loom`)
and only throws `ModuleNotFoundError` at runtime. The always-visible loom panel
fires a loom-asset request for ANY composite → generic "internal server error".

### Root cause — Bug 2 (CSRF 403 on all POST) — NARROWED (one diagnostic pending)

AWS ALB does **not** emit `X-Forwarded-Host`, so the shipped `--trust-proxy` fix
(`lib/csrf.py:62-70`, read at `api/app.py:493-494`) has nothing to consult and
silently falls back to the raw `Host`. `sms-proxy.sh` is a raw SSM TCP forward
(`localhost:8080 → ALB:80`); `uvicorn.run` (`startup.py:120`) sets no
`proxy_headers`. The exact `Origin`/`Host` arriving at the pod was **never
captured** — that single live diagnostic is the missing piece before finalizing.

### Deploy drift (must be reconciled on `patch/db-filter`)

- Committed workbench Deployment (`kustomize/base/workbench/workbench.yaml`) passes `--base-path /workbench` but **NOT `--trust-proxy`** (that was live-patched only / lives on unmerged PR #169).
- Overlay pins image `0.1.1` (`.../sms-api-stanford-test/kustomization.yaml`), not the live-patched dev SHA `481b3f2`.
- Build/deploy cycle: `gh workflow run build-and-push.yml --ref demo-v2ecoli` (no `version` input) → git-short-SHA image tag; publishing a GitHub Release → release-tag image. Overlay `newTag` selects which runs.

### Fix decisions

- **Bug 2 → production-grade allowed-origins allowlist** (standard secure ALB/subpath pattern, à la Django `CSRF_TRUSTED_ORIGINS`). Operator declares the browser-facing origin explicitly; deterministic, header-independent, preserves the CSRF guard. Gated behind the live header capture.
- **Bug 3 → fix in the dashboard `Dockerfile`** (explicit `bigraph-loom` overlay install), NOT v2ecoli (per memory: v2ecoli is a plain pbg-template instance; the loom dep is the workbench's).

### Workstreams

**WS-A — Bug 3: install `bigraph-loom` in the combined image (`demo-v2ecoli`)**
1. `Dockerfile`: after the workbench `--no-deps` install (~L55) / alongside the pbg-ptools overlay (`Dockerfile:63-66`), add `uv pip install --python /app/v2ecoli/.venv/bin/python --no-deps "bigraph-loom @ git+https://github.com/vivarium-collective/bigraph-loom.git@main"` (pin identical to `pyproject.toml:47`).
2. `Dockerfile:70`: broaden the sanity import to include `bigraph_loom` so regressions fail the build.
3. Anti-whack-a-mole audit: grep `vivarium_workbench/` for other *lazy* third-party imports of workbench-only deps (e.g. `pbg-basic-processes`, `investigation-contracts` per the `Dockerfile:41` comment) not caught by `import vivarium_workbench`; add each missing one to the overlay + sanity import; cross-check against v2ecoli's `uv.lock`.

**WS-B — Bug 2: diagnose, then allowlist (`demo-v2ecoli` code + `patch/db-filter` manifest)**
1. Diagnostic FIRST (on the already-deployed pod, through the live tunnel): `curl -v -X POST -H 'Origin: http://localhost:8080' http://localhost:8080/workbench/api/<safe-POST>` and/or a temporary one-line debug log of `Origin`/`Host`/`X-Forwarded-Host`/`X-Forwarded-For` in `_csrf_mw`.
2. Code (`demo-v2ecoli`): additive `allowed_origins` param on `lib/csrf.py::is_request_allowed` (exact-match short-circuit before same-origin); `allowed_origins_via_env(env)` reading `VIVARIUM_WORKBENCH_ALLOWED_ORIGINS` (comma-sep) via `lib/env_compat.get_env` + suffix constant in `lib/env_compat.py`; wire through `api/app.py::_csrf_mw` (~L493); add repeatable `--allowed-origin` to `cli.py` `p_serve` (mirror `--trust-proxy` L444) setting the env in `cmd_serve` (mirror L51-52). Complementary: `uvicorn.run(..., proxy_headers=True, forwarded_allow_ips="*")` in `startup.py:120`.
3. Tests: extend `tests/test_csrf_lib.py`, `tests/test_api_app.py::TestCsrfMiddleware`, `tests/test_csrf_origin_guard.py` (allowlist allows despite Host mismatch; empty allowlist unchanged; e2e via `dashboard_client`).
4. Manifest (`patch/db-filter`): add `VIVARIUM_WORKBENCH_ALLOWED_ORIGINS=http://localhost:8080` to the workbench container `env:` in `kustomize/base/workbench/workbench.yaml`; drop the never-effective `--trust-proxy` unless the diagnostic surfaces a real `X-Forwarded-Host`.

**WS-C — Rewrite `demos/v2ecoli/WALKTHROUGH.md` remote-first (`demo-v2ecoli`)**
1. Replace §0 + §1 with the 5-step canonical remote sequence above.
2. Standardize the tunnel on **`sms-proxy.sh`** (confirmed to route `/workbench`, `/` PTools, `/docs` SMS API, `/sms/sms.html` — covers Segments 6 & 7 on one port); retire `ptools-proxy.sh` references.
3. Update every segment assuming local serve: drop `--port 8771`/`serve --workspace`, change URLs to `localhost:8080/workbench`; update Appendix C timing + Appendix F must-knows.
4. Fold confirmed fixes into Appendix E troubleshooting.
5. Add **Appendix G — Local Dev (offline)** preserving the `serve --workspace . --port 8771` recipe.
6. Refresh `Last verified` + branch header after WS-E passes.

**WS-D — Iterative build → deploy → verify cycle (`demo-v2ecoli` + `patch/db-filter`)**
Per round: (1) commit on `demo-v2ecoli` → `gh workflow run build-and-push.yml --ref demo-v2ecoli` → note SHA tag. (2) On `patch/db-filter`, bump workbench `newTag` in `.../sms-api-stanford-test/kustomization.yaml` to that SHA (+ env changes) → `kubectl apply -k kustomize/overlays/sms-api-stanford-test` → `kubectl -n sms-api-stanford-test rollout status deploy/workbench`. (3) Verify via logs/browser. Repeat. Never touch `main` during iteration.

**WS-E — Full e2e remote walkthrough verification (acceptance gate)**
Drive the entire rewritten WALKTHROUGH through the tunnel in a browser as the acceptance test. Confirm Bug 2 (Run / Run-remotely POSTs succeed), Bug 3 (Composite Explorer + loom panel render for `parca`/`colony`/`baseline`), and surface any other remote-only gaps: base-path correctness of other static assets & the 3D/analyses viewers (Segment 7), Segment 6 remote-run reachability through `localhost:8080`. Fix via WS-D iterations. Re-baseline the full `pytest` suite (prior `F`s never triaged).

**WS-F — Merge + release (ONLY after WS-E passes & is reproducible from WALKTHROUGH alone)**
1. `vivarium-workbench`: finish PR #465 (`demo-v2ecoli`→`main`) through review (never auto-merge, per policy) → merge → cut a GitHub Release (version bump) → CI builds the release-tagged image.
2. `sms-api`: PR `patch/db-filter`→`main` → review → merge; set overlay `newTag` to the new workbench release tag.
3. Update deprecated-alias/version notes; mark #2/#3/#4 + `SAVE_SLOT.md` resolved.

### Tests

- Per-fix (local): `uv run pytest tests/test_csrf_lib.py tests/test_csrf_origin_guard.py tests/test_api_app.py -k csrf`. Bug 3 has no local repro (dep present in dev venv) — verified in-image via the broadened `Dockerfile:70` sanity import at build time.
- Full-suite re-baseline in WS-E (prior run's `F`s untriaged).

### Verification

- Per-round (deployed): `kubectl -n sms-api-stanford-test logs deploy/workbench` shows no `ModuleNotFoundError`/`403`; browser POSTs succeed; Composite Explorer renders.
- Acceptance (WS-E, the merge gate): a clean operator, following ONLY the rewritten WALKTHROUGH, completes all 8 segments through `localhost:8080/workbench` with zero manual workarounds.

### Open risk

Bug 2's exact fix is gated on the WS-B step-1 header capture. If it shows raw `Host`
already equals `Origin` (403 from elsewhere), the allowlist still lands as correct
hardening but the true 403 source is chased from the captured evidence before WS-B
is declared done.

### Progress notes

- **2026-07-13 (planned)**: Comprehensive plan approved. Bug 3 root cause CONFIRMED (v2ecoli `uv.lock` has zero `bigraph-loom`); Bug 2 NARROWED (ALB omits `X-Forwarded-Host`; one live header capture pending) — production-grade allowlist chosen. Deploy drift on `patch/db-filter` catalogued (missing `--trust-proxy`, image pinned `0.1.1` vs live SHA `481b3f2`). Evidence gathered by reading `Dockerfile`, `lib/csrf.py`, `api/app.py:455-524`, `lib/startup.py:100-121`, `cli.py`, `demos/v2ecoli/WALKTHROUGH.md`, the sms-api `kustomize/base/workbench/workbench.yaml` + stanford-test overlay + `target-group-binding.yaml`, `sms-proxy.sh`, and `.github/workflows/build-and-push.yml`. No code written yet. Full plan mirror: `~/.claude/plans/giggly-hatching-globe.md`.

---
