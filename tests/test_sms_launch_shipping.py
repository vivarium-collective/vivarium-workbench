"""sms-launch.js (the quarantined SMS/vEcoli launch UI, moved out of
study-detail.js) ships wherever study-detail.js does: the live study page and
the published read-only bundle both load it, BEFORE study-detail.js (which calls
window.SmsLaunch.mount while it loads). The hook's own behaviour, with and without
the file, is covered by tests/js/test_sms_launch_hook.js.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml


def _script_pos(html: str, name: str) -> int:
    # the <script src> tag, not prose mentions
    m = re.search(r'<script[^>]*\bsrc="[^"]*/' + re.escape(name) + r'[?"]', html)
    assert m, f"{name} not loaded by the study page"
    return m.start()


def _write_study(ws: Path, slug: str) -> None:
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    spec = {
        "schema_version": 3, "name": slug, "objective": "test", "status": "in_progress",
        "baseline": [{"name": "core", "composite": "pkg.composites.core", "params": {}}],
        "variants": [], "runs": [],
    }
    (sd / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))


def test_live_study_page_loads_sms_launch_before_study_detail(tmp_path, inproc_client):
    ws = tmp_path / "ws"
    _write_study(ws, "demo")
    client = inproc_client(ws)
    html = client.get("/studies/demo").text
    assert _script_pos(html, "sms-launch.js") < _script_pos(html, "study-detail.js")
    # The button is no longer server-rendered; sms-launch.js creates it.
    assert 'id="study-run-composite"' not in html
    js = client.get("/sms-launch.js")
    assert js.status_code == 200
    assert "window.SmsLaunch" in js.text


def test_published_bundle_ships_sms_launch(tmp_path):
    from vivarium_workbench import publish

    ws = tmp_path / "ws"
    _write_study(ws, "demo")
    out = tmp_path / "bundle"
    publish.build_bundle(ws, out)
    assert (out / "assets" / "sms-launch.js").is_file()
    html = (out / "studies" / "demo" / "index.html").read_text()
    assert _script_pos(html, "sms-launch.js") < _script_pos(html, "study-detail.js")
    assert 'src="/assets/sms-launch.js"' in html  # rewritten to the bundle's assets/
