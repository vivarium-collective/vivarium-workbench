# Deploying vivarium-workbench on EKS

Deploys the workbench as a **peer of sms-api** on the existing GovCloud EKS
cluster — same Kustomize + `TargetGroupBinding` + ghcr pattern. See
[`docs/REFACTOR-PLAN.md`](../docs/REFACTOR-PLAN.md) §2B (deployment & storage) and
§5C (demo track & rollout) for the decisions behind this.

> **Status:** the app, image, and manifests are ready. Base-path support is on
> `main`; the combined image builds and serves `/workbench` (verified in a
> container). What remains is infra wiring — the target-group ARN, the amd64
> image push, and three cluster values (see the runbook + Open items below).

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

## Deploy runbook

Prereqs: a `kubectl` context on the cluster, `docker buildx`, a ghcr login, and
`aws sso login --profile stanford-sso` for the CDK/CloudFormation steps.

**0. (pre-deploy sanity) run the image locally under the prefix**
```bash
docker run --rm -p 8090:8000 vivarium-workbench:dev \
  serve --workspace /app/v2ecoli --host 0.0.0.0 --port 8000 --base-path /workbench
# open http://localhost:8090/workbench/  → Composites → Explore renders
```

**1. Provision the ALB target group (sms-cdk, one-time)**
The `/workbench/*` + `/bigraph-loom/*` listener rules and the workbench target
group live in `../sms-cdk`. After `cdk deploy`, grab the ARN:
```bash
aws cloudformation describe-stacks --stack-name smscdk-internal-alb \
  --query "Stacks[0].Outputs"      # -> WorkbenchTargetGroupArn
```

**2. Fill in the cluster values (one-time)**
- `overlays/dev/target-group-binding.yaml` → set `targetGroupARN` to that ARN.
- `overlays/dev/kustomization.yaml` → `namespace` (and the `SMS_API_BASE`
  override patch if the sms-api namespace differs from `sms-api-stanford`).
- `overlays/dev/pvc.yaml` → `storageClassName` (the EBS `gp3` class; sms-api's
  eks overlay ships `storageclass-gp3-retain`).
- Ensure the `ghcr-secret` pull secret exists in the namespace (sealed-secret
  pattern — mirror `../sms-api/kustomize/overlays/sms-api-stanford`).

**3. Build + push the image (`linux/amd64`) and pin the tag**
```bash
deploy/build-and-push.sh          # -> ghcr.io/vivarium-collective/vivarium-workbench:<git-sha>
```
Set that tag in `overlays/dev/kustomization.yaml` under `images: … newTag:`.

**4. Apply and watch the rollout**
```bash
kubectl apply -k deploy/kustomize/overlays/dev
kubectl -n <ns> rollout status deploy/workbench
kubectl -n <ns> get targetgroupbinding,pvc,pods
```

**5. In-cluster smoke test (the Day-1 integration check)**
```bash
# workbench pod can reach sms-api in-cluster:
kubectl -n <ns> exec deploy/workbench -- \
  curl -s -o /dev/null -w '%{http_code}\n' \
  http://api.sms-api-stanford.svc.cluster.local:8000/health
```
Then, via the ALB tunnel, open `http://<tunnel-host>/workbench/`, and drive a
**remote run** end-to-end (submit → land → render) — the whole point of the
in-cluster deployment.

**Rollback:** `kubectl -n <ns> rollout undo deploy/workbench`, or repin the
previous image tag and re-apply.

> **Prod / customer demo:** create `overlays/prod` mirroring dev with its own
> image tag. Note the single-target-group caveat in
> `overlays/dev/target-group-binding.yaml` — dev and prod can't both bind the one
> workbench TG; a true split needs a second sms-cdk TG + rule.
