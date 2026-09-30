"""Per-comparison two-repo build pinning — dual-engine W4, workbench side.

Spec: ``docs/dual-engine-comparison.md`` §5.6 (Q4) / §6 W4. The single global
``VIVARIUM_WORKBENCH_REMOTE_REPO_URL`` structurally assumes ONE repo per
deployment — the #240 failure mode (every session silently dispatched against
the wrong repo for ~a month). A dual-engine comparison pins **two** repos, so
its resolution must be **per-declared-environment**, not per-deployment:

* :func:`resolve_environment_build` — resolve a W1 ``environment: {repo, ref}``
  declaration (``study_spec.condition_environment``) to a registered build.
  ``ref`` may be a **commit** (full or short sha — preferred; the spec pins
  commits, never branches) or a **branch name**; commit match wins.
* :func:`resolve_comparison_pair` — the W4 discipline in one call:
  **resolve BOTH engines, then VERIFY both builds are complete, before the
  caller submits EITHER** — so engine A never runs while engine B's build turns
  out broken/pending (viva-api's ``/simulator/versions`` happily lists failed
  builds; its own ``_verify_build_complete`` fires only per-dispatch, after
  you've resolved). Returns role-tagged entries shaped for the run manifest's
  ``environments`` list, with ``simulator_id`` + resolved ``commit`` filled —
  "record both simulator_ids as data on the comparison, resolved once at
  submit" (the answers doc's recommendation; the late-resolution alternative is
  the ``V2ECOLI_BATCH_BASELINE_COMPOSITE_ID`` saga).

The deployment-wide pin (``remote_pinned.pinned_config``) remains the default
for ordinary single-engine runs; nothing here changes that path.
"""

from __future__ import annotations

import re

from vivarium_workbench.lib.remote_pinned import NoPinnedBuildError, _normalize_repo


def _repo_key(url_or_slug: str) -> str:
    """Comparable repo key bridging the two spellings in play: a W1
    declaration's ``org/repo`` shorthand and sms-api's registered full URL
    (``https://github.com/org/repo[.git]``). Lowercase, no ``.git``, no scheme/
    host — just ``org/repo``."""
    u = _normalize_repo(url_or_slug)
    if "://" in u:
        u = u.split("://", 1)[1]          # drop scheme
        u = u.split("/", 1)[1] if "/" in u else u   # drop host
    parts = [s for s in u.split("/") if s]
    return "/".join(parts[-2:]) if len(parts) >= 2 else u
from vivarium_workbench.lib.sms_api_client import SmsApiClient

# Build-status vocabulary shared with the remote-run status panel
# (lib/remote_run_views.py) — one source of truth for "what counts as built".
# Only the OK set matters here: failed, still-building, and unknown all mean
# "do not dispatch the pair yet".
from vivarium_workbench.lib.remote_run_views import _TERMINAL_OK

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


class BuildNotReadyError(RuntimeError):
    """A resolved build exists but is not in a completed state.

    Carries ``role`` (``candidate``/``reference``) and the raw ``status`` so the
    caller's error names WHICH engine blocked the comparison.
    """

    def __init__(self, role: str, simulator_id: int, status: str) -> None:
        super().__init__(
            f"{role} build (simulator {simulator_id}) is not ready: "
            f"status={status!r} — comparison dispatch refused before submitting "
            "either engine"
        )
        self.role = role
        self.simulator_id = simulator_id
        self.status = status


def _looks_like_sha(ref: str) -> bool:
    return bool(_SHA_RE.match(ref.strip().lower()))


