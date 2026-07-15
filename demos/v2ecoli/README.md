# vivarium-workbench — v2ecoli GovCloud Demo

A guided demonstration of **vivarium-workbench** (the web UI for
[process-bigraph](https://github.com/vivarium-collective/process-bigraph)
workspaces) driving the **v2ecoli** whole-cell *E. coli* model. The dashboard, the
v2ecoli workspace, and the sms-api simulation backend all run **in-cluster on AWS
GovCloud**; you connect with an authenticated SSM tunnel and a browser. Nothing is
built, cloned, or served locally.

- **Demo target:** the `/workbench` deployment on the **`sms-api-stanford`**
  Kubernetes namespace — the **`smscdk`** GovCloud stack.
- **Reached at:** `http://localhost:8080/workbench` through the `sms-proxy.sh` tunnel.
- **Full presenter script:** [`WALKTHROUGH.md`](WALKTHROUGH.md) (8 segments, ~20 min).

---

## Prerequisites

1. **AWS GovCloud access** to the Stanford stacks (SSO profile `stanford-sso`,
   region `us-gov-west-1`) with permission to open SSM sessions to the batch
   submit node.
2. **AWS CLI v2** + the **Session Manager plugin** installed.
3. A clone of **`sms-cdk`** (ships the tunnel script) at `$SMS_CDK_DIR`
   (e.g. `~/sms/sms-cdk`):
   ```bash
   git clone git@github.com:vivarium-collective/sms-cdk.git ~/sms/sms-cdk
   ```

No local Python, workspace, or dashboard install is required for the remote demo.
(An offline local-serve fallback is documented in [`WALKTHROUGH.md`](WALKTHROUGH.md)
Appendix G.)

---

## Quick Start

```bash
# 1. Authenticate to GovCloud (the `stanford` ~/.zshrc function sets
#    AWS_PROFILE=stanford-sso, AWS_DEFAULT_REGION=us-gov-west-1, then `aws sso login`).
stanford test

# 2. Open the SSM tunnel (Terminal 1 — stays alive until Ctrl+C).
cd $SMS_CDK_DIR/scripts && ./sms-proxy.sh -s smscdk
#   → localhost:8080 → internal ALB:80

# 3. Ensure the pinned remote-run build tracks the LATEST v2ecoli main (Terminal 2).
#    Non-negotiable demo constraint; builds on the remote sms-api only if stale (~13 min).
cd ~/vivarium-app/vivarium-dashboard
./demos/v2ecoli/scripts/ensure_latest_main_build.sh      # must print MATCH ✓ / BUILT ✓

# 4. Open the dashboard.
open http://localhost:8080/workbench
```

The proxy banner lists every endpoint on port 8080: `/workbench` (dashboard),
`/` (PTools UI), `/docs` (sms-api Swagger).

---

## What the demo covers (8 segments)

| # | Segment | Page / Tab | The point |
|---|---------|-----------|-----------|
| 1 | Introduction | Home / rail | One UI over any process-bigraph workspace; every action is git-committed. |
| 2 | Simulator agnosticism | **Registry** | Process classes from 7 packages co-exist in one type system. |
| 3 | Engine swappability | **Composites** | Multiple cell engines share one reactor-coupler contract — drop-in replaceable. |
| 4 | ParCa modularization | **Composite Explorer** → parca | A monolithic fit step, now an inspectable multi-step graph in bigraph-loom. |
| 5 | Investigation DAG | **Investigations** | Studies with pass/fail gates encode the scientific method. |
| 6 | Simulations DB + remote run | **Simulations DB** | Local + GovCloud runs side-by-side; a live pinned run on the Ray backend (Part B). |
| 7 | Visualizations + PTools omics | **Analyses** | Registered viz classes; optional Pathway Tools omics viewer. |
| 8 | Wrap-up & Q&A | — | Extensibility recap: any simulator, any backend, unified by git provenance. |

Exact figures (process/composite/investigation/viz counts, Simulations DB size)
reflect the seeded workspace — confirm against the live deployment before quoting;
`WALKTHROUGH.md` carries the last-verified numbers.

---

## The pinned-build constraint

Segment 6 Part B runs the simulation on the remote Ray backend against a
**pre-built, pinned** v2ecoli simulator. The demo's hard rule is that this build is
**always the latest `vivarium-collective/v2ecoli` main commit**.

The pinned resolver picks the *newest built* simulator for `v2ecoli@main` on the
target sms-api — **not** the live GitHub tip — and each stack has its **own**
registry. So the build goes stale whenever v2ecoli main advances, and a build on
one stack does not exist on another. `scripts/ensure_latest_main_build.sh` closes
that gap: it compares the live main tip against the sms-api's newest built commit
and, if stale, registers + builds the current tip (fully remote — no push, no
login, no local workspace; v2ecoli is public and the sms-api endpoint takes no auth
token through the tunnel). **Run it before every recording and after any v2ecoli
main merge.** The deployed dashboard is resolve-only (`REMOTE_PINNED=1`) and cannot
build — always seed via this script, never the dashboard UI.

---

## File layout

```
demos/v2ecoli/
├── README.md                        ← this file
├── WALKTHROUGH.md                   ← 8-segment presenter script (remote-first; Appendix G = offline)
├── VERIFICATION_REPORT.md           ← last live-verification record
├── scripts/
│   └── ensure_latest_main_build.sh  ← pinned-build gate (ensure latest v2ecoli main is built)
├── speaker/                         ← speaker aids
└── .gitignore                       ← keeps generated state out of git
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/workbench` refuses / times out | Tunnel down or SSO expired | Re-run `stanford test`, then restart `sms-proxy.sh -s smscdk` |
| Tunnel hangs on `Starting session…`, then `listen tcp: lookup localhost: no such host` on Ctrl+C | `/etc/hosts` empty → `localhost` unresolvable (some corporate security agents truncate it) | Restore `/etc/hosts` (`127.0.0.1 localhost` / `::1 localhost`) in a real terminal; `sudo chflags uchg /etc/hosts` to keep it |
| "no built simulator for …v2ecoli@main" in the run card | This stack's registry isn't seeded | Run `scripts/ensure_latest_main_build.sh` (Quick Start step 3) |
| `cross-origin request forbidden` on POST | ALB rewrites `Host`; allowlist missing | Deployment needs `VIVARIUM_WORKBENCH_ALLOWED_ORIGINS=http://localhost:8080` (see WALKTHROUGH Appendix E) |
| Segment 7 PTools Omics **Launch** doesn't paint | Known `sms-ptools` scheme mismatch | Demo the interactive figures + omics-TSV delivery; skip the Launch (deferred fix) |

---

## Decoupling principle

The demo assets never modify existing v2ecoli files — all artifacts live under
`demos/v2ecoli/`, and the workspace's composites, studies, and investigations are
consumed read-only. The dashboard itself is workspace-agnostic; no dashboard code
changes are needed to demo a different workspace.
