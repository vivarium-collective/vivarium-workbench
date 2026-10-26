"""Named environment declarations for reproducibility.

A workspace declares named *environments* in ``environments.yaml`` at its root,
in the shape of compose-api's own ``environments.yaml`` (vivarium-collective's
compose-api environment-composition plan): each environment names the common
``core`` pins (process-bigraph, bigraph-schema), its member ``packages`` (a
version specifier, or a git ``{install: git, commit: <sha>}`` source), and
optional ``native``/``python``/``notes``. A composite or study condition
references one **by name** (``environment_ref:``), and a run captures the actual
environment it executed in (:func:`env_fingerprint.compute_env`). :func:`verify`
then reports whether the capture satisfies the declaration, so an unreproducible
run is surfaced rather than shipped silently.

Separation of concern (why this exists):
    The *science* is the study/composite (``study.yaml``, the generator). The
    *runtime* is the environment it must execute in. Declaring the runtime makes
    reproducibility a property of the record — a run names the environment it is
    meant to run in, and the captured fingerprint is checked against it — instead
    of a property of whatever ``.venv`` happens to be assembled on the machine.

Every function is best-effort where a failure must not block a run from being
recorded (loading tolerates an absent file), but a *malformed* or *missing-named*
declaration raises: a user who declared an environment believes the run is
pinned to it, and dropping the pin quietly is the silent-failure class this
codebase keeps retiring (cf. ``study_spec.condition_environment``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

ENVIRONMENTS_FILE = "environments.yaml"


# ─── loading ─────────────────────────────────────────────────────────────────

def load_environments(ws_root) -> dict[str, dict]:
    """Return ``{name: environment-spec}`` from ``<ws_root>/environments.yaml``.

    An absent file yields ``{}`` (a workspace that declares no environments is
    valid — today's behavior for every existing workspace). Each returned spec
    carries its own ``name``. A present-but-malformed file raises ``ValueError``.
    """
    if ws_root is None:
        return {}
    path = Path(str(ws_root)) / ENVIRONMENTS_FILE
    if not path.is_file():
        return {}

    import yaml
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:  # malformed YAML is a real error, not empty
        raise ValueError(f"{ENVIRONMENTS_FILE}: invalid YAML: {e}") from e
    if not isinstance(doc, dict):
        raise ValueError(f"{ENVIRONMENTS_FILE}: top level must be a mapping")

    raw = doc.get("environments") or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{ENVIRONMENTS_FILE}: 'environments' must be a mapping of "
            f"name -> environment, got {type(raw).__name__}"
        )

    out: dict[str, dict] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ValueError(
                f"{ENVIRONMENTS_FILE}: environment {name!r} must be a mapping, "
                f"got {type(spec).__name__}"
            )
        env = dict(spec)
        env["name"] = str(name)
        out[str(name)] = env
    return out


def default_environment_name(ws_root) -> Optional[str]:
    """The workspace's ``default:`` environment name, or ``None``."""
    if ws_root is None:
        return None
    path = Path(str(ws_root)) / ENVIRONMENTS_FILE
    if not path.is_file():
        return None
    import yaml
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    name = doc.get("default") if isinstance(doc, dict) else None
    return str(name) if isinstance(name, str) and name.strip() else None


def resolve_environment(ws_root, *, environment_ref: Optional[str] = None) -> Optional[dict]:
    """Resolve the environment a composite/condition declares, or ``None``.

    ``environment_ref`` names an entry in ``environments.yaml``. When it is
    ``None``, the workspace's ``default:`` environment is used if one is set.
    Returns the resolved spec (with ``name``), or ``None`` when nothing is
    declared and there is no default. A named-but-unknown ``environment_ref``
    raises ``ValueError`` (a declared pin that resolves to nothing is a silent
    failure, not a no-op).
    """
    envs = load_environments(ws_root)
    name = environment_ref or default_environment_name(ws_root)
    if not name:
        return None
    if name not in envs:
        known = ", ".join(sorted(envs)) or "(none declared)"
        raise ValueError(
            f"environment {name!r} is not declared in {ENVIRONMENTS_FILE}; "
            f"known environments: {known}"
        )
    return envs[name]


# ─── verification ──────────────────────────────────────────────────────────

def _declared_pins(declared: dict) -> dict[str, Any]:
    """Flatten an environment's ``core`` + ``packages`` into ``{name: spec}``.

    ``core`` pins win over ``packages`` on a name collision (the common core is
    the authoritative floor).
    """
    pins: dict[str, Any] = {}
    for key in ("packages", "core"):
        section = declared.get(key) or {}
        if isinstance(section, dict):
            for pkg, spec in section.items():
                pins[str(pkg)] = spec
    return pins


def _satisfies(spec: Any, version: Optional[str]) -> bool:
    """Does an installed ``version`` satisfy a declared version ``spec``?

    Uses ``packaging.specifiers`` when the spec is a PEP 440 specifier
    (``>=1.10.1``, ``==7.35.0``, …); falls back to exact-string equality when
    packaging can't parse it. A bare version (no operator) is treated as ``==``.
    """
    if version is None:
        return False
    text = str(spec).strip()
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
        if text and text[0] not in "=<>!~":
            text = "==" + text
        return Version(str(version)) in SpecifierSet(text)
    except Exception:  # noqa: BLE001 — unparseable spec: fall back to exact match
        return str(version) == str(spec).lstrip("=").strip()


def verify(declared: Optional[dict], captured: Optional[dict]) -> dict:
    """Compare a declared environment against a captured fingerprint.

    Returns ``{"matches": bool, "environment": <name|None>, "drift": [...]}``
    where each drift entry is ``{package, declared, captured, reason}``. A
    declared package absent from the capture, a version that violates the
    declared specifier, or a git-pinned package whose captured ``git_sha``
    differs from the declared ``commit`` each produce a drift entry. ``matches``
    is ``True`` iff ``drift`` is empty.

    No declaration (``declared is None``) is not drift — it is a run that
    declared nothing; ``matches`` is ``True`` and ``drift`` empty.
    """
    if not declared:
        return {"matches": True, "environment": None, "drift": []}

    sim = (captured or {}).get("sim_packages") or {}
    drift: list[dict] = []

    for pkg, spec in _declared_pins(declared).items():
        info = sim.get(pkg) or sim.get(pkg.replace("-", "_")) or sim.get(pkg.replace("_", "-"))
        got_version = info.get("version") if isinstance(info, dict) else None
        got_sha = info.get("git_sha") if isinstance(info, dict) else None

        if isinstance(spec, dict) and spec.get("install") == "git":
            want = spec.get("commit")
            if got_sha is None:
                drift.append({"package": pkg, "declared": want, "captured": None,
                              "reason": "git source not captured"})
            elif want and got_sha != want:
                drift.append({"package": pkg, "declared": want, "captured": got_sha,
                              "reason": "git commit differs"})
            continue

        if got_version is None:
            drift.append({"package": pkg, "declared": spec, "captured": None,
                          "reason": "package not captured"})
        elif not _satisfies(spec, got_version):
            drift.append({"package": pkg, "declared": spec, "captured": got_version,
                          "reason": "version does not satisfy declared spec"})

    return {"matches": not drift, "environment": declared.get("name"), "drift": drift}
