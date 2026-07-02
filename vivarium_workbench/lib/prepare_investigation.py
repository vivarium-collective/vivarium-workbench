"""Drive an investigation's coordinated-generation prep through the dashboard.

For each study in an investigation that declares ``comparative_visualizations``,
this runs the baseline + every comparison variant those overlays reference (via
the dashboard run API), records every run into the workspace's *current*
coordinated generation (``pbg_superpowers.generation`` — the same core the run
path stamps and the report's banner reads), then renders the comparative
figures.

A coordinated *generation* is opened BEFORE the run loop so the dashboard
stamps each run with it as it executes — provenance must be current at run
time, not written after.

Moved out of the v2ecoli workspace ``scripts/`` (it has no biology/workspace
coupling) so it's maintained against the run API + ``comparative_viz`` it
depends on. Exposed as ``vivarium-dashboard prepare-investigation`` and, for
the framework, ``pbg_superpowers`` is aware of it.

Requires a running dashboard for the workspace (the run engine); the URL is
auto-detected from ``<workspace>/.pbg/dashboard/dashboard-info``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def _dashboard_url(ws: Path, override: str | None = None) -> str:
    if override:
        return override.rstrip("/")
    info = WorkspacePaths.load(ws).pbg / "dashboard" / "dashboard-info"
    if info.is_file():
        try:
            return json.loads(info.read_text(encoding="utf-8"))["url"].rstrip("/")
        except Exception:
            pass
    return "http://localhost:8765"


def _post(url: str, payload: dict, timeout: float = 1800.0) -> tuple[int, dict]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
            try:
                return r.status, json.loads(body)
            except json.JSONDecodeError:
                return r.status, {"raw": body[:200]}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def _investigations(ws: Path) -> list[str]:
    inv_root = WorkspacePaths.load(ws).investigations
    if not inv_root.is_dir():
        return []
    return sorted(
        d.name for d in inv_root.iterdir()
        if d.is_dir() and (d / "investigation.yaml").is_file())


def _study_slugs(ws: Path, inv_slug: str) -> list[str]:
    spec = yaml.safe_load((WorkspacePaths.load(ws).investigations / inv_slug
                           / "investigation.yaml").read_text(encoding="utf-8")) or {}
    out = []
    for s in (spec.get("studies") or []):
        out.append(s if isinstance(s, str) else (s.get("study") or s.get("name")))
    return [s for s in out if s]


def prepare_study(ws: Path, slug: str, dash: str, steps: int | None,
                  render_only: bool) -> dict:
    """Run baseline + comparison variants for one study, render comparatives."""
    studies_dir = WorkspacePaths.load(ws).studies
    sf = studies_dir / slug / "study.yaml"
    if not sf.is_file():
        return {"study": slug, "skipped": "no study.yaml"}
    spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
    cvs = spec.get("comparative_visualizations") or []
    if not cvs:
        return {"study": slug, "skipped": "no comparative_visualizations"}

    # Distinct sim_names the comparatives overlay. The one whose name equals
    # the study slug is the baseline; the rest are declared variants.
    sim_names: list[str] = []
    for cv in cvs:
        for r in (cv.get("runs") or []):
            sn = r.get("sim_name")
            if sn and sn not in sim_names:
                sim_names.append(sn)

    run_results = []
    if not render_only:
        for sn in sim_names:
            if sn == slug:
                payload = {"study": slug}
                if steps is not None:
                    payload["steps"] = steps
                code, body = _post(f"{dash}/api/study-run-baseline", payload)
                kind = "baseline"
            else:
                payload = {"study": slug, "variant": sn}
                if steps is not None:
                    payload["steps"] = steps
                code, body = _post(f"{dash}/api/study-run-variant", payload)
                kind = "variant"
            run_id = (body.get("simulation_id") or body.get("run_id")
                      if isinstance(body, dict) else None)
            run_results.append({"run": sn, "kind": kind, "http": code,
                                "run_id": run_id})
            print(f"  ran {sn} ({kind}): HTTP {code} run_id={run_id}", flush=True)

    from vivarium_dashboard.lib.comparative_viz import render_comparative_time_series
    study_db = studies_dir / slug / "runs.db"
    viz_dir = studies_dir / slug / "viz"
    viz_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    for cv in cvs:
        runs = [{"label": r.get("label") or r.get("sim_name"),
                 "db_path": study_db, "sim_name": r.get("sim_name")}
                for r in (cv.get("runs") or [])]
        if not runs:
            continue
        out = viz_dir / f"comparative_{cv['name']}.html"
        try:
            render_comparative_time_series(
                runs=runs,
                observable_path=cv.get("observable_path", ""),
                title=cv.get("title", cv["name"]),
                y_label=cv.get("y_label", ""),
                output_path=out,
                observable_index=cv.get("observable_index"),
                target_band=cv.get("target_band"),
                target_band_label=cv.get("target_band_label"),
            )
            rendered.append({"viz": cv["name"], "bytes": out.stat().st_size})
        except Exception as e:  # noqa: BLE001
            rendered.append({"viz": cv["name"], "error": str(e)})
        print(f"  rendered {cv['name']}: "
              f"{rendered[-1].get('bytes', rendered[-1].get('error'))}", flush=True)

    return {"study": slug, "runs": run_results, "rendered": rendered}


def prepare_investigation(workspace: Path | str, *,
                          investigation: str | None = None,
                          study: str | None = None,
                          steps: int | None = None,
                          render_only: bool = False,
                          dashboard_url: str | None = None,
                          param_set: Path | str | None = None) -> dict:
    """Prepare an investigation's coordinated generation. Returns a summary dict.

    ``param_set``: optional path to a params file hashed into the generation's
    ``param_set_hash`` (provenance — records which param snapshot this used).
    ``study``: prepare only this study (reuses the current generation).
    ``render_only``: skip sims; just re-render comparatives.
    """
    from pbg_superpowers import generation as _gen

    ws = Path(workspace)
    inv = investigation
    if inv is None:
        invs = _investigations(ws)
        if len(invs) != 1:
            raise SystemExit(
                f"specify --investigation (found {invs} in {ws}/investigations)")
        inv = invs[0]

    dash = _dashboard_url(ws, dashboard_url)
    studies = [study] if study else _study_slugs(ws, inv)
    ps = Path(param_set) if param_set else None

    # Open (or reuse) the coordinated generation BEFORE running anything.
    if render_only:
        gen = _gen.current_generation(ws)
    elif study:
        gen = _gen.current_generation(ws) or _gen.start_generation(
            ws, param_set=ps, label=f"{inv} (partial: {study})")
    else:
        gen = _gen.start_generation(ws, param_set=ps, label=f"{inv} full prep")

    generation_id = gen.generation_id if gen else None
    print(f"Preparing investigation {inv!r} via {dash}")
    print(f"  generation: {generation_id or '(none — render-only with no current generation)'}"
          f"  (git {_gen.git_sha(ws)})")
    print(f"  studies: {studies}")
    if not render_only:
        print("  (running baseline + comparison variants — this may take "
              "several minutes per study)")

    results = [prepare_study(ws, slug, dash, steps, render_only) for slug in studies]

    full_run = not study
    print("\n=== SUMMARY ===")
    if generation_id:
        manifest = WorkspacePaths.load(ws).pbg / "generations" / f"{generation_id}.json"
        print(f"generation {generation_id} → {manifest}"
              f"{'' if full_run else '  (PARTIAL: single study; current pointer unchanged)'}")
        reloaded = _gen.read_generation(ws, generation_id)
        n_stamped = len(reloaded.runs) if reloaded else 0
        print(f"  {n_stamped} run(s) stamped into this generation")
    else:
        print("no coordinated generation (render-only with none current)")
    for r in results:
        if r.get("skipped"):
            print(f"  {r['study']}: skipped ({r['skipped']})")
        else:
            nr = len(r.get("runs") or [])
            nv = sum(1 for v in (r.get("rendered") or []) if "bytes" in v)
            print(f"  {r['study']}: {nr} run(s), {nv} comparative(s) rendered")

    return {"investigation": inv, "generation_id": generation_id,
            "studies": results}
