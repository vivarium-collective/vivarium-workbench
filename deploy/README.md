# Deploying vivarium-workbench on EKS

Deploys the workbench as a **peer of sms-api** on the existing GovCloud EKS
cluster — same Kustomize + `TargetGroupBinding` + ghcr pattern. See
[`docs/REFACTOR-PLAN.md`](../docs/REFACTOR-PLAN.md) §2B (deployment & storage) and
§5C (demo track & rollout) for the decisions behind this.

> **Status:** WIP, landing as a series of small PRs. This first PR scaffolds the
> directory and a first-cut Kustomize `base`. Overlays, the Dockerfile, and the
> in-cluster smoke test follow as separate PRs.

## Design (from RFC §2B)

- **Runs as another Deployment+Service in the existing EKS cluster** — not
  ECS/Fargate. Reuses sms-api's pattern verbatim (`../sms-api/kustomize`).
- **sms-api is the only cloud backend, reached in-cluster:**
  `SMS_API_BASE=http://api.<sms-ns>.svc.cluster.local:8000` — no `localhost`
  tunnel. The workbench therefore needs **no direct S3 access and no IRSA**.
- **Storage = a private EBS `gp3` PVC** (RWO) for the workspace (git/YAML/SQLite)
  + caches. Single replica → the current global-state/`os.chdir` model is fine.
  **No shared filesystem with the compute** — run outputs arrive from S3 via
  sms-api's download (the existing `remote_run_landing` path).
- **No app auth** — the AWS account / VPC / tunnel perimeter is the control
  (same posture as sms-api).

## Layout (target)

```
deploy/
├── README.md                       # this file
└── kustomize/
    ├── base/                       # Deployment + Service (env-agnostic)
    │   ├── kustomization.yaml
    │   └── deployment.yaml
    └── overlays/
        ├── dev/                    # persistent staging — refactor deploys here
        │   ├── kustomization.yaml  #   image tag, namespace, EBS PVC, SMS_API_BASE,
        │   ├── pvc.yaml            #   TargetGroupBinding, ghcr secret
        │   └── target-group-binding.yaml
        └── prod/                   # customer demo — pinned to a known-good tag
            └── … (same shape, own tag + target group)
Dockerfile                          # workbench image (+ workspace deps) → ghcr
```

## Image (approach A — combined)

The workbench must import the workspace's package (`pbg_v2ecoli`, via
`build_core()`) **in-process** to render, so it needs the *same* environment
v2ecoli runs in. The [`Dockerfile`](../Dockerfile) therefore **mirrors
v2ecoli's Dockerfile** — clones v2ecoli, `uv sync`s its locked env *including*
the workbench's deps, then overlays this repo's workbench (`uv pip install
--no-deps .`) and serves. The sim-runtime layers (upstream vEcoli/Cython, AWS
CLI, Ray-on-Batch entrypoint) are omitted — the workbench renders, it doesn't run
sims. Build: `docker build -t ghcr.io/vivarium-collective/vivarium-workbench:dev .`
(needs a first build-test to shake out uv/import specifics.)

> A **v2ecoli sidecar** was considered and rejected for now: the workbench imports
> `pbg_v2ecoli` in-process, which can't cross a container boundary. A separate
> environment container is the right shape *later*, once the `EnvironmentResolver`
> port lets the workbench resolve the env over a boundary instead of importing it.

## Open items (tracked as follow-up PRs / for Alex to confirm)

- **Cluster specifics** — namespace(s), the EBS `gp3` StorageClass name, the
  workspace pod `runAsUser`/`fsGroup` (sms-api uses `17163`/`10000`), and the
  ghcr pull secret name.
- **ALB** — a workbench **target group + listener rule** on the existing internal
  ALB (a small `sms-cdk` addition), then a `TargetGroupBinding` in each overlay.
- **Secrets** — GitHub token for git push (if the demo pushes), via the sealed
  secret pattern.

## Applying (once complete)

```bash
kubectl apply -k deploy/kustomize/overlays/dev     # persistent staging
kubectl apply -k deploy/kustomize/overlays/prod    # customer demo (pinned tag)
```
