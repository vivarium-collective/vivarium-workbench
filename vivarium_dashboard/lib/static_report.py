"""Static, self-contained investigation report for GitHub Pages.

The interactive report (:func:`render_workspace_report`) is an API-backed SPA and
needs the live dashboard server. This module renders a *static* alternative that
bakes the investigation narrative + each study's decision figure into one page
with no runtime ``/api`` calls, so it can be hosted on plain static hosting
(GitHub Pages). It also embeds an expert-feedback UI that produces a YAML report
and submits it to GitHub (issue or PR) via prefilled URLs — no backend / token.

Opt-in: nothing here runs as part of the default report flow. Invoke explicitly:

    python -m vivarium_dashboard.lib.static_report --slug <inv> [--out _site]
    python -m vivarium_dashboard.lib.static_report --slug <inv> --publish

Per-study decision figure: a study opts in via ``decision_figure_viz: <viz-name>``
in its ``study.yaml`` (the name of a registered visualization whose rendered
``viz/<name>.html`` is self-contained). Studies without it show a "pending" note.
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from .workspace_paths import WorkspacePaths

CONF_COLOR = {"Accepted": "#059669", "Investigating": "#d97706",
              "Planned": "#6b7280", "Refuted": "#dc2626"}
STATE_COLOR = {"executable": "#6b7280", "calibrated": "#d97706",
               "validated": "#059669", "blocked": "#dc2626"}


def _e(x: object) -> str:
    return html.escape(str(x if x is not None else ""))


def _md(text: str) -> str:
    out = []
    for para in (text or "").strip().split("\n\n"):
        p = _e(para.strip()).replace("\n", " ")
        p = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", p)
        if p:
            out.append(f"<p>{p}</p>")
    return "\n".join(out)


def _owner_repo(ws_root: Path) -> tuple[str, str]:
    """(owner/repo, default-branch) from the origin remote; best-effort."""
    repo = "OWNER/REPO"
    try:
        url = subprocess.run(["git", "-C", str(ws_root), "remote", "get-url", "origin"],
                             capture_output=True, text=True).stdout.strip()
        m = re.search(r"github\.com[:/]+([^/]+/[^/.\s]+)", url)
        if m:
            repo = m.group(1)
    except Exception:
        pass
    branch = "main"
    try:
        r = subprocess.run(["git", "-C", str(ws_root), "remote", "show", "origin"],
                           capture_output=True, text=True).stdout
        mb = re.search(r"HEAD branch:\s*(\S+)", r)
        if mb:
            branch = mb.group(1)
    except Exception:
        pass
    return repo, branch


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.is_file() else {}


def _fb(key: str, label: str) -> str:
    return (f'<details class="fb"><summary>✎ Add feedback on {_e(label)}</summary>'
            f'<textarea data-fbkey="{_e(key)}" rows="3" '
            f'placeholder="Your comment becomes an annotations.{_e(key)}[] entry in the YAML">'
            f'</textarea></details>')


def render_static_report(ws_root: Path | str, slug: str, out: Path | str = "_site",
                         *, today: str = "") -> Path:
    """Render the static report for investigation ``slug`` into ``out``.

    Returns the path to the written ``index.html``. Copies each study's decision
    figure (``decision_figure_viz``) into ``<out>/figures/`` and embeds it.
    """
    ws_root = Path(ws_root)
    out = Path(out)
    wp = WorkspacePaths.load(ws_root)
    inv = _load(wp.investigations / slug / "investigation.yaml")
    repo, base = _owner_repo(ws_root)

    study_slugs = list(inv.get("studies") or [])
    studies: dict[str, dict] = {}
    for s in study_slugs:
        try:
            studies[s] = _load(wp.study_dir(s) / "study.yaml")
        except Exception:
            studies[s] = {}

    out.mkdir(parents=True, exist_ok=True)
    figdir = out / "figures"
    figdir.mkdir(exist_ok=True)

    # copy opted-in decision figures + 3D system-behavior views
    def _copy_viz(field: str) -> dict[str, str]:
        out_: dict[str, str] = {}
        for s in study_slugs:
            viz = (studies[s].get(field) or "").strip()
            if not viz:
                continue
            try:
                src = wp.study_dir(s) / "viz" / f"{viz}.html"
            except Exception:
                continue
            if src.is_file():
                dst = f"{s}__{viz}.html"
                shutil.copyfile(src, figdir / dst)
                out_[s] = dst
        return out_

    fig_src = _copy_viz("decision_figure_viz")
    sys_src = _copy_viz("system_figure_viz")  # e.g. particles-3d — animated actin+membrane

    exe = inv.get("executive", {}) or {}
    sa = inv.get("scientific_argument", {}) or {}

    vrows = ""
    for v in (exe.get("validation_status") or []):
        st = v.get("state", "")
        vrows += (f"<tr><td><code>{_e(v.get('study'))}</code></td>"
                  f"<td><span class='pill' style='background:{STATE_COLOR.get(st, '#6b7280')}'>{_e(st)}</span></td>"
                  f"<td>{_e(v.get('note'))}</td></tr>")

    def bullets(items):
        return "".join(f"<li>{_e(x)}</li>" for x in (items or []))

    cards = ""
    for s in study_slugs:
        st = studies[s]
        title = st.get("title") or s
        conf = st.get("confidence", "Investigating")
        fig = fig_src.get(s)
        figblock = (f"<iframe src='figures/{fig}' loading='lazy'></iframe>" if fig else
                    "<div class='nofig'>No decision figure yet (set <code>decision_figure_viz</code> "
                    "in the study, or it needs additional emitted data).</div>")
        sysfig = sys_src.get(s)
        sysblock = (f"<details class='sys' open><summary>🧬 System behavior in 3D "
                    f"— drag to rotate · ▶ / ⏸ to play · slider to scrub</summary>"
                    f"<iframe class='sys3d' src='figures/{sysfig}' loading='lazy'></iframe>"
                    f"</details>" if sysfig else "")
        ev = st.get("evidence_status", "")
        cards += f"""
        <section class="card">
          <div class="cardhead"><h3>{_e(title)}</h3>
            <span class="pill" style="background:{CONF_COLOR.get(conf, '#6b7280')}">{_e(conf)}</span></div>
          <p class="claim">{_e(st.get('claim', ''))}</p>
          <div class="meta">
            <div><b>Expected pattern:</b> {_e(st.get('expected_pattern', ''))}</div>
            <div><b>Acceptance threshold:</b> {_e(st.get('acceptance_threshold', ''))}</div>
            {f'<div><b>Evidence:</b> <code>{_e(ev)}</code></div>' if ev else ''}
          </div>
          {sysblock}
          {figblock}
          {_fb(s.replace('-', '_'), title)}
        </section>"""

    glossary = "".join(f"<dt>{_e(g.get('term'))}</dt><dd>{_e(g.get('definition'))}</dd>"
                       for g in (inv.get("glossary") or []))

    page = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(inv.get('title') or slug)}</title>
<style>
  :root {{ --ink:#1f2937; --muted:#6b7280; --rule:#e5e7eb; --accent:#10b981; }}
  * {{ box-sizing:border-box; }}
  body {{ font:15px/1.6 Inter,system-ui,-apple-system,sans-serif; color:var(--ink);
         max-width:980px; margin:0 auto; padding:32px 20px 80px; }}
  h1 {{ font-size:26px; margin:0 0 4px; }} h2 {{ font-size:19px; margin:36px 0 10px;
        border-bottom:2px solid var(--accent); padding-bottom:4px; }}
  h3 {{ font-size:16px; margin:0; }}
  .lead {{ font-size:17px; color:#374151; }}
  .badge {{ display:inline-block; padding:2px 10px; border-radius:999px; color:#fff; font-size:12px; font-weight:600; }}
  .pill {{ display:inline-block; padding:1px 9px; border-radius:999px; color:#fff; font-size:11px; font-weight:600; white-space:nowrap; }}
  .banner {{ background:#fef3c7; border:1px solid #d97706; color:#92400e; padding:10px 14px; border-radius:8px; font-size:13px; margin:14px 0; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  td,th {{ border:1px solid var(--rule); padding:6px 9px; text-align:left; vertical-align:top; }}
  th {{ background:#f9fafb; }}
  ul {{ margin:6px 0 6px 20px; }} code {{ background:#f3f4f6; padding:1px 5px; border-radius:4px; font-size:12px; }}
  .ladder {{ font-family:ui-monospace,monospace; font-size:13px; background:#f9fafb; border:1px solid var(--rule); border-radius:8px; padding:10px 14px; }}
  .card {{ border:1px solid var(--rule); border-radius:10px; padding:14px 16px; margin:16px 0; box-shadow:0 1px 2px rgba(0,0,0,.04); }}
  .cardhead {{ display:flex; justify-content:space-between; align-items:center; gap:10px; }}
  .claim {{ color:#374151; margin:8px 0; }}
  .meta {{ font-size:13px; color:var(--muted); margin:8px 0 12px; }} .meta b {{ color:var(--ink); }}
  iframe {{ width:100%; height:440px; border:1px solid var(--rule); border-radius:8px; background:#fff; }}
  iframe.sys3d {{ height:520px; background:#f8fafc; }}
  details.sys {{ margin:6px 0 12px; }}
  details.sys summary {{ cursor:pointer; font-size:13px; font-weight:600; color:#0369a1; margin-bottom:6px; }}
  .nofig {{ background:#f9fafb; border:1px dashed var(--rule); border-radius:8px; padding:18px; color:var(--muted); font-size:13px; }}
  dt {{ font-weight:600; margin-top:6px; }} dd {{ margin:0 0 4px 16px; color:#374151; }}
  .foot {{ margin-top:48px; padding-top:14px; border-top:1px solid var(--rule); color:var(--muted); font-size:12px; }}
  a {{ color:#0ea5e9; }}
  details.fb {{ margin:8px 0 2px; }}
  details.fb summary {{ cursor:pointer; color:#0ea5e9; font-size:12px; font-weight:600; }}
  details.fb textarea, .reviewer textarea, .reviewer input {{ width:100%; margin-top:6px; font:13px/1.5 Inter,system-ui,sans-serif; padding:7px 9px; border:1px solid var(--rule); border-radius:6px; resize:vertical; }}
  .reviewer {{ background:#f0f9ff; border:1px solid #bae6fd; border-radius:10px; padding:14px 16px; margin:16px 0; }}
  .reviewer label {{ font-size:12px; font-weight:600; color:#0369a1; display:block; margin-top:8px; }}
  .fbbar {{ position:sticky; bottom:0; background:#fff; border-top:2px solid var(--accent); padding:12px 0; margin-top:24px; display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
  .btn {{ border:0; border-radius:7px; padding:9px 16px; font-size:13px; font-weight:600; cursor:pointer; color:#fff; }}
  .btn.gen {{ background:#475569; }} .btn.issue {{ background:#1f883d; }} .btn.pr {{ background:#8250df; }} .btn.copy {{ background:#0ea5e9; }}
  .btn:disabled {{ opacity:.45; cursor:not-allowed; }}
  #yamlout {{ width:100%; height:240px; font:12px/1.5 ui-monospace,monospace; margin-top:10px; border:1px solid var(--rule); border-radius:8px; padding:10px; display:none; }}
  .hint {{ font-size:12px; color:var(--muted); }}
</style></head><body>

<h1>{_e(inv.get('title') or slug)}</h1>
<span class="badge" style="background:var(--accent)">verdict: {_e(exe.get('verdict_status', 'in-progress'))}</span>
<p class="lead">{_e(inv.get('lead', ''))}</p>
<div class="banner"><b>Status:</b> {_e(exe.get('verdict', ''))}</div>

<div class="reviewer">
  <b>📝 Expert review.</b> <span class="hint">Add comments in the ✎ boxes, then generate a YAML feedback report and submit it to GitHub (issue or PR). No setup — you review and submit in the GitHub UI.</span>
  <label>Your name (reviewer)</label><input id="rev-name" type="text" placeholder="e.g. Jane Expert">
  <label>Overall assessment</label><textarea id="rev-overall" rows="3" placeholder="meta.overall_assessment"></textarea>
</div>

<h2>Executive summary</h2>
{_md(exe.get('verdict_detail', ''))}
<h3>Validation status</h3>
<table><tr><th>Study</th><th>State</th><th>Note</th></tr>{vrows}</table>
{_fb('executive', 'the executive summary')}

<h2>Scientific argument</h2>
<p><b>Main claim.</b> {_e(sa.get('main_claim', ''))}</p>
<p><b>Evidence for</b></p><ul>{bullets(sa.get('evidence_for'))}</ul>
<p><b>Evidence against / open</b></p><ul>{bullets(sa.get('evidence_against'))}</ul>
<p><b>Caveats</b></p><ul>{bullets(sa.get('caveats'))}</ul>
{_fb('scientific_argument', 'the scientific argument')}

<h2>Decision figures</h2>
<p>Each study is organized around one pass/fail decision figure with its expected pattern + acceptance threshold.</p>
{cards}

<h2>Glossary</h2><dl>{glossary}</dl>

<h2>Cross-cutting feedback</h2>
{_fb('global_visual_design', 'visual design (across all figures)')}
{_fb('global_interpretation', 'interpretation (across the investigation)')}

<div class="fbbar">
  <button class="btn gen" id="btn-gen">Preview YAML</button>
  <button class="btn copy" id="btn-copy">Copy YAML</button>
  <button class="btn issue" id="btn-issue">Open as GitHub issue</button>
  <button class="btn pr" id="btn-pr">Propose as PR file</button>
  <span class="hint" id="fb-status"></span>
</div>
<textarea id="yamlout" readonly spellcheck="false"></textarea>

<script>
(function() {{
  var OWNER_REPO = {repo!r}, BASE = {base!r}, SLUG = {slug!r};
  function block(t, n) {{ var p=' '.repeat(n); return (t||'').replace(/\\r/g,'').split('\\n').map(function(l){{return p+l;}}).join('\\n'); }}
  function buildYaml() {{
    var name=(document.getElementById('rev-name').value||'anonymous').trim();
    var overall=(document.getElementById('rev-overall').value||'').trim();
    var y='meta:\\n  investigation: '+SLUG+'\\n  reviewer: '+JSON.stringify(name)+'\\n  focus: expert-review\\n';
    if (overall) y+='  overall_assessment: |\\n'+block(overall,4)+'\\n';
    var byKey={{}};
    document.querySelectorAll('textarea[data-fbkey]').forEach(function(t){{
      var v=(t.value||'').trim(); if(!v) return;
      (byKey[t.getAttribute('data-fbkey')]=byKey[t.getAttribute('data-fbkey')]||[]).push(v);
    }});
    var keys=Object.keys(byKey);
    if (keys.length) {{ y+='annotations:\\n'; keys.forEach(function(k){{
      y+='  '+k+':\\n'; byKey[k].forEach(function(txt){{ y+='    - text: |\\n'+block(txt,8)+'\\n'; }}); }}); }}
    return {{yaml:y, name:name, hasAny: keys.length>0 || !!overall}};
  }}
  function fname(name) {{ var d=new Date().toISOString().slice(0,10);
    var safe=(name||'anon').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')||'anon';
    return 'feedback/'+SLUG+'--'+safe+'--'+d+'.yaml'; }}
  function status(m) {{ document.getElementById('fb-status').textContent=m; }}
  function ensure() {{  // (re)generate from current inputs and show the preview
    var r=buildYaml(); var o=document.getElementById('yamlout'); o.value=r.yaml; o.style.display='block'; return r;
  }}
  function need() {{    // ensure there's content; returns the result or null (+message)
    var r=ensure();
    if (!r.hasAny) {{ status('Add a comment in a ✎ box (or an overall assessment) first.'); return null; }}
    return r;
  }}
  function openUrl(url) {{ var w=window.open(url,'_blank','noopener'); if (!w) {{ location.href=url; }} }}
  document.getElementById('btn-gen').onclick=function(){{
    var r=ensure();
    status(r.hasAny?'Review the YAML below, then Copy / open an issue / propose a PR.':'Add a comment in a ✎ box (or an overall assessment).');
  }};
  document.getElementById('btn-copy').onclick=function(){{ var r=need(); if(!r) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(r.yaml).then(function(){{status('Copied to clipboard.');}},
        function(){{ document.getElementById('yamlout').select(); status('Copy failed — text selected below; press Ctrl/Cmd-C.'); }});
    }} else {{ document.getElementById('yamlout').select(); status('Select-all done — press Ctrl/Cmd-C to copy.'); }}
  }};
  document.getElementById('btn-issue').onclick=function(){{ var r=need(); if(!r) return;
    var url='https://github.com/'+OWNER_REPO+'/issues/new?title='+encodeURIComponent('Expert feedback: '+SLUG+' ('+r.name+')')+
      '&labels=feedback&body='+encodeURIComponent('Generated from the investigation report.\\n\\n```yaml\\n'+r.yaml+'\\n```\\n');
    status('Opening a prefilled GitHub issue…'); openUrl(url);
  }};
  document.getElementById('btn-pr').onclick=function(){{ var r=need(); if(!r) return;
    var url='https://github.com/'+OWNER_REPO+'/new/'+BASE+'?filename='+encodeURIComponent(fname(r.name))+'&value='+encodeURIComponent(r.yaml);
    status('Opening GitHub to commit '+fname(r.name)+' on a new branch…'); openUrl(url);
  }};
}})();
</script>

<div class="foot">
  Generated {_e(today)} from <code>investigations/{_e(slug)}/investigation.yaml</code> + per-study decision figures.
  Static snapshot of the interactive vivarium-dashboard report.
  · <a href="https://github.com/{_e(repo)}">Source</a>
  · <a href="demo/report.html">Original demo</a>
</div>
</body></html>"""

    index = out / "index.html"
    index.write_text(page, encoding="utf-8")
    return index


