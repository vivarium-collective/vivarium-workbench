# vivarium-workbench — container image

**This repo owns the workbench container image.** The Kubernetes **deployment
lives in [`viva-api/kustomize`](https://github.com/vivarium-collective/viva-api)**
(the renamed `sms-api` repo), where the workbench is deployed as a peer service
of viva-api — one deploy of viva-api brings the workbench up too. Keeping a
single authoritative home for the manifests (in viva-api) avoids "which
kustomize is real?" confusion.

See [`docs/REFACTOR-PLAN.md`](../docs/REFACTOR-PLAN.md) §2B (deployment & storage)
and §5C (demo track & rollout) for the decisions behind this.

## Image (approach A — combined)

The workbench must import the workspace's package (`pbg_v2ecoli`, via
`build_core()`) **in-process** to render, so it needs the *same* environment
v2ecoli runs in. The [`Dockerfile`](../Dockerfile) therefore **mirrors
v2ecoli's Dockerfile** — clones v2ecoli, `uv sync`s its locked env *including*
the workbench's deps, then overlays this repo's workbench (`uv pip install
--no-deps .`) and serves. It then installs the **`pbg-ptools`** viewer plugin
explicitly (also `--no-deps`) — the workbench discovers it via the `pbg-*` scan to
render the Pathway Tools Omics Viewer; because the workbench itself is installed
`--no-deps`, nothing transitive is pulled, so the plugin must be added by hand.
The sim-runtime layers (upstream vEcoli/Cython, AWS CLI, Ray-on-Batch entrypoint)
are omitted — the workbench renders, it doesn't run sims. The v2ecoli workspace is
baked in at `/app/v2ecoli` (used to seed the deployment's workspace volume).

> A **v2ecoli sidecar** was considered and rejected for now: the workbench imports
> `pbg_v2ecoli` in-process, which can't cross a container boundary. A separate
> environment container is the right shape *later*, once the `EnvironmentResolver`
> port lets the workbench resolve the env over a boundary instead of importing it.

**The viva-api (SMS) backend plugin.** Since step P4b (docs/backend-adapters.md) the
plugin is its own distribution, `vivarium-workbench-vivaapi`, built from this repo's
uv workspace member `packages/vivarium-workbench-vivaapi/`. The image carries it:
`uv sync --frozen --no-dev --extra vivaapi` installs it from the **same commit and
`uv.lock`** as the core (not from PyPI), the build-time sanity check fails unless
exactly the `sms` provider loads at the core's own version, and `/app/BUILD_INFO.json`
records it under `"plugins"`.

## Build + push

```bash
# linux/amd64 (the EKS nodes are x86_64) -> ghcr.io/vivarium-collective/vivarium-workbench:<git-sha>
deploy/build-and-push.sh [version] [org]
```
Then pin that tag in sms-api's stanford overlay (`images:` → `newTag`) and deploy
sms-api. (Requires `docker buildx` + a ghcr login.)

Every image carries its own provenance: OCI labels (`docker inspect`/`crane
config`) and `/app/BUILD_INFO.json` (`kubectl exec ... -- cat
/app/BUILD_INFO.json`) both record the exact commit it was built from — see
`Dockerfile`'s own "image provenance" section and #1114. A semver-shaped
`version` (`X.Y.Z`) is refused outright unless the tree is clean, `main` is
checked out, and `v<version>` already points at HEAD — i.e. unless it could
only have come from `bump-and-release.sh` below. A non-semver `version` (the
default short git sha, or any other ad-hoc tag) is unrestricted, same as
always.

### Cutting a real, semver-tagged release

```bash
deploy/bump-and-release.sh <version> [org]   # e.g. 0.3.86
```
Bumps `pyproject.toml`, commits, creates an annotated `v<version>` tag, pushes
both, and dispatches this same build against `main` — refusing first unless the
tree is clean, `main` is checked out and in sync with `origin/main`, `<version>`
is a real semver increase over the current `pyproject.toml`, and neither the
tag nor the ghcr image already exist. `build-and-push.yml`'s own CI gate
enforces the same rules server-side for any semver `version` dispatched by
hand — a bare short-sha dispatch (the default, `version` left blank) is
unaffected.

## Deployment (lives in sms-api/kustomize)

The workbench runs as a Deployment+Service in the sms-api EKS cluster, under the
`/workbench` ALB path prefix (`serve --base-path /workbench`), with its **own EBS
`gp3` PVC** (RWO, single replica) seeded once from the baked-in workspace by an
initContainer. What the manifests need:

- **One ARN** — the `WorkbenchTargetGroupArn` (an sms-cdk stack output) for the
  `TargetGroupBinding`. The sms-cdk `/workbench/*` + `/bigraph-loom/*` listener
  rules route to that target group.
- **No new secrets** — reuse the org `ghcr-secret`; **no** Postgres, IRSA, or AWS
  creds (the workbench delegates all compute/storage to sms-api over in-cluster
  HTTP), so no IAM role annotation either.
- **`SMS_API_BASE`** → the in-cluster sms-api service DNS
  (`http://api.<sms-api-ns>.svc.cluster.local:8000`) — the only backend.

The workbench `Deployment`/`Service` (in `kustomize/base`) and the overlay
additions (EBS PVC + `TargetGroupBinding` + pinned image tag) are maintained in
the **sms-api** repo.
