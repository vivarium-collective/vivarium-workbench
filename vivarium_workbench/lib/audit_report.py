"""Render the L0-L5 reproducibility audit as a self-contained HTML page.

Same content as the in-app Audit view, but a standalone file you can open with
no server, email, or commit — produced on demand by ``vivarium-workbench audit
--html PATH`` (and reusable behind a UI download button). Pure string
generation, inline CSS, no external assets.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from vivarium_workbench.lib.html_render import esc as _esc

from vivarium_workbench.lib.audit_views import build_audit
from vivarium_workbench.lib.workspace_paths import WorkspacePaths

# L0 (red) -> L5 (green); '—' (ungraded) = slate. Mirrors static/audit.js.
_GRADE_COLORS = ["#ef4444", "#f97316", "#eab308", "#84cc16", "#22c55e", "#16a34a"]
_STATUS = {"pass": ("✓", "#0d9488"), "warn": ("⚠", "#d97706"), "fail": ("✗", "#e11d48")}




def _grade_color(level) -> str:
    return _GRADE_COLORS[level] if isinstance(level, int) and 0 <= level <= 5 else "#94a3b8"


def _badge(grade: dict) -> str:
    if not grade:
        return ""
    bl = grade.get("blocked_by")
    title = (f"blocked at {bl['level']}: {bl['name']}" if bl else "fully rebuildable (L5)")
    return (f'<span class="badge" title="{_esc(title)}" '
            f'style="background:{_grade_color(grade.get("level"))}">{_esc(grade.get("label"))}</span>')


def _check_row(c: dict) -> str:
    glyph, color = _STATUS.get(c.get("status"), ("?", "#94a3b8"))
    detail = f' <span class="detail">— {_esc(c.get("detail"))}</span>' if c.get("detail") else ""
    return (f'<div class="row"><span class="g" style="color:{color}">{glyph}</span>'
            f'<span class="lvl">{_esc(c.get("level"))}</span>'
            f'<span class="nm"><b>{_esc(c.get("name"))}</b>{detail}</span></div>')


def _block(kind: str, audit: dict) -> str:
    grade = audit.get("grade")
    body = ""
    if grade and grade.get("checks"):
        body = "".join(_check_row(c) for c in grade["checks"])
    elif grade:
        bl = grade.get("blocked_by")
        members = (f"{grade['n_members']} member{'' if grade['n_members'] == 1 else 's'} · "
                   if grade.get("n_members") is not None else "")
        status = (f"blocked at {_esc(bl['level'])} — {_esc(bl['name'])}" if bl
                  else "fully rebuildable (L5)")
        body = f'<div class="inv">{members}{status}</div>'
    body += "".join(_check_row(c) for c in (audit.get("checks") or []))
    return (f'<div class="card">'
            f'<div class="head">{_badge(grade)}'
            f'<span class="slug">{_esc(audit.get("slug"))}</span>'
            f'<span class="kind">{_esc(kind)}</span></div>{body}</div>')


def _dist_chips(dist: dict) -> str:
    chips = []
    for k in ["L5", "L4", "L3", "L2", "L1", "L0", "—"]:
        if dist.get(k):
            lvl = -1 if k == "—" else int(k[1:])
            chips.append(f'<span class="chip" style="background:{_grade_color(lvl)}">{_esc(k)} {dist[k]}</span>')
    return "".join(chips)


def render_audit_html(ws_root, *, title: str | None = None,
                      generated_at: str | None = None, rerun_href: str | None = None) -> str:
    """Return a self-contained HTML string for the workspace's audit report."""
    ws_root = Path(ws_root)
    report, _ = build_audit(ws_root)
    summ = report.get("summary", {})
    name = title or _workspace_name(ws_root)
    err = report.get("error")
    blocks = "".join(_block("study", s) for s in report.get("studies", []))
    blocks += "".join(_block("investigation", i) for i in report.get("investigations", []))
    if not blocks:
        blocks = '<div class="notice">No studies or investigations to audit.</div>'
    stamp = ""
    if generated_at:
        stamp = f'<span class="stamp">generated {_esc(generated_at)}</span>'
    if rerun_href:
        stamp += f'<a class="rerun" href="{_esc(rerun_href)}">↻ Re-run audit</a>'
    return _PAGE.format(
        name=_esc(name),
        n_studies=summ.get("n_studies", len(report.get("studies", []))),
        n_inv=summ.get("n_investigations", len(report.get("investigations", []))),
        chips=_dist_chips(summ.get("grade_distribution", {})),
        stamp=(f'<div class="meta">{stamp}</div>' if stamp else ""),
        error=(f'<div class="degraded">Audit degraded: {_esc(err)}</div>' if err else ""),
        blocks=blocks,
    )


