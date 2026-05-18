"""Render reports/index.html for the workspace dashboard.

v0.3.0: workspace IS the model. Single dashboard only — no per-model deep dives.
v0.4.1: imports moved to Registry tab; _pending_entries() removed (dead code).

Public API:
  render_workspace_report(ws_root=None, *, today=None) -> Path
"""
from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
import warnings
from datetime import date
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ._root import workspace_root


def _ws_root() -> Path:
    return workspace_root()


# _next_step_hint() removed in v0.4.4. Each tab's page-lead + the workstream
# strip + Build Model's per-phase action buttons carry the "what next" signal
# contextually; a separate top-of-page banner duplicated the information and
# accumulated stale wording.


def _env(template_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
        keep_trailing_newline=True,
    )


def _copy_assets(target_dir: Path) -> None:
    """Copy bundled static assets (css, js, images) into ``reports/assets/``.

    After extraction from pbg-template, asset sources live inside the
    installed ``vivarium_dashboard`` package — not under the workspace's
    ``scripts/`` tree. We copy the flat top-level files in
    ``vivarium_dashboard/static/`` (everything except the nested
    ``loom-explore/`` viewer, which the server streams directly).
    """
    import vivarium_dashboard as _pkg
    static_dir = Path(_pkg.__file__).parent / "static"
    target_dir.mkdir(parents=True, exist_ok=True)
    if static_dir.is_dir():
        for path in static_dir.iterdir():
            if path.is_dir() or path.name.startswith("."):
                continue  # skip nested dirs (e.g. loom-explore/) and dotfiles
            shutil.copy2(path, target_dir / path.name)


def _load_registry(ws_root: Path, package_path: str | None) -> tuple[dict, str | None]:
    """Try to import the workspace package and call build_core()/registry_snapshot().

    package_path: e.g. 'pbg_chromosome_rep1' (the Python package directory name).
    Returns (registry_dict, warning_or_None).
    """
    if not package_path:
        return {"processes": [], "types": []}, None

    ws_root_str = str(ws_root)
    injected = ws_root_str not in sys.path
    if injected:
        sys.path.insert(0, ws_root_str)
    try:
        core = importlib.import_module(f"{package_path}.core")
        build_core = getattr(core, "build_core", None)
        registry_snapshot = getattr(core, "registry_snapshot", None)
        if build_core is None or registry_snapshot is None:
            return {"processes": [], "types": []}, (
                f"{package_path}.core imported but missing build_core() or registry_snapshot()."
            )
        build_core()
        snap = registry_snapshot()

        def _names(items):
            if not items:
                return []
            if isinstance(items[0], str):
                return list(items)
            return [it.get("name", str(it)) for it in items]

        return {
            "processes": _names(snap.get("processes", [])),
            "types": _names(snap.get("types", [])),
        }, None
    except ModuleNotFoundError:
        warning = (
            f"Package '{package_path}' is not importable — registry shown as empty. "
            "Install it in the workspace venv or run /pbg-pull-processes."
        )
        return {"processes": [], "types": []}, warning
    except Exception as exc:
        warning = f"{package_path}.core raised {type(exc).__name__}: {exc}"
        return {"processes": [], "types": []}, warning
    finally:
        if injected and ws_root_str in sys.path:
            sys.path.remove(ws_root_str)


def _load_document(ws_root: Path, package_path: str | None) -> dict:
    """Try to call <package_path>.document.build_document(); return {} on any error."""
    if not package_path:
        return {}
    ws_root_str = str(ws_root)
    injected = ws_root_str not in sys.path
    if injected:
        sys.path.insert(0, ws_root_str)
    try:
        doc_mod = importlib.import_module(f"{package_path}.document")
        build_document = getattr(doc_mod, "build_document", None)
        if build_document is None:
            return {}
        return build_document() or {}
    except Exception:
        return {}
    finally:
        if injected and ws_root_str in sys.path:
            sys.path.remove(ws_root_str)


def _count_bib_entries(ws_root: Path) -> int:
    """Count @-entries in references/papers.bib."""
    bib_file = ws_root / "references" / "papers.bib"
    if not bib_file.exists():
        return 0
    try:
        text = bib_file.read_text()
        return sum(1 for line in text.splitlines() if line.strip().startswith("@"))
    except Exception:
        return 0


