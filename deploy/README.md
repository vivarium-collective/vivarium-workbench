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

## Open items (tracked as follow-up PRs / for Alex to confirm)

- **Dockerfile** — base image, Python version, and how the demo workspace's
  package (`v2ecoli`) + deps are installed so `build_core()` imports for
  rendering. (`v2ecoli` is a path dep today — needs a build-context strategy.)
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
