"""Tests for lib.upload_mutations — upload / import pure builders.

Covers (per builder):
  - Happy paths: file write / registration + (dict, 2xx) return.
  - Every 400/404/409 validation path.
  - Behavioral commit-path tests: drive the REAL server._post_* handler with
    server._active_branch_action monkeypatched to a recorder, asserting:
      (a) _active_branch_action IS called with the exact commit_msg,
      (b) validation 400 returns BEFORE the wrapper is ever called,
      (c) the inner action() re-raises on a lib non-200.

Route tests (FastAPI via TestClient):
  - Happy path per route (file written + workspace.yaml updated).
  - Key error paths per route.
  - Each route appears in the OpenAPI schema.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

import pbg_superpowers

from vivarium_dashboard.lib import upload_mutations as um
from vivarium_dashboard.api.app import create_app, get_workspace


_SCHEMA_SRC = Path(pbg_superpowers.__file__).parent / "schemas" / "workspace.schema.json"

_WS_YAML = """\
schema_version: 3
name: testws
created: "2026-01-01"
plugin_version: "0.14.0"
package_path: pbg_testws
datasets: []
expert_docs: []
imports: {}
"""

_INV_SLUG = "dnaa-replication"


def _make_ws(tmp_path: Path) -> Path:
    """Schema-valid workspace + an empty investigation for the scoped paths."""
    w = tmp_path / "ws"
    w.mkdir()
    (w / "workspace.yaml").write_text(_WS_YAML, encoding="utf-8")
    schemas = w / ".pbg" / "schemas"
    schemas.mkdir(parents=True)
    (schemas / "workspace.schema.json").write_text(
        _SCHEMA_SRC.read_text(encoding="utf-8"), encoding="utf-8"
    )
    inv = w / "investigations" / _INV_SLUG
    (inv / "studies").mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        f"name: {_INV_SLUG}\ntitle: {_INV_SLUG}\nstudies: []\n", encoding="utf-8"
    )
    return w


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: Any) -> Path:
    """Workspace fixture; registers the workspace root so schema validation
    (load_workspace / save_workspace) resolves the bundled schema."""
    w = _make_ws(tmp_path)
    import vivarium_dashboard.lib._root as _root
    monkeypatch.setattr(_root, "_WS_ROOT", w.resolve())
    monkeypatch.setattr(_root, "_WS_PATHS", None)
    return w


@pytest.fixture
def client(ws: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _read_ws(ws: Path) -> dict:
    return yaml.safe_load((ws / "workspace.yaml").read_text(encoding="utf-8"))


def _read_inv(ws: Path) -> dict:
    return yaml.safe_load(
        (ws / "investigations" / _INV_SLUG / "investigation.yaml").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# register_dataset
# ---------------------------------------------------------------------------


class TestRegisterDataset:
    def test_happy_file_b64_global(self, ws: Path) -> None:
        resp, code = um.register_dataset(ws, {
            "name": "my data", "file_b64": _b64(b"a,b\n1,2\n"), "filename": "d.csv",
        })
        assert code == 200, resp
        assert resp["ok"] is True
        # File landed under datasets/<slug>/<filename>.
        dest = ws / "datasets" / "my-data" / "d.csv"
        assert dest.is_file()
        # Registered in workspace.yaml with computed sha256.
        ds = _read_ws(ws)["datasets"]
        assert ds[0]["name"] == "my data"
        assert ds[0]["path"] == "datasets/my-data/d.csv"
        assert len(ds[0]["sha256"]) == 64

    def test_happy_claims_csv_parsed(self, ws: Path) -> None:
        resp, code = um.register_dataset(ws, {
            "name": "d", "url": "https://x/y", "claims": "c1, c2 ,c3",
        })
        assert code == 200
        assert _read_ws(ws)["datasets"][0]["claims"] == ["c1", "c2", "c3"]

    def test_happy_path_form_computes_sha(self, ws: Path) -> None:
        src = ws / "raw.txt"
        src.write_text("hello", encoding="utf-8")
        resp, code = um.register_dataset(ws, {"name": "d", "path": "raw.txt"})
        assert code == 200
        entry = _read_ws(ws)["datasets"][0]
        assert entry["path"] == "raw.txt"
        assert len(entry["sha256"]) == 64

    def test_happy_url_form_keeps_sha(self, ws: Path) -> None:
        resp, code = um.register_dataset(ws, {
            "name": "d", "url": "https://x/y", "sha256": "deadbeef",
        })
        assert code == 200
        entry = _read_ws(ws)["datasets"][0]
        assert entry["url"] == "https://x/y"
        assert entry["sha256"] == "deadbeef"

    def test_happy_investigation_scoped(self, ws: Path) -> None:
        resp, code = um.register_dataset(ws, {
            "name": "oric-counts", "file_b64": _b64(b"x\n"), "filename": "oric.csv",
            "investigation": _INV_SLUG,
        })
        assert code == 200, resp
        dest = (ws / "investigations" / _INV_SLUG / "inputs" / "datasets"
                / "oric-counts" / "oric.csv")
        assert dest.is_file()
        ds = _read_inv(ws)["inputs"]["datasets"]
        assert any(d.get("name") == "oric-counts" for d in ds)
        # Global pool stays empty.
        assert not (_read_ws(ws).get("datasets") or [])

    def test_400_missing_name(self, ws: Path) -> None:
        resp, code = um.register_dataset(ws, {"url": "https://x"})
        assert code == 400
        assert "name is required" in resp["error"]

    def test_400_invalid_investigation_slug(self, ws: Path) -> None:
        resp, code = um.register_dataset(ws, {
            "name": "d", "url": "https://x", "investigation": "Bad Slug",
        })
        assert code == 400
        assert "invalid investigation slug" in resp["error"]

    def test_400_filename_required_with_file_b64(self, ws: Path) -> None:
        resp, code = um.register_dataset(ws, {"name": "d", "file_b64": _b64(b"x")})
        assert code == 400
        assert "filename is required when file_b64 is provided" in resp["error"]

    def test_400_no_source(self, ws: Path) -> None:
        resp, code = um.register_dataset(ws, {"name": "d"})
        assert code == 400
        assert "either file_b64, path, or url is required" in resp["error"]

    def test_404_investigation_not_found(self, ws: Path) -> None:
        resp, code = um.register_dataset(ws, {
            "name": "d", "file_b64": _b64(b"x"), "filename": "f.csv",
            "investigation": "ghost-inv",
        })
        assert code == 404
        assert "investigation 'ghost-inv' not found" in resp["error"]

    def test_409_duplicate(self, ws: Path) -> None:
        um.register_dataset(ws, {"name": "dup", "url": "https://x"})
        resp, code = um.register_dataset(ws, {"name": "dup", "url": "https://y"})
        assert code == 409
        assert "dataset 'dup' already registered" in resp["error"]


# ---------------------------------------------------------------------------
# register_expert_doc
# ---------------------------------------------------------------------------


class TestRegisterExpertDoc:
    def test_happy_file_b64_global(self, ws: Path) -> None:
        resp, code = um.register_expert_doc(ws, {
            "name": "oric notes", "file_b64": _b64(b"# notes\n"), "filename": "n.md",
        })
        assert code == 200, resp
        dest = ws / "references" / "expert" / "oric-notes.md"
        assert dest.is_file()
        docs = _read_ws(ws)["expert_docs"]
        assert docs[0]["name"] == "oric notes"
        assert docs[0]["path"] == "references/expert/oric-notes.md"
        assert len(docs[0]["sha256"]) == 64

    def test_happy_default_ext_pdf(self, ws: Path) -> None:
        um.register_expert_doc(ws, {
            "name": "noext", "file_b64": _b64(b"x"), "filename": "plainfile",
        })
        assert (ws / "references" / "expert" / "noext.pdf").is_file()

    def test_happy_source_path(self, ws: Path) -> None:
        src = ws / "src.md"
        src.write_text("# hi", encoding="utf-8")
        resp, code = um.register_expert_doc(ws, {"name": "doc", "source_path": "src.md"})
        assert code == 200, resp
        assert (ws / "references" / "expert" / "doc.md").is_file()
        assert _read_ws(ws)["expert_docs"][0]["name"] == "doc"

    def test_happy_metadata_fields(self, ws: Path) -> None:
        um.register_expert_doc(ws, {
            "name": "d", "file_b64": _b64(b"x"), "filename": "f.pdf",
            "description": "desc", "contributor": "alice",
            "claims_supported": "c1,c2",
        })
        entry = _read_ws(ws)["expert_docs"][0]
        assert entry["description"] == "desc"
        assert entry["contributor"] == "alice"
        assert entry["claims_supported"] == ["c1", "c2"]

    def test_happy_investigation_scoped(self, ws: Path) -> None:
        resp, code = um.register_expert_doc(ws, {
            "name": "oric-notes", "file_b64": _b64(b"# n\n"), "filename": "notes.md",
            "investigation": _INV_SLUG,
        })
        assert code == 200, resp
        assert (ws / "investigations" / _INV_SLUG / "inputs" / "expert"
                / "oric-notes.md").is_file()
        docs = _read_inv(ws)["inputs"]["expert_docs"]
        assert any(d.get("name") == "oric-notes" for d in docs)

    def test_400_invalid_investigation_slug(self, ws: Path) -> None:
        resp, code = um.register_expert_doc(ws, {
            "name": "d", "file_b64": _b64(b"x"), "filename": "f.pdf",
            "investigation": "Bad Slug",
        })
        assert code == 400
        assert "invalid investigation slug" in resp["error"]

    def test_400_missing_name(self, ws: Path) -> None:
        resp, code = um.register_expert_doc(ws, {
            "file_b64": _b64(b"x"), "filename": "f.pdf",
        })
        assert code == 400
        assert "name is required" in resp["error"]

    def test_400_no_source(self, ws: Path) -> None:
        resp, code = um.register_expert_doc(ws, {"name": "d"})
        assert code == 400
        assert "either file_b64+filename or source_path is required" in resp["error"]

    def test_400_filename_required_with_file_b64(self, ws: Path) -> None:
        # file_b64 alone (truthy) clears the no-source guard, then the file_b64
        # branch demands a filename — matching the legacy handler order.
        resp, code = um.register_expert_doc(ws, {"name": "d", "file_b64": _b64(b"x")})
        assert code == 400
        assert "filename is required when file_b64 is provided" in resp["error"]

    def test_400_filename_required_branch(self, ws: Path) -> None:
        # Provide source_path so the no-source guard passes, plus file_b64 with
        # no filename → the file_b64 branch's "filename is required" fires.
        resp, code = um.register_expert_doc(ws, {
            "name": "d", "file_b64": _b64(b"x"), "source_path": "ignored.md",
        })
        assert code == 400
        assert "filename is required when file_b64 is provided" in resp["error"]

    def test_400_source_path_does_not_exist(self, ws: Path) -> None:
        resp, code = um.register_expert_doc(ws, {"name": "d", "source_path": "nope.md"})
        assert code == 400
        assert "source_path does not exist" in resp["error"]

    def test_400_source_path_not_a_file(self, ws: Path) -> None:
        (ws / "adir").mkdir()
        resp, code = um.register_expert_doc(ws, {"name": "d", "source_path": "adir"})
        assert code == 400
        assert "source_path is not a regular file" in resp["error"]

    def test_404_investigation_not_found(self, ws: Path) -> None:
        resp, code = um.register_expert_doc(ws, {
            "name": "d", "file_b64": _b64(b"x"), "filename": "f.pdf",
            "investigation": "ghost-inv",
        })
        assert code == 404
        assert "investigation 'ghost-inv' not found" in resp["error"]

    def test_409_duplicate(self, ws: Path) -> None:
        um.register_expert_doc(ws, {"name": "dup", "file_b64": _b64(b"x"), "filename": "f.pdf"})
        resp, code = um.register_expert_doc(ws, {"name": "dup", "file_b64": _b64(b"y"), "filename": "g.pdf"})
        assert code == 409
        assert "expert doc 'dup' already registered" in resp["error"]


# ---------------------------------------------------------------------------
# register_import_entry
# ---------------------------------------------------------------------------


class TestRegisterImportEntry:
    def test_happy_reference_next_step(self, ws: Path) -> None:
        resp, code = um.register_import_entry(ws, {
            "name": "vEcoli", "source": "https://github.com/x/vEcoli",
            "ref": "main", "mode": "reference",
        })
        assert code == 200, resp
        assert resp["ok"] is True
        assert resp["next_terminal_step"] == "git submodule add https://github.com/x/vEcoli external/vEcoli"
        assert "git submodule add is NOT performed" in resp["note"]
        assert _read_ws(ws)["imports"]["vEcoli"]["mode"] == "reference"

    def test_happy_in_place_next_step(self, ws: Path) -> None:
        resp, code = um.register_import_entry(ws, {
            "name": "x", "source": "https://s", "ref": "main", "mode": "in-place",
        })
        assert code == 200
        assert resp["next_terminal_step"] == "git submodule add https://s external/x"

    def test_happy_fork_source_no_submodule(self, ws: Path) -> None:
        resp, code = um.register_import_entry(ws, {
            "name": "x", "source": "https://s", "ref": "main", "mode": "fork-source",
        })
        assert code == 200
        assert resp["next_terminal_step"] == "(fork-source: no submodule needed)"

    def test_400_missing_fields(self, ws: Path) -> None:
        resp, code = um.register_import_entry(ws, {"name": "x", "source": "s"})
        assert code == 400
        assert "name, source, ref, mode are required" in resp["error"]

    def test_400_invalid_mode(self, ws: Path) -> None:
        resp, code = um.register_import_entry(ws, {
            "name": "x", "source": "s", "ref": "main", "mode": "bogus",
        })
        assert code == 400
        assert "mode must be one of" in resp["error"]

    def test_400_invalid_name_chars(self, ws: Path) -> None:
        resp, code = um.register_import_entry(ws, {
            "name": "bad name!", "source": "s", "ref": "main", "mode": "reference",
        })
        assert code == 400
        assert "name must contain only word chars" in resp["error"]

    def test_409_duplicate(self, ws: Path) -> None:
        um.register_import_entry(ws, {"name": "dup", "source": "s", "ref": "m", "mode": "reference"})
        resp, code = um.register_import_entry(ws, {"name": "dup", "source": "s2", "ref": "m", "mode": "reference"})
        assert code == 409
        assert "already registered" in resp["error"]


# ---------------------------------------------------------------------------
# Behavioral commit-path tests (drive real server.Handler.* shims)
# ---------------------------------------------------------------------------


def _make_handler_and_capture():
    """Return (handler_instance, captured_dict). The handler's _json is replaced
    with a recorder that stores (resp, code) and returns resp."""
    import vivarium_dashboard.server as _srv
    handler = object.__new__(_srv.Handler)
    captured: dict[str, Any] = {}

    def _capture_json(resp, code):
        captured["resp"] = resp
        captured["code"] = code
        return resp

    handler._json = _capture_json  # type: ignore[method-assign]
    return handler, captured


class TestDatasetCommitPath:
    def test_shim_calls_active_branch_action_with_commit_msg(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        calls: dict[str, Any] = {}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            action_fn()
            return {"branch": "b", "commit": "c"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _recorder)
        handler, captured = _make_handler_and_capture()
        handler._post_dataset({"name": "ds", "url": "https://x"})
        assert calls["commit_msg"] == "feat(4): register dataset 'ds'"
        assert captured["code"] == 200

    def test_commit_msg_investigation_variant(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        calls: dict[str, Any] = {}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            action_fn()
            return {"branch": "b", "commit": "c"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _recorder)
        handler, captured = _make_handler_and_capture()
        handler._post_dataset({
            "name": "ds", "file_b64": _b64(b"x"), "filename": "f.csv",
            "investigation": _INV_SLUG,
        })
        assert calls["commit_msg"] == f"feat(4): register dataset 'ds' for investigation '{_INV_SLUG}'"

    def test_400_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_active_branch_action must NOT be called on 400")

        monkeypatch.setattr(_srv, "_active_branch_action", _boom)
        handler, captured = _make_handler_and_capture()
        handler._post_dataset({"url": "https://x"})  # missing name
        assert captured["code"] == 400
        assert "name is required" in captured["resp"]["error"]

    def test_action_reraises_on_lib_non_200(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _lib_fails(ws_root: Path, body: dict) -> "tuple[dict, int]":
            return {"error": "boom"}, 409

        monkeypatch.setattr(_srv._upload_mut, "register_dataset", _lib_fails)
        captured_action: dict[str, Any] = {}

        def _record_and_run(commit_message, action_fn):
            try:
                action_fn()
            except Exception as exc:
                captured_action["raised"] = exc
                return {"error": str(exc)}, 500
            return {"branch": "b"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _record_and_run)
        handler, captured = _make_handler_and_capture()
        handler._post_dataset({"name": "x", "url": "https://x"})
        assert "raised" in captured_action
        assert captured["code"] == 500


class TestExpertDocCommitPath:
    def test_shim_calls_active_branch_action_with_commit_msg(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        calls: dict[str, Any] = {}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            action_fn()
            return {"branch": "b", "commit": "c"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _recorder)
        handler, captured = _make_handler_and_capture()
        handler._post_expert_doc({"name": "doc", "file_b64": _b64(b"x"), "filename": "f.pdf"})
        assert calls["commit_msg"] == "feat(5): add expert document 'doc'"
        assert captured["code"] == 200

    def test_400_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_active_branch_action must NOT be called on 400")

        monkeypatch.setattr(_srv, "_active_branch_action", _boom)
        handler, captured = _make_handler_and_capture()
        handler._post_expert_doc({"name": "d"})  # no source
        assert captured["code"] == 400
        assert "either file_b64+filename or source_path is required" in captured["resp"]["error"]

    def test_action_reraises_on_lib_non_200(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _lib_fails(ws_root: Path, body: dict) -> "tuple[dict, int]":
            return {"error": "boom"}, 409

        monkeypatch.setattr(_srv._upload_mut, "register_expert_doc", _lib_fails)
        captured_action: dict[str, Any] = {}

        def _record_and_run(commit_message, action_fn):
            try:
                action_fn()
            except Exception as exc:
                captured_action["raised"] = exc
                return {"error": str(exc)}, 500
            return {"branch": "b"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _record_and_run)
        handler, captured = _make_handler_and_capture()
        handler._post_expert_doc({"name": "x", "file_b64": _b64(b"y"), "filename": "f.pdf"})
        assert "raised" in captured_action
        assert captured["code"] == 500


class TestImportCommitPath:
    def test_shim_calls_active_branch_action_with_commit_msg_and_shapes_resp(
        self, ws: Path, monkeypatch: Any
    ) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        calls: dict[str, Any] = {}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            action_fn()
            return {"branch": "b", "commit": "c"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _recorder)
        handler, captured = _make_handler_and_capture()
        handler._post_import({
            "name": "vEcoli", "source": "https://s", "ref": "main", "mode": "reference",
        })
        assert calls["commit_msg"] == "feat(0.5): register import 'vEcoli' (mode=reference)"
        # The shim's post-action shaping (next_terminal_step / note) still fires.
        assert captured["resp"]["next_terminal_step"] == "git submodule add https://s external/vEcoli"
        assert "git submodule add is NOT performed" in captured["resp"]["note"]

    def test_400_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_active_branch_action must NOT be called on 400")

        monkeypatch.setattr(_srv, "_active_branch_action", _boom)
        handler, captured = _make_handler_and_capture()
        handler._post_import({"name": "x", "source": "s"})  # missing ref/mode
        assert captured["code"] == 400
        assert "name, source, ref, mode are required" in captured["resp"]["error"]

    def test_action_reraises_on_lib_non_200(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _lib_fails(ws_root: Path, body: dict) -> "tuple[dict, int]":
            return {"error": "boom"}, 409

        monkeypatch.setattr(_srv._upload_mut, "register_import_entry", _lib_fails)
        captured_action: dict[str, Any] = {}

        def _record_and_run(commit_message, action_fn):
            try:
                action_fn()
            except Exception as exc:
                captured_action["raised"] = exc
                return {"error": str(exc)}, 500
            return {"branch": "b"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _record_and_run)
        handler, captured = _make_handler_and_capture()
        handler._post_import({"name": "x", "source": "s", "ref": "m", "mode": "reference"})
        assert "raised" in captured_action
        assert captured["code"] == 500


# ---------------------------------------------------------------------------
# FastAPI route tests
# ---------------------------------------------------------------------------


class TestDatasetRoute:
    def test_happy_path(self, client: TestClient, ws: Path) -> None:
        resp = client.post("/api/dataset", json={
            "name": "ds", "file_b64": _b64(b"a,b\n"), "filename": "d.csv",
        })
        assert resp.status_code == 200, resp.json()
        assert resp.json()["ok"] is True
        assert (ws / "datasets" / "ds" / "d.csv").is_file()
        assert _read_ws(ws)["datasets"][0]["name"] == "ds"

    def test_400_missing_name(self, client: TestClient) -> None:
        resp = client.post("/api/dataset", json={"url": "https://x"})
        assert resp.status_code == 400
        assert "name is required" in resp.json().get("error", "")

    def test_409_duplicate(self, client: TestClient) -> None:
        client.post("/api/dataset", json={"name": "dup", "url": "https://x"})
        resp = client.post("/api/dataset", json={"name": "dup", "url": "https://y"})
        assert resp.status_code == 409

    def test_dataset_in_openapi(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/dataset" in paths and "post" in paths["/api/dataset"]


class TestExpertDocRoute:
    def test_happy_path(self, client: TestClient, ws: Path) -> None:
        resp = client.post("/api/expert-doc", json={
            "name": "doc", "file_b64": _b64(b"# x\n"), "filename": "n.md",
        })
        assert resp.status_code == 200, resp.json()
        assert (ws / "references" / "expert" / "doc.md").is_file()
        assert _read_ws(ws)["expert_docs"][0]["name"] == "doc"

    def test_400_no_source(self, client: TestClient) -> None:
        resp = client.post("/api/expert-doc", json={"name": "d"})
        assert resp.status_code == 400
        assert "either file_b64+filename or source_path is required" in resp.json().get("error", "")

    def test_expert_doc_in_openapi(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/expert-doc" in paths and "post" in paths["/api/expert-doc"]


class TestImportRoute:
    def test_happy_path(self, client: TestClient, ws: Path) -> None:
        resp = client.post("/api/import", json={
            "name": "vEcoli", "source": "https://s", "ref": "main", "mode": "reference",
        })
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["ok"] is True
        assert data["next_terminal_step"] == "git submodule add https://s external/vEcoli"
        assert _read_ws(ws)["imports"]["vEcoli"]["mode"] == "reference"

    def test_400_missing_fields(self, client: TestClient) -> None:
        resp = client.post("/api/import", json={"name": "x", "source": "s"})
        assert resp.status_code == 400
        assert "name, source, ref, mode are required" in resp.json().get("error", "")

    def test_409_duplicate(self, client: TestClient) -> None:
        client.post("/api/import", json={"name": "dup", "source": "s", "ref": "m", "mode": "reference"})
        resp = client.post("/api/import", json={"name": "dup", "source": "s2", "ref": "m", "mode": "reference"})
        assert resp.status_code == 409

    def test_import_in_openapi(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/import" in paths and "post" in paths["/api/import"]