def _parse_bib_entries(ws_root: Path) -> list[dict]:
    """Parse references/papers.bib into a flat list of {key, type, fields}.

    Deliberately a minimal parser — doesn't handle every BibTeX nicety, just
    enough to render entries in the dashboard. Supports @article{key, k=v,...}
    with quoted "..." or brace {...} values. Comments (% ...) at line start
    are skipped.

    Returns one dict per entry:
        {
          key:      "Schmidt2016NatBiotechnol",
          type:     "article",
          title:    "...",
          author:   "...",        # raw text from the file
          journal:  "...",
          year:     "2016",
          volume:   "34",
          number:   "1",
          pages:    "104--110",
          doi:      "10.1038/nbt.3418",
          url:      "https://www.nature.com/articles/nbt.3418",
          note:     "...",
          has_notes_md: bool,     # whether references/notes/<key>.md exists
        }
    """
    bib_file = ws_root / "references" / "papers.bib"
    if not bib_file.exists():
        return []
    text = bib_file.read_text()

    import re

    # Strip line-comments (lines beginning with %).
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("%"))

    entries: list[dict] = []
    # Match each @<type>{<key>, ... }. Brace-balanced extraction: starting at
    # the opening { after the type, scan forward counting braces.
    i = 0
    n = len(text)
    while i < n:
        m = re.search(r"@(\w+)\s*\{", text[i:])
        if not m:
            break
        etype = m.group(1).lower()
        start = i + m.end()
        depth = 1
        j = start
        while j < n and depth > 0:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        body = text[start : j - 1] if depth == 0 else text[start:n]
        i = j

        # Split into key, then key=value pairs.
        first_comma = body.find(",")
        if first_comma < 0:
            continue
        key = body[:first_comma].strip()
        rest = body[first_comma + 1:]

        fields: dict[str, str] = {}
        # Split fields by top-level commas, respecting brace/quote nesting.
        buf = []
        depth = 0
        in_q = False
        for ch in rest:
            if ch == "{" and not in_q:
                depth += 1
                buf.append(ch)
            elif ch == "}" and not in_q:
                depth -= 1
                buf.append(ch)
            elif ch == '"':
                in_q = not in_q
                buf.append(ch)
            elif ch == "," and depth == 0 and not in_q:
                _absorb_kv("".join(buf), fields)
                buf = []
            else:
                buf.append(ch)
        if buf:
            _absorb_kv("".join(buf), fields)

        # Check for an accompanying reading-notes markdown file.
        notes_md = ws_root / "references" / "notes" / f"{key}.md"

        entry = {
            "key": key,
            "type": etype,
            "title": fields.get("title", ""),
            "author": fields.get("author", ""),
            "journal": fields.get("journal", ""),
            "year": fields.get("year", ""),
            "volume": fields.get("volume", ""),
            "number": fields.get("number", ""),
            "pages": fields.get("pages", ""),
            "doi": fields.get("doi", ""),
            "url": fields.get("url", ""),
            "note": fields.get("note", ""),
            "has_notes_md": notes_md.is_file(),
            "notes_md_path": str(notes_md.relative_to(ws_root)) if notes_md.is_file() else "",
        }
        entries.append(entry)
    return entries


def _absorb_kv(chunk: str, fields: dict) -> None:
    """Internal: parse a single 'key = {value}' chunk into the fields dict."""
    if "=" not in chunk:
        return
    k, v = chunk.split("=", 1)
    k = k.strip().lower()
    v = v.strip()
    # Strip outer braces or quotes.
    if v.startswith("{") and v.endswith("}"):
        v = v[1:-1]
    elif v.startswith('"') and v.endswith('"'):
        v = v[1:-1]
    # Collapse internal braces (e.g. {DnaA} -> DnaA) and whitespace.
    import re
    v = re.sub(r"\s+", " ", v.replace("{", "").replace("}", "")).strip()
    if k and v:
        fields[k] = v


_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")


def _human_size(n: int) -> str:
    """Render a byte count as e.g. '314 KB' or '1.2 MB'."""
    size = float(n)
    for unit in _SIZE_UNITS:
        if size < 1024.0 or unit == _SIZE_UNITS[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}" if size < 10 else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{int(size)} TB"


def _enrich_with_file_info(entries: list[dict], ws_root: Path) -> list[dict]:
    """For each entry with a 'path' field, attach file_exists / size_bytes / size_human / sha256_valid.

    Used to render datasets, expert_docs, references_pdfs with file-presence indicators
    instead of plain links the user has to click to verify.
    """
    out = []
    for raw in entries:
        if not isinstance(raw, dict):
            out.append(raw)
            continue
        e = dict(raw)  # don't mutate the original
        path = e.get("path")
        if not path:
            e["file_exists"] = None
            e["size_human"] = None
            e["sha256_valid"] = None
            out.append(e)
            continue
        abs_path = (ws_root / path) if not Path(path).is_absolute() else Path(path)
        if not abs_path.exists():
            e["file_exists"] = False
            e["size_human"] = None
            e["sha256_valid"] = None
            out.append(e)
            continue
        try:
            size = abs_path.stat().st_size
            e["file_exists"] = True
            e["size_bytes"] = size
            e["size_human"] = _human_size(size)
        except OSError:
            e["file_exists"] = None
            e["size_human"] = None
        # sha256 check is optional — only validate if metadata declares one.
        declared = e.get("sha256")
        if declared and e.get("file_exists"):
            try:
                import hashlib as _hashlib
                h = _hashlib.sha256()
                with abs_path.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
                e["sha256_valid"] = h.hexdigest() == declared
            except OSError:
                e["sha256_valid"] = None
        else:
            e["sha256_valid"] = None
        out.append(e)
    return out