def resolve_environment_build(client: SmsApiClient, environment: dict) -> dict:
    """Resolve a declared ``{repo, ref}`` environment to a registered build.

    ``ref`` semantics: a hex string of 7–40 chars is treated as a **commit**
    (matched as a prefix of ``git_commit_hash``, case-insensitive) — the
    preferred, reproducible form. Anything else is a **branch name** (newest
    matching build wins, mirroring ``remote_pinned.resolve_pinned_build``).

    Returns ``{"simulator_id", "commit", "ref", "repo_url"}``. Raises
    :class:`remote_pinned.NoPinnedBuildError` when nothing matches — with the
    repo@ref named, since a comparison error must say which engine failed.
    """
    repo = str(environment.get("repo") or "").strip()
    ref = str(environment.get("ref") or "").strip()
    if not repo or not ref:
        raise ValueError(f"environment requires repo and ref, got {environment!r}")

    want_repo = _repo_key(repo)
    # A branch ref asks for that branch's builds (list_branch_builds: the
    # server's ?repo_url=&branch= filter where advertised); a SHA ref matches on
    # repo + commit prefix over the whole listing, which the environments carry.
    listing = (
        client.list_simulators() if _looks_like_sha(ref) else client.list_branch_builds(repo, ref)
    )
    versions = (listing or {}).get("versions") or []
    in_repo = [
        v for v in versions
        if _repo_key(v.get("git_repo_url", "")) == want_repo
        and v.get("database_id") is not None
    ]
    if _looks_like_sha(ref):
        want = ref.lower()
        matches = [
            v for v in in_repo
            if str(v.get("git_commit_hash") or "").lower().startswith(want)
        ]
    else:
        matches = [v for v in in_repo if (v.get("git_branch") or "") == ref]
    if not matches:
        raise NoPinnedBuildError(
            f"no registered build for {repo}@{ref} — register/build it first"
        )
    latest = max(
        matches,
        key=lambda v: (str(v.get("created_at") or ""), int(v.get("database_id", 0))),
    )
    return {
        "simulator_id": int(latest["database_id"]),
        "commit": str(latest.get("git_commit_hash") or ""),
        "ref": ref,
        "repo_url": repo,
    }


def verify_build_ready(client: SmsApiClient, resolved: dict, role: str) -> None:
    """Raise :class:`BuildNotReadyError` unless the build's status is terminal-OK.

    "Registered ≠ built": ``/simulator/versions`` lists builds whose image build
    failed or is still running. A comparison must check this for BOTH engines
    before dispatching EITHER.
    """
    status = str(
        (client.simulator_status(int(resolved["simulator_id"])) or {}).get("status", "")
    ).lower()
    if status in _TERMINAL_OK:
        return
    raise BuildNotReadyError(role, int(resolved["simulator_id"]), status)


def resolve_comparison_pair(
    client: SmsApiClient, candidate_env: dict, reference_env: dict
) -> dict:
    """Resolve + verify BOTH engines of a comparison; return the recorded pair.

    Order matters and is deliberate:
      1. resolve candidate, resolve reference — so a *resolution* error for
         either engine surfaces before any status round-trips;
      2. verify candidate, verify reference — so a not-ready build for either
         engine surfaces **before the caller submits anything**.

    Returns ``{"candidate": entry, "reference": entry}`` where each entry is
    shaped for ``build_run_manifest``'s ``environments`` list::

        {"role", "repo", "ref", "commit", "simulator_id"}

    The caller records these on the comparison **as data** (they also thread
    into each run's ``declared_environment`` so the manifest's ``declared``
    entry carries the resolved commit + simulator_id, not nulls).
    """
    resolved_c = resolve_environment_build(client, candidate_env)
    resolved_r = resolve_environment_build(client, reference_env)
    verify_build_ready(client, resolved_c, "candidate")
    verify_build_ready(client, resolved_r, "reference")
    return {
        "candidate": {
            "role": "candidate",
            "repo": resolved_c["repo_url"],
            "ref": resolved_c["ref"],
            "commit": resolved_c["commit"],
            "simulator_id": resolved_c["simulator_id"],
        },
        "reference": {
            "role": "reference",
            "repo": resolved_r["repo_url"],
            "ref": resolved_r["ref"],
            "commit": resolved_r["commit"],
            "simulator_id": resolved_r["simulator_id"],
        },
    }
