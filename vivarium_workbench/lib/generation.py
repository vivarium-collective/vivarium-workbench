"""Coordinated *generation* model — provenance for expert-facing results.

v2ecoli expert-feedback round 1 (2026-05-21): an exported investigation
report glued together study panels rendered on different days against
different code+param states. The reviewer saw results "before implementing
new parameters" sitting next to newer ones, with nothing flagging the mix.
The root cause: there is no *atomic result generation*. ``runs.db`` just
accumulates runs over time and each viz HTML carries its own independent
timestamp.

A **generation** fixes that: it is one coherent snapshot of an investigation's
results — every study run against ONE ``(git_sha, param_set_hash,
composite_versions)`` state, stamped with a shared ``generation_id``. Every
run row, every rendered panel, and the report header all carry that id, so:

- the report shows the generation stamp once, prominently, and
- any panel whose run belongs to an *older* generation is flagged stale
  instead of silently mixed in.

This module is the **shared core**: both the live dashboard (which stamps
runs as it executes them) and the report renderer (which flags stale panels)
import it, so live and exported views agree on what "current" means.

On-disk shape (runtime state under ``.pbg/``; the durable archive is the
exported HTML, which bakes the generation info in):

    <ws>/.pbg/generations/<generation_id>.json   # one manifest per generation
    <ws>/.pbg/generations/current                # text: the current id

Manifest JSON::

    {
      "generation_id": "gen-20260521T084700Z-a1b2c3",
      "created_at": "2026-05-21T08:47:00.123456+00:00",
      "git_sha": "d146458",
      "param_set_hash": "9f1c2e7a4b0d",
      "composite_versions": {"v2ecoli.composites.baseline.baseline": "…"},
      "label": "round-1 rerun at cell-cycle length",
      "runs": [{"study": "dnaa-00", "run_id": "…", "sim_name": "baseline"}]
    }
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from discovery_engine.workspace_paths import WorkspacePaths


_GEN_ID_RE = re.compile(r"^gen-\d{8}T\d{6}Z-[0-9a-f]{6}$")


@dataclass
class Generation:
    """One coordinated result-generation manifest."""

    generation_id: str
    created_at: str
    git_sha: str | None = None
    param_set_hash: str | None = None
    composite_versions: dict[str, str] = field(default_factory=dict)
    label: str | None = None
    runs: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Generation":
        return cls(
            generation_id=d["generation_id"],
            created_at=d.get("created_at", ""),
            git_sha=d.get("git_sha"),
            param_set_hash=d.get("param_set_hash"),
            composite_versions=dict(d.get("composite_versions") or {}),
            label=d.get("label"),
            runs=list(d.get("runs") or []),
        )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def generations_dir(ws_root: Path | str) -> Path:
    return WorkspacePaths.load(ws_root).pbg / "generations"


def _manifest_path(ws_root: Path | str, generation_id: str) -> Path:
    return generations_dir(ws_root) / f"{generation_id}.json"


def _current_pointer(ws_root: Path | str) -> Path:
    return generations_dir(ws_root) / "current"


# ---------------------------------------------------------------------------
# IDs + hashing
# ---------------------------------------------------------------------------

def new_generation_id(now: datetime | None = None) -> str:
    """A sortable, unique generation id: ``gen-<utc-compact>-<6hex>``."""
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"gen-{stamp}-{secrets.token_hex(3)}"


def is_generation_id(value: str) -> bool:
    return bool(_GEN_ID_RE.match(value or ""))


def compute_param_set_hash(param_set: Any) -> str:
    """Stable short hash of a parameter set, regardless of how it's passed.

    Accepts a dict (hashed as canonical sorted JSON), a ``str``/``bytes``
    blob (hashed raw — e.g. the text of a params yaml), or a ``Path`` to a
    file (its bytes are hashed). Returns the first 12 hex chars of sha256.

    The hash lets the param-enforcement gate (a later thread) assert that
    the params a generation *declares* match the params actually applied.
    """
    if isinstance(param_set, Path):
        data = param_set.read_bytes()
    elif isinstance(param_set, bytes):
        data = param_set
    elif isinstance(param_set, str):
        data = param_set.encode("utf-8")
    else:
        # dict / list / other JSON-able — canonicalize so key order doesn't
        # change the hash.
        data = json.dumps(param_set, sort_keys=True,
                          separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:12]


def git_sha(ws_root: Path | str) -> str | None:
    """Short git sha of ``ws_root``'s HEAD, or None if not a repo / no git."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ws_root), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = (r.stdout or "").strip()
    return sha or None


