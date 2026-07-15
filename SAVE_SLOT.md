# Checkpoint — 2026-07-14 (night): smscdk pre-flight verified + WALKTHROUGH stack-fix on `feat/improved-visual-feedback`

## ⭐ RESUME HERE

**Branch policy (user, explicit, unchanged):** ALL work stays on
**`feat/improved-visual-feedback`** and ships in **PR #467** — NOT `demo-v2ecoli`.
This branch will later be merged + released like `demo-v2ecoli` (PR review → merge
to `main` → version bump/release → overlay `newTag` repoint).

**Primary goal (unchanged):** record the 8-segment remote GovCloud v2ecoli demo
(`demos/v2ecoli/WALKTHROUGH.md`). This session's pre-flight checks all PASS — the
demo is clear to record (one documented PTools caveat, below).

## Session goal

Do orientation items 1 + 2: fix stale WALKTHROUGH refs, and verify the pinned-build
latest-main gate on the **smscdk** stack. Both done. Plus captured a durable
`stanford` vs `stanford test` stack distinction the user supplied.

## Progress table

| Item | Status |
|---|---|
| **WALKTHROUGH pre-flight retargeted to smscdk** — `stanford` (no arg), `sms-proxy -s smscdk`, `sms-api-stanford` namespace, `kube_stanford.yml`, `deployment: smscdk` provenance, `smscdk-ray-mnp` queue | ✅ Done (staged, NOT committed) |
| Old `prep_remote_build.py` Appendix-G refs | ✅ Already gone (landed in `d3a30c8`); grep-confirmed absent |
| **Pinned-build gate on smscdk** (`ensure_latest_main_build.sh`) | ✅ MATCH ✓, exit 0 — built == live main `a08e20b` |
| **`/etc/hosts` state** | ✅ Restored + `uchg`-locked (user did it; mtime 20:13); earlier refusal was a STALE DNS CACHE, not the file |
| `localhost:8080/workbench` reachable | ✅ 200 (cache self-corrected; v4+v6 both resolve now) |
| Durable memory: `stanford` vs `stanford test` | ✅ Saved (`project_stanford_zshrc_commands.md`) |
| Durable memory: Cisco `/etc/hosts` partial-truncation + stale-cache nuance | ✅ Appended to `project_cisco_empties_etc_hosts.md` |
| Plan-7 progress UX (PR #467) | ✅ CODE-COMPLETE + live-verified; PR OPEN, `MERGEABLE`, `REVIEW_REQUIRED` |
| **Commit the staged WALKTHROUGH.md** | ❌ PENDING (user runs the one-liner — agent does not commit) |
| **Record the demo** | ❌ PENDING (pre-flight now clean; next focus) |
| PR #467 review → merge → release | ❌ PENDING |

## Key files touched (this session)

- **EDITED (staged, uncommitted)** `demos/v2ecoli/WALKTHROUGH.md` — the demo
  retargeted to smscdk earlier (`d3a30c8`) but the OPERATIONAL pre-flight commands
  still said `stanford test` / `-s smsvpctest` / `sms-api-stanford-test`, which
  would drop the operator on the WRONG (test) stack. Fixed every executable
  command + namespace + tunnel invocation + the `deployment:` provenance value +
  the Batch queue name to smscdk. **Deliberately LEFT** the DECISION-note
  `smsvpctest` *contrast* (lines ~6-16) and the new `stanford test → smsvpctest`
  explainer comment in §0.1 intact — those are correct as-is.
- **Memory (outside repo)** `~/.claude/projects/.../memory/`:
  - NEW `project_stanford_zshrc_commands.md` + MEMORY.md pointer.
  - UPDATED `project_cisco_empties_etc_hosts.md` (partial-truncation IPv6 variant +
    stale-cache false-alarm caveat).

## Key design decisions / gotchas

- **`stanford` ≠ `stanford test`** (user, durable): `stanford` (no arg) → **smscdk**
  stack · namespace `sms-api-stanford` · `~/.kube/kube_stanford.yml`. `stanford test`
  → **smsvpctest** · `sms-api-stanford-test` · `kube_stanford_test.yml`. The demo
  is smscdk, so use the UNPARAMETERIZED `stanford`. `[[project_stanford_zshrc_commands]]`
- **The `/etc/hosts` refusal was a false alarm.** File was already restored+locked.
  The symptom (`localhost:8080` refused, `127.0.0.1:8080` worked, `dscacheutil`
  ipv6-only) was a **stale mDNSResponder cache** that self-cleared. ALWAYS
  `cat /etc/hosts` + `ls -lO` first; if the `127.0.0.1 localhost` line is present
  and `uchg` is set, do NOT re-restore — flush cache or wait. `[[project_cisco_empties_etc_hosts]]`
- **Per-stack simulator registries.** smscdk has its own; the pinned build there is
  `a08e20b` (== live v2ecoli main). Gate script closes the newest-BUILT-≠-live-tip
  drift. `[[project_demo_latest_v2ecoli_main_constraint]]`
- **Segment-7 caveat stands:** PTools Omics-Viewer **Launch** does NOT auto-paint
  on deployed `sms-ptools:0.5.9` (scheme mismatch → plan 9). Demo with the caveat
  or skip the Launch; interactive figures + omics-TSV delivery DO work.
  `[[project_ptools_segment7_routing]]`

## Verification

- `git ls-remote …/v2ecoli main` → `a08e20b…`; smscdk `/core/v1/simulator/versions`
  newest v2ecoli@main == `a08e20b` → gate **MATCH ✓ exit 0**.
- `curl localhost:8080/workbench` → **200**; `/core/v1/simulator/versions` → 200.
- `cat /etc/hosts` shows `127.0.0.1 localhost`; `ls -lO` shows `uchg`; mtime 20:13
  (holding, not re-truncated).
- Full `pytest` NOT re-run — **no Python/JS source changed** this session (docs +
  memory only). Plan-7 JS/pytest were green earlier this session. Pre-existing
  non-regression failures still stand (10 legacy `test_study_detail_page`, 1
  remote-run-panel, broken `test_chain_block.js`).

## Next steps (priority order)

1. **Commit the staged WALKTHROUGH.md** — user runs (agent staged it already;
   `[[feedback_suggest_commits]]`):
   ```
   git commit -m "docs(demo): fix pre-flight to target smscdk (stanford, -s smscdk, sms-api-stanford)"
   ```
   (Do NOT `git add` CLAUDE.md/AGENTS.md/Makefile/todo.md/.pr-body-*.md.)
2. **Record the demo** on smscdk — pre-flight is clean: tunnel up, `localhost:8080`
   OK, gate MATCH ✓. Walk `demos/v2ecoli/WALKTHROUGH.md` Segments 1–8; Segment 6
   Part B now shows the plan-7 progress bar (only if serving THIS branch — the
   deployed pod does NOT carry plan-7 yet; use Path B or a post-merge deploy).
   Apply the Segment-7 PTools caveat.
3. **PR #467 review → merge** (no auto-merge; `[[feedback_pr_review_required]]`).
4. **Version bump + release** into `main`, then repoint the overlay `newTag` to the
   release tag and roll (same as 0.2.0 / PR #466 flow).
5. Parked: plans 8 + 9 (await "proceed"); backlog (a) pydantic-settings
   `environment.py` (untracked WIP, keep OUT of plan-7 commits).

## Quick reference

- Branch `feat/improved-visual-feedback`, **8 ahead of `origin/main`**, all pushed;
  1 staged uncommitted file (`WALKTHROUGH.md`).
- Pre-flight (smscdk): `stanford` → `~/sms/sms-cdk/scripts/sms-proxy.sh -s smscdk`
  (→ localhost:8080) → open `localhost:8080/workbench`.
- Gate (fully remote): `SMS_API_BASE=http://localhost:8080 ./demos/v2ecoli/scripts/ensure_latest_main_build.sh` (must exit 0).
  If `localhost` refuses but `127.0.0.1:8080` works → stale DNS cache; check
  `/etc/hosts` before touching it (`SMS_API_BASE=http://127.0.0.1:8080` as a bypass).
- Tests: `uv run --no-sync pytest -q` (bare `uv run` fails — `../pbg-ptools` path dep)
  + `node tests/js/test_progress_track.js`.
- Cluster env: `AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 KUBECONFIG=~/.kube/kube_stanford.yml`.
- Manual pinned check: `git ls-remote https://github.com/vivarium-collective/v2ecoli main`
  vs `curl -s localhost:8080/core/v1/simulator/versions`.

## Related memory
`[[project_stanford_zshrc_commands]]`, `[[project_cisco_empties_etc_hosts]]`,
`[[project_demo_latest_v2ecoli_main_constraint]]`, `[[project_pinned_build_remote_runs]]`,
`[[project_plan7_progress_ux_pr467]]`, `[[project_ptools_segment7_routing]]`,
`[[feedback_suggest_commits]]`, `[[feedback_pr_review_required]]`, `[[feedback_do_not_commit]]`.
