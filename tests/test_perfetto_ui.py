"""The Perfetto UI the trace action opens: config switch, pinned fetch, serving.

``fetch_bundle`` is exercised with a fake opener built from synthetic files whose
hashes are patched in, so the verification chain (pinned index/manifest/licence
hashes -> manifest SRI hashes -> every resource) is tested without the network.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from vivarium_workbench.lib import perfetto_ui


def _sri(data: bytes) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode()


@pytest.fixture
def fake_release(monkeypatch):
    """A tiny synthetic 'release' and an opener serving it; pins patched to match."""
    files = {
        "frontend_bundle.js": b"console.log('perfetto')",
        "trace_processor.wasm": b"\x00asm\x01\x00\x00\x00",
        "assets/Roboto.woff2": b"font",
    }
    index = b"<!doctype html><script src='./frontend_bundle.js'></script>"
    manifest = json.dumps({"resources": {k: _sri(v) for k, v in files.items()}}).encode()
    licence = b"Apache License 2.0"
    root = f"{perfetto_ui.PERFETTO_UI_ORIGIN}/{perfetto_ui.PERFETTO_UI_VERSION}/"
    served = {root + "index.html": index, root + "manifest.json": manifest,
              perfetto_ui.LICENSE_URL: licence}
    served.update({root + k: v for k, v in files.items()})
    monkeypatch.setattr(perfetto_ui, "INDEX_SHA256", hashlib.sha256(index).hexdigest())
    monkeypatch.setattr(perfetto_ui, "MANIFEST_SHA256", hashlib.sha256(manifest).hexdigest())
    monkeypatch.setattr(perfetto_ui, "LICENSE_SHA256", hashlib.sha256(licence).hexdigest())
    fetched: list = []

    def opener(url: str) -> bytes:
        fetched.append(url)
        return served[url]

    return {"opener": opener, "served": served, "root": root, "files": files, "fetched": fetched}


def test_fetch_bundle_mirrors_and_verifies(tmp_path, fake_release):
    dest = tmp_path / "pf"
    perfetto_ui.fetch_bundle(dest, opener=fake_release["opener"])
    assert (dest / "index.html").is_file() and (dest / "LICENSE").is_file()
    assert (dest / "assets" / "Roboto.woff2").read_bytes() == b"font"
    assert perfetto_ui.installed_version(dest) == perfetto_ui.PERFETTO_UI_VERSION
    # idempotent: a second call fetches nothing
    n = len(fake_release["fetched"])
    perfetto_ui.fetch_bundle(dest, opener=fake_release["opener"])
    assert len(fake_release["fetched"]) == n


def test_fetch_bundle_refuses_a_tampered_resource(tmp_path, fake_release):
    fake_release["served"][fake_release["root"] + "frontend_bundle.js"] = b"evil()"
    dest = tmp_path / "pf"
    with pytest.raises(perfetto_ui.BundleVerificationError, match="frontend_bundle.js"):
        perfetto_ui.fetch_bundle(dest, opener=fake_release["opener"])
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []   # no half-written temp dir left behind


def test_fetch_bundle_refuses_a_moved_manifest(tmp_path, fake_release, monkeypatch):
    monkeypatch.setattr(perfetto_ui, "MANIFEST_SHA256", "0" * 64)
    with pytest.raises(perfetto_ui.BundleVerificationError, match="manifest.json"):
        perfetto_ui.fetch_bundle(tmp_path / "pf", opener=fake_release["opener"])


def test_pinned_constants_are_consistent():
    assert perfetto_ui.PERFETTO_UI_VERSION.endswith(perfetto_ui.PERFETTO_UI_COMMIT[:9])
    for h in (perfetto_ui.INDEX_SHA256, perfetto_ui.MANIFEST_SHA256, perfetto_ui.LICENSE_SHA256):
        assert len(h) == 64 and int(h, 16) >= 0
    assert perfetto_ui.PERFETTO_UI_COMMIT in perfetto_ui.LICENSE_URL


# ---------------------------------------------------------------- viewer_config


@pytest.fixture
def installed(tmp_path, fake_release, monkeypatch):
    dest = tmp_path / "pf"
    perfetto_ui.fetch_bundle(dest, opener=fake_release["opener"])
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI_DIR", str(dest))
    return dest


@pytest.mark.parametrize("mode,expected", [
    (None, ("bundled", "/perfetto/")),
    ("auto", ("bundled", "/perfetto/")),
    ("bundled", ("bundled", "/perfetto/")),
    ("off", ("off", None)),
    ("https://perfetto.example.org/ui", ("external", "https://perfetto.example.org/ui/")),
])
def test_viewer_config_with_bundle(installed, monkeypatch, mode, expected):
    if mode is None:
        monkeypatch.delenv("VIVARIUM_WORKBENCH_PERFETTO_UI", raising=False)
    else:
        monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI", mode)
    cfg = perfetto_ui.viewer_config()
    assert (cfg.mode, cfg.url) == expected


@pytest.mark.parametrize("mode,expected", [
    (None, ("external", "https://ui.perfetto.dev/")),
    ("bundled", ("off", None)),   # bundled-only and none installed: no viewer
])
def test_viewer_config_without_bundle(tmp_path, monkeypatch, mode, expected):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI_DIR", str(tmp_path / "absent"))
    if mode is None:
        monkeypatch.delenv("VIVARIUM_WORKBENCH_PERFETTO_UI", raising=False)
    else:
        monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI", mode)
    cfg = perfetto_ui.viewer_config()
    assert (cfg.mode, cfg.url) == expected


# ---------------------------------------------------------------- serving


@pytest.fixture
def rc(tmp_path):
    from fastapi.testclient import TestClient

    from vivarium_workbench.api.app import create_app, get_workspace

    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: tmp_path
    return TestClient(app)


def test_route_serves_bundle(installed, rc):
    r = rc.get("/perfetto/")
    assert r.status_code == 200 and b"frontend_bundle.js" in r.content
    assert r.headers["cache-control"] == "no-store"
    w = rc.get("/perfetto/trace_processor.wasm")
    assert w.status_code == 200 and w.headers["content-type"] == "application/wasm"
    assert "max-age" in w.headers["cache-control"]
    assert rc.get("/perfetto/nope.js").status_code == 404


def test_route_redirects_bare_prefix(installed, rc):
    r = rc.get("/perfetto", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/perfetto/"


def test_route_rejects_traversal(installed):
    with pytest.raises(perfetto_ui.AssetTraversal):
        perfetto_ui.resolve_asset("../secret")


def test_resolve_asset_refuses_a_symlink_out_of_the_bundle(installed, tmp_path):
    """Allowlist (Eran's #1214 review): the resolved file must lie inside the bundle -- a
    symlink in the bundle pointing elsewhere is refused, not served."""
    secret = tmp_path / "secret.txt"
    secret.write_text("not for the browser")
    (installed / "escape.js").symlink_to(secret)
    with pytest.raises(perfetto_ui.AssetTraversal):
        perfetto_ui.resolve_asset("escape.js")


def test_resolve_asset_returns_the_resolved_path_inside_the_bundle(installed):
    p = perfetto_ui.resolve_asset("frontend_bundle.js")
    assert p is not None and p.is_relative_to(installed.resolve()) and p.is_file()


def test_route_404_without_bundle(tmp_path, monkeypatch, rc):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI_DIR", str(tmp_path / "absent"))
    assert rc.get("/perfetto/").status_code == 404


# ------------------------------------------------ frontend.css: out-of-bundle font URLs

#: The shape of the pinned release's frontend.css: the correct declaration, then the
#: duplicates compiled deeper in Perfetto's tree whose URLs climb out of the release dir.
BROKEN_CSS = (
    '@font-face{font-family:"Roboto";src:url(assets/Roboto.woff2) format("woff2")}\n'
    '@font-face{font-family:"Roboto";src:url(../assets/assets/Roboto.woff2) format("woff2")}\n'
    '@font-face{font-family:"Roboto";src:url(../../assets/assets/Roboto.woff2) format("woff2")}\n'
    "@font-face{font-family:'Roboto';src:url('../../../../assets/assets/Roboto.woff2')}\n"
    '@font-face{font-family:"X";src:url(../assets/assets/NotInBundle.woff2)}\n'
    '.chev{background:url("data:image/svg+xml,%3Csvg%3E")}\n'
)


@pytest.fixture
def installed_with_css(tmp_path, fake_release, monkeypatch):
    """A bundle whose frontend.css has the pinned release's escaping font URLs -- and is
    verified like every other resource (it is in the manifest)."""
    files = dict(fake_release["files"])
    files["frontend.css"] = BROKEN_CSS.encode()
    root = fake_release["root"]
    manifest = json.dumps({"resources": {k: _sri(v) for k, v in files.items()}}).encode()
    served = dict(fake_release["served"])
    served[root + "manifest.json"] = manifest
    served[root + "frontend.css"] = files["frontend.css"]
    monkeypatch.setattr(perfetto_ui, "MANIFEST_SHA256", hashlib.sha256(manifest).hexdigest())
    dest = tmp_path / "pf"
    perfetto_ui.fetch_bundle(dest, opener=served.__getitem__)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI_DIR", str(dest))
    return dest


def test_stylesheet_font_urls_point_back_into_the_bundle(installed_with_css, rc):
    r = rc.get("/perfetto/frontend.css")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")
    css = r.text
    # Every escaping URL to a font the bundle has now names the bundle's own copy ...
    assert "assets/assets/Roboto.woff2" not in css
    assert css.count("url(assets/Roboto.woff2)") == 3
    assert "url('assets/Roboto.woff2')" in css            # quotes kept
    # ... a font the bundle lacks is left alone (the rewrite only points at verified files),
    assert "url(../assets/assets/NotInBundle.woff2)" in css
    assert 'url("data:image/svg+xml,%3Csvg%3E")' in css   # other URLs untouched
    # ... and the rewritten URL is served, from inside the bundle.
    assert rc.get("/perfetto/assets/Roboto.woff2").content == b"font"


def test_stylesheet_on_disk_stays_the_verified_file(installed_with_css, rc):
    """The correction is applied as it is served: the file keeps the manifest's hash."""
    rc.get("/perfetto/frontend.css")
    on_disk = (installed_with_css / "frontend.css").read_bytes()
    assert on_disk == BROKEN_CSS.encode()
    manifest = json.loads((installed_with_css / "manifest.json").read_text())
    assert _sri(on_disk) == manifest["resources"]["frontend.css"]


def test_only_the_top_level_stylesheet_is_rewritten(installed_with_css):
    target = installed_with_css / "frontend_bundle.js"
    assert perfetto_ui.served_bytes("frontend_bundle.js", target) is None
    assert perfetto_ui.served_bytes("assets/x.css", installed_with_css / "assets" / "x.css") is None


def test_fix_stylesheet_never_names_a_path_outside_assets(tmp_path):
    (tmp_path / "assets").mkdir()
    css = "a{src:url(../assets/assets/..)} b{src:url(../../assets/assets/.hidden)}"
    assert perfetto_ui.fix_stylesheet(css, tmp_path) == css


def test_traversal_still_refused_by_the_route(installed_with_css, rc):
    for path in ("/perfetto/..%2F..%2Fsecret", "/perfetto/assets/..%2F..%2Fsecret"):
        assert rc.get(path).status_code in (403, 404)
    with pytest.raises(perfetto_ui.AssetTraversal):
        perfetto_ui.resolve_asset("assets/../../secret")