# ---------------------------------------------------------------------------
# Manifest read / write
# ---------------------------------------------------------------------------

def _write_manifest(ws_root: Path | str, gen: Generation) -> Path:
    gens = generations_dir(ws_root)
    gens.mkdir(parents=True, exist_ok=True)
    path = _manifest_path(ws_root, gen.generation_id)
    path.write_text(json.dumps(gen.to_dict(), indent=2), encoding="utf-8")
    return path


def read_generation(ws_root: Path | str, generation_id: str) -> Generation | None:
    path = _manifest_path(ws_root, generation_id)
    if not path.is_file():
        return None
    try:
        return Generation.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def current_generation_id(ws_root: Path | str) -> str | None:
    """The id the current-pointer names, or None if no generation exists."""
    ptr = _current_pointer(ws_root)
    if not ptr.is_file():
        return None
    try:
        value = ptr.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def current_generation(ws_root: Path | str) -> Generation | None:
    gid = current_generation_id(ws_root)
    return read_generation(ws_root, gid) if gid else None


def set_current_generation(ws_root: Path | str, generation_id: str) -> None:
    gens = generations_dir(ws_root)
    gens.mkdir(parents=True, exist_ok=True)
    _current_pointer(ws_root).write_text(generation_id + "\n", encoding="utf-8")


def list_generations(ws_root: Path | str) -> list[Generation]:
    """All generations, newest first (ids sort chronologically)."""
    gens = generations_dir(ws_root)
    if not gens.is_dir():
        return []
    out: list[Generation] = []
    for path in gens.glob("gen-*.json"):
        g = read_generation(ws_root, path.stem)
        if g is not None:
            out.append(g)
    out.sort(key=lambda g: g.generation_id, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def start_generation(
    ws_root: Path | str,
    *,
    git_sha_value: str | None = None,
    param_set: Any = None,
    composite_versions: dict[str, str] | None = None,
    label: str | None = None,
    make_current: bool = True,
    now: datetime | None = None,
) -> Generation:
    """Create a new generation manifest and (by default) make it current.

    The driver (e.g. ``scripts/prepare_investigation.py``) calls this once
    before running any study, then runs every study's baseline + variants;
    the dashboard stamps each resulting run with the current generation id.

    ``git_sha_value`` defaults to ``git_sha(ws_root)`` when omitted.
    ``param_set`` is hashed via :func:`compute_param_set_hash` (pass the
    enforced params dict, the params-yaml text, or a Path to it).
    """
    now = now or datetime.now(timezone.utc)
    gen = Generation(
        generation_id=new_generation_id(now),
        created_at=now.astimezone(timezone.utc).isoformat(),
        git_sha=git_sha_value if git_sha_value is not None else git_sha(ws_root),
        param_set_hash=(compute_param_set_hash(param_set)
                        if param_set is not None else None),
        composite_versions=dict(composite_versions or {}),
        label=label,
    )
    _write_manifest(ws_root, gen)
    if make_current:
        set_current_generation(ws_root, gen.generation_id)
    return gen


def record_run(
    ws_root: Path | str,
    generation_id: str,
    *,
    study: str,
    run_id: str,
    sim_name: str | None = None,
) -> Generation | None:
    """Append a run to a generation's manifest. Idempotent on ``run_id``.

    Returns the updated Generation, or None if the manifest is missing.
    """
    gen = read_generation(ws_root, generation_id)
    if gen is None:
        return None
    if not any(r.get("run_id") == run_id for r in gen.runs):
        gen.runs.append({"study": study, "run_id": run_id, "sim_name": sim_name})
        _write_manifest(ws_root, gen)
    return gen


def is_stale(run_generation_id: str | None, current_generation_id: str | None) -> bool:
    """True if a run's generation is not the current one.

    A run with no generation id is stale once any generation exists (it
    predates the coordinated-generation model). When there is no current
    generation at all, nothing is stale — the concept doesn't apply yet.
    """
    if current_generation_id is None:
        return False
    return run_generation_id != current_generation_id