def _detect_github_repo(ws_root: Path) -> str | None:
    """Parse `git remote get-url origin` and return 'owner/repo' if GitHub, else None."""
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=ws_root, capture_output=True, text=True, check=True,
        )
        url = r.stdout.strip()
        # SSH: git@github.com:owner/repo.git
        import re
        m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def render_workspace_report(ws_root: Path | None = None, *, today: str | None = None) -> Path:
    """Build <ws_root>/reports/index.html from workspace.yaml + pending branches."""
    ws_root = ws_root or _ws_root()
    today = today or date.today().isoformat()
    ws = yaml.safe_load((ws_root / "workspace.yaml").read_text())
    decisions_file = ws_root / "docs" / "decisions.yaml"
    decisions = (
        (yaml.safe_load(decisions_file.read_text()) or {}).get("decisions", [])
        if decisions_file.exists() else []
    )
    import vivarium_dashboard as _pkg
    template_dir = Path(_pkg.__file__).parent / "templates"
    env = _env(template_dir)
    tpl = env.get_template("index.html.j2")
    out = ws_root / "reports" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    _copy_assets(ws_root / "reports" / "assets")

    references_count = _count_bib_entries(ws_root)
    bib_entries = _parse_bib_entries(ws_root)
    # Merge cached enrichment data (DOI / publisher URL / OA PDF URL) into
    # each entry so the rendered cards can surface "Open ↗" + "OA PDF ↗" links.
    try:
        from vivarium_dashboard.lib.references_fetch import (
            load_cache as _load_refs_cache, enrich_entries as _enrich_refs,
        )
        bib_entries = _enrich_refs(bib_entries, _load_refs_cache(ws_root))
    except Exception:
        # Cache failures must never break the report render.
        pass
    datasets = _enrich_with_file_info(ws.get("datasets") or [], ws_root)
    expert_docs = _enrich_with_file_info(ws.get("expert_docs") or [], ws_root)
    references_pdfs = _enrich_with_file_info(ws.get("references_pdfs") or [], ws_root)
    imports = ws.get("imports") or {}
    observables = ws.get("observables") or []
    visualizations = ws.get("visualizations") or []
    simulations = ws.get("simulations") or []
    package_path = ws.get("package_path")

    # Load registry from workspace package.
    registry, registry_warning = _load_registry(ws_root, package_path)
    pbg_doc = _load_document(ws_root, package_path)

    # Active investigation: when the workspace has exactly one
    # investigation declared at investigations/<name>/investigation.yaml,
    # surface its name alongside the workspace name in the dashboard
    # chrome (so e.g. `v2ecoli:colonies` instead of just `v2ecoli`).
    # Multiple investigations → leave blank; the workspace switcher
    # already exposes a per-investigation selector.
    active_investigation_name = ""
    inv_root = ws_root / "investigations"
    if inv_root.is_dir():
        inv_dirs = sorted(
            d for d in inv_root.iterdir()
            if d.is_dir() and (d / "investigation.yaml").is_file()
        )
        if len(inv_dirs) == 1:
            try:
                inv_spec = yaml.safe_load((inv_dirs[0] / "investigation.yaml").read_text()) or {}
                active_investigation_name = inv_spec.get("name") or inv_dirs[0].name
            except Exception:
                active_investigation_name = inv_dirs[0].name

    # Cache-bust for the live dashboard assets. Browsers happily serve a
    # stale walkthrough.js / style.css if the URL doesn't change, even
    # when the plugin ships a newer version. Computing a version stamp
    # from the asset mtimes makes the URL change whenever the file does.
    assets_dir = ws_root / "reports" / "assets"
    def _mtime(rel: str) -> str:
        p = assets_dir / rel
        try:
            return str(int(p.stat().st_mtime))
        except OSError:
            return "0"
    asset_version = _mtime("walkthrough.js") + "_" + _mtime("style.css")

    out.write_text(tpl.render(
        workspace_name=ws["name"],
        active_investigation_name=active_investigation_name,
        workspace_description=ws.get("description", ""),
        generated_at=today,
        imports=imports,
        datasets=datasets,
        references_count=references_count,
        references_pdfs=references_pdfs,
        bib_entries=bib_entries,
        decisions=decisions,
        expert_docs=expert_docs,
        observables=observables,
        visualizations=visualizations,
        simulations=simulations,
        package_path=package_path,
        registry=registry,
        registry_warning=registry_warning,
        pbg_doc_json=json.dumps(pbg_doc, indent=2, default=str),
        asset_version=asset_version,
    ), encoding="utf-8")
    return out


def render_dashboard(ws_root: Path | str, *, write_all: bool = True) -> Path:
    """CLI-facing alias for :func:`render_workspace_report`.

    ``write_all`` is accepted for forward-compatibility with multi-page
    renderers but currently ignored (we only render the workspace dashboard).
    """
    return render_workspace_report(Path(ws_root))
