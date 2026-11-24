"""Reconstructable environment fingerprint for a run's manifest.

``compute_env()`` captures the environment a run executed under — the workspace's
git HEAD (+ dirty flag + branch), key simulation packages' versions (+ a
best-effort git SHA for local checkouts), a hash of the workspace's ``uv.lock``,
the Python/platform strings, and the caller's cache fingerprint — so a rerun can
tell whether it executed under materially the same environment.

This is now a **thin delegation** to the single canonical producer,
``discovery_engine.study.run_registry.capture_environment`` / ``env_id``
(discovery-engine ≥ 0.7.6), so dashboard-launched runs and engine/earn-path runs
record one identical env shape and one env_id algorithm. The engine's package set
is framework-only (no-domain-coupling guard); the workbench adds its own domain
package(s) via ``extra_packages``. Every field remains best-effort — a failure
degrades to ``None`` rather than raising, so capture never blocks a run.
"""
from __future__ import annotations

from discovery_engine.study.run_registry import capture_environment as _capture_env
from discovery_engine.study.run_registry import env_id as _engine_env_id

# Domain/workspace packages the workbench knows about but the engine (which stays
# domain-free) does not — threaded into the canonical capture as extra packages.
_DOMAIN_PACKAGES: tuple[str, ...] = ("v2ecoli",)


def compute_env(*, ws_root=None, cache_fingerprint=None) -> dict:
    """Canonical env snapshot ``{workspace_commit, dirty, branch,
    sim_packages:{dist:{version,git_sha}}, lockfile_hash, python, platform,
    cache_fingerprint}`` — delegates to the engine's ``capture_environment`` so the
    two run-manifest writers agree. ``cache_fingerprint`` is passed through. Never
    raises.
    """
    return _capture_env(ws_root=ws_root, cache_fingerprint=cache_fingerprint,
                        extra_packages=_DOMAIN_PACKAGES)


def env_id(env: dict) -> str:
    """Stable, order-independent 16-hex digest of ``env`` — the canonical engine
    algorithm, so a run's env_id is identical whichever writer recorded it."""
    return _engine_env_id(env)