def get_or_build_report(ws_root, *, rerun: bool = False) -> str:
    """Return the persisted audit-report HTML, (re)generating it when asked or
    when none is cached. Cache: ``<ws>/.pbg/audit/{report.html, meta.json}``.
    The page carries its own generated-at stamp + a ``?rerun=1`` link."""
    ws_root = Path(ws_root)
    cache = WorkspacePaths.load(ws_root).pbg / "audit"
    html_path, meta_path = cache / "report.html", cache / "meta.json"
    if not rerun and html_path.is_file():
        return html_path.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    page = render_audit_html(ws_root, generated_at=now, rerun_href="?rerun=1")
    try:
        cache.mkdir(parents=True, exist_ok=True)
        html_path.write_text(page, encoding="utf-8")
        meta_path.write_text(json.dumps({"generated_at": now}), encoding="utf-8")
    except OSError:
        pass  # read-only fs (snapshot) — still return the freshly rendered page
    return page


def _workspace_name(ws_root: Path) -> str:
    try:
        import yaml
        data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
        return data.get("name") or ws_root.name
    except Exception:  # noqa: BLE001
        return ws_root.name


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reproducibility audit — {name}</title>
<style>
  body{{margin:0;font-family:-apple-system,"Segoe UI",sans-serif;color:#1e293b;background:#f8fafc;line-height:1.5}}
  .wrap{{max-width:960px;margin:0 auto;padding:24px 18px}}
  h1{{font-size:1.15em;margin:0 0 2px}}
  .sub{{color:#64748b;font-size:0.85em;margin-bottom:14px}}
  .summary{{display:flex;gap:22px;align-items:center;flex-wrap:wrap;padding:12px 14px;border:1px solid #e5e7eb;
           border-radius:8px;background:#fff;margin-bottom:14px}}
  .summary b{{color:#1e293b}} .summary .lbl{{color:#475569}}
  .chip,.badge{{color:#fff;font-weight:700;border-radius:9999px}}
  .chip{{font-size:0.8em;padding:1px 8px}}
  .gr{{display:flex;gap:5px;align-items:center;flex-wrap:wrap}}
  .gr .cap{{color:#94a3b8;font-size:0.75em;text-transform:uppercase;letter-spacing:0.05em}}
  .meta{{margin-left:auto;display:flex;gap:12px;align-items:center;font-size:0.8em}}
  .meta .stamp{{color:#94a3b8}}
  .meta .rerun{{color:#2563eb;text-decoration:none;font-weight:600}}
  .meta .rerun:hover{{text-decoration:underline}}
  .degraded{{padding:8px 12px;margin-bottom:10px;border-radius:6px;background:#fef3c7;color:#92400e;font-size:0.85em}}
  .card{{border:1px solid #e5e7eb;border-radius:8px;padding:10px 13px;margin:8px 0;background:#fff}}
  .head{{display:flex;gap:8px;align-items:center;margin-bottom:5px}}
  .badge{{font-size:0.8em;padding:1px 9px;letter-spacing:0.03em}}
  .slug{{flex:1;font-weight:600}}
  .kind{{font-size:0.7em;text-transform:uppercase;letter-spacing:0.05em;color:#94a3b8}}
  .row{{display:flex;gap:8px;align-items:flex-start;font-size:0.85em;margin:2px 0}}
  .row .g{{width:1.1em;text-align:center;font-weight:700;flex:none}}
  .row .lvl{{width:1.8em;color:#94a3b8;font-variant-numeric:tabular-nums;flex:none}}
  .row .nm{{color:#334155}} .row .detail{{color:#64748b}}
  .inv{{font-size:0.83em;color:#64748b;margin:2px 0}}
  .notice{{padding:16px;color:#64748b}}
  footer{{color:#94a3b8;font-size:0.75em;margin-top:16px}}
</style></head>
<body><div class="wrap">
  <h1>Reproducibility audit</h1>
  <div class="sub">{name}</div>
  <div class="summary">
    <div class="lbl"><b>{n_studies}</b> studies</div>
    <div class="lbl"><b>{n_inv}</b> investigations</div>
    <div class="gr"><span class="cap">grade</span>{chips}</div>
    {stamp}
  </div>
  {error}
  {blocks}
  <footer>L0 Declared · L1 Keyable · L2 Run recorded · L3 Content-addressed output ·
    L4 Environment pinned · L5 Rebuildable from sources — generated by vivarium-workbench audit.</footer>
</div></body></html>
"""