def publish_to_pages(ws_root: Path | str, out: Path | str = "_site", *,
                     branch: str = "gh-pages", push: bool = True, today: str = "") -> str:
    """Copy the built static report to the ``gh-pages`` branch root (preserving
    everything else, e.g. /demo/) via a worktree. Returns a status string."""
    ws_root = Path(ws_root)
    out = Path(out)
    if not (out / "index.html").is_file():
        raise FileNotFoundError(f"no built report at {out}/index.html — render first")
    wt = ws_root / ".pbg" / "worktrees" / branch
    g = lambda *a: subprocess.run(["git", "-C", str(ws_root), *a], check=True,
                                  capture_output=True, text=True)
    subprocess.run(["git", "-C", str(ws_root), "fetch", "origin", branch], capture_output=True, text=True)
    subprocess.run(["git", "-C", str(ws_root), "worktree", "remove", "--force", str(wt)], capture_output=True, text=True)
    shutil.rmtree(wt, ignore_errors=True)
    g("worktree", "add", str(wt), branch)
    try:
        shutil.copyfile(out / "index.html", wt / "index.html")
        if (wt / "figures").exists():
            shutil.rmtree(wt / "figures")
        if (out / "figures").exists():
            shutil.copytree(out / "figures", wt / "figures")
        subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
        diff = subprocess.run(["git", "-C", str(wt), "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            return "no changes to publish"
        subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m",
                        f"publish: static report{(' — ' + today) if today else ''}"], check=True)
        if push:
            subprocess.run(["git", "-C", str(wt), "push", "origin", branch], check=True)
            return f"published to {branch}"
        return f"committed to {branch} worktree (not pushed)"
    finally:
        subprocess.run(["git", "-C", str(ws_root), "worktree", "remove", "--force", str(wt)],
                       capture_output=True, text=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render/publish a static investigation report.")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--out", default="_site")
    ap.add_argument("--ws", default=".")
    ap.add_argument("--date", default="")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args(argv)
    ws = Path(a.ws)
    idx = render_static_report(ws, a.slug, a.out, today=a.date)
    print(f"rendered {idx} ({idx.stat().st_size} bytes) + figures/")
    if a.build_only or not a.publish:
        return 0
    print(publish_to_pages(ws, a.out, push=not a.no_push, today=a.date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
