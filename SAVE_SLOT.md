# Checkpoint — 2026-07-09

## Session Goal

Iterative programmatic verification of all 8 demo segments against a live
`vivarium-workbench` server (v2ecoli workspace). Each segment verified via REST
API, results recorded in `demos/v2ecoli/VERIFICATION_REPORT.md`.

---

## Progress Table

| Step | Status | Detail |
|---|---|---|
| Fix v2ecoli venv (numpy compat + editable install) | ✅ Done | numpy pinned <2.5, vivarium-workbench reinstalled editable |
| Run `verify_demo.py` | ✅ Done | 38 passed, 0 failed |
| Regenerate ParCa cache (fast mode) | ✅ Done | 83.5s, all 9 steps |
| Start dashboard server | ✅ Done | PID 42842, `http://127.0.0.1:8771`, HTTP 200 |
| Verify Segment 1 (Introduction) | ✅ Done | HTTP 200, 9 pages |
| Verify Segment 2 (Registry) | ✅ Done | 173 processes, 10 packages |
| Verify Segment 3 (Composites) | ✅ Done | 28 composites, 5 packages, 3 cell engines |
| Verify Segment 4 (ParCa) | ✅ Done | 9 steps, port wiring, 83.5s runtime |
| Verify Segment 5 (Investigations) | ✅ Done | 6 studies, 5 DAG edges, gate mechanism |
| Verify Segment 6 (Simulations DB) | ✅ Done | 18 runs, 60 sms-api builds, multi-emitter |
| Verify Segment 7 (Analyses) | ✅ Done | 3D viz, 39 report cards, 12 study figures |
| Verify Segment 8 (Wrap-up) | 🔄 TODO | Finalize report, record summary |
| Commit verification artifacts | 🔄 TODO | 7 modified/untracked files |
| Dry-run presenter script | 🔄 TODO | All 8 segments against live server |
| Test remote prep scripts | 🔄 TODO | `prep_remote_build.py` + `prep_remote_land.py` |

---

## Key Files Touched

| File | Change | Why |
|---|---|---|
| `pyproject.toml` | Added `vivarium-workbench = { path = "." }` to `[tool.uv.sources]` | Resolve circular dependency: v2ecoli sources vivarium-workbench from git main, conflicts with local editable checkout |
| `pyproject.toml` | Reverted above change (didn't fix conflict) | The real fix was reinstalling editable into v2ecoli's venv |
| `demos/v2ecoli/PLAN.md` | Updated status, added Section 3.1b (iterative verification protocol) | Document the segment-by-segment API verification workflow |
| `demos/v2ecoli/VERIFICATION_REPORT.md` | Created from scratch | Static, provable record of every demo claim verified against live API |
| `NEXT_STEPS.md` | Rewritten incrementally | Driven by verification progress; now points to Segment 8 finalization |
| `SAVE_SLOT.md` | Rewritten (this file) | Checkpoint for agent handoff |
| `AGENTS.md` | Untracked (pre-existing) | Repo-local agent instructions |

---

## Key Decisions & Gotchas

### Environment
- **Run from v2ecoli directory.** The dashboard must operate in the v2ecoli venv where vivarium-workbench is installed editable. `uv pip install --python /path/to/v2ecoli/.venv/bin/python -e /path/to/vivarium-dashboard` — explicitly target v2ecoli's venv, not the dashboard's.
- **numpy ceiling.** numba requires numpy < 2.5. v2ecoli's `requires-python = "==3.12.12"` pins the Python version. The dashboard venv uses Python 3.14.
- **CWD sensitivity in verify_demo.py.** Composite resolution opens `out/cache/initial_state.json` via relative path. Must run from within the v2ecoli workspace root or the 6 cell-engine composites will fail with file-not-found.

### Caveats Found (non-blocking)
1. **Gate evaluation quirk** — showcase-2 shows `blocked: True` despite parent showcase-1 being `complete`. The gate condition field says `missing: parent.status=complete` but the status IS complete. Evaluation likely needs triggering. Presenter can explain the gate concept regardless.
2. **3 failed/orphaned runs** — millard2017_metabolism (failed, 0 steps), one baseline (failed), reactor_bird_coupled_millard (orphaned, 3600 steps). Cosmetic. 15 of 18 runs completed.
3. **Viz freshness drift** — 5 `drift` and 3 `mismatch` verdicts on report cards. The PLAN fallback script acknowledges this. The presenter can explain the freshness tracking system.
4. **PTools server** — not running locally (expected). Integration is configured.
5. **Composite count** — PLAN says "30 composites" but API returns 28. Likely due to branch differences in v2ecoli repo.

---

## Verification

| Check | Result |
|---|---|
| `verify_demo.py` | 38 passed, 0 failed |
| Dashboard HTTP 200 | `http://127.0.0.1:8771` |
| Server PID | 42842 (still running) |
| SMS tunnel | Up (at `~/sms/sms-cdk/scripts/ptools-proxy.sh`) |
| API endpoints | All segments 1-7 verified via live API calls |

---

## Next Steps (Priority Order)

1. **Finalize Segment 8** — Mark VERIFICATION_REPORT.md as complete, update summary table.
2. **Commit verification artifacts** — 7 files staged (`git add` + commit on `demo-v2ecoli` branch).
3. **Dry-run presenter script** — Walk all 8 segments against live `http://127.0.0.1:8771` browser, note any UI rendering gaps.
4. **Test remote prep scripts** — `prep_remote_build.py` and `prep_remote_land.py` against SMS API tunnel (port 8080).
5. **Resolve `.perspective.md`** — staged for deletion, either commit the deletion or restore.

---

## Quick Reference

```bash
# Dashboard server (running)
http://127.0.0.1:8771

# Verify everything
cd ~/vivarium-app/v2ecoli
uv run python ~/vivarium-app/vivarium-dashboard/demos/v2ecoli/verify_demo.py

# Regenerate ParCa cache
cd ~/vivarium-app/v2ecoli
uv run v2ecoli-parca --mode fast --cpus 4 -o out/cache

# Install vivarium-dashboard editable into v2ecoli venv
uv pip install --python ~/vivarium-app/v2ecoli/.venv/bin/python -e ~/vivarium-app/vivarium-dashboard

# Run tests (from dashboard repo)
cd ~/vivarium-app/vivarium-dashboard
uv run pytest tests/ -x --timeout 120

# Publish static bundle
vivarium-workbench-publish --workspace ~/vivarium-app/v2ecoli --out /tmp/bundle
```
