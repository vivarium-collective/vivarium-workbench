"""Tests for lib.composite_mutations — composite POST pure builders.

Covers (per builder):
  - Happy paths: sidecar / catalog file writes + spec ``composites`` / ``variants``
    mutation + ``(dict, status)`` return.
  - Every 400/404/409 validation path (incl. promote's ``name`` / ``path``
    augmentation, perturb's replace-in-place, rebuild's not-derived 400).
  - The composite-add generator path is exercised by stubbing
    ``_resolve_composite_source_or_generate`` / ``materialize_generator_doc``;
    the YAML-source path uses a real on-disk ``*.composite.yaml``.

Behavioral commit-path tests: drive the REAL ``server._post_*`` handlers with
``server._commit_or_run`` monkeypatched to a recorder, asserting:
  (a) ``_commit_or_run`` IS called with the exact commit_msg,
  (b) validation 400/404/409 returns BEFORE the wrapper is ever called,
  (c) the inner do_action re-raises (→ workstream error 500) when the mutation
      fails inside the wrapper, and the promote post-wrapper augmentation.
NOT inspect.getsource, NOT ``assert ... or True``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import types

from vivarium_dashboard.lib import composite_mutations as cm
from vivarium_dashboard.lib import investigation_migrate as _imig
from vivarium_dashboard.lib import composite_recipes as _recipes
from vivarium_dashboard.lib import composite_lookup as _clookup


_INV = "demo"


def _make_ws(tmp_path: Path) -> Path:
    w = tmp_path / "ws"
    w.mkdir()
    (w / "workspace.yaml").write_text(
        "schema_version: 3\nname: testws\npackage_path: pbg_testws\n",
        encoding="utf-8",
    )
    return w


def _make_inv(ws: Path, spec: dict) -> Path:
    inv = ws / "investigations" / _INV
    inv.mkdir(parents=True, exist_ok=True)
    (inv / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return inv


def _make_source(ws: Path, stem: str, doc: dict) -> str:
    """Write a real YAML composite source and return its dotted ref."""
    cdir = ws / "pbg_testws" / "composites"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / f"{stem}.composite.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return f"pbg_testws.composites.{stem}"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return _make_ws(tmp_path)


def _read(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# add_investigation_composite
# ---------------------------------------------------------------------------


class TestAddInvestigationComposite:
    def test_happy_yaml_source(self, ws: Path) -> None:
        ref = _make_source(ws, "baseline", {"name": "baseline-doc", "state": {}})
        inv = _make_inv(ws, {"name": _INV, "composites": [], "runs": []})
        resp, code = cm.add_investigation_composite(
            ws, {"investigation": _INV, "name": "base", "source": ref}
        )
        assert code == 200, resp
        assert resp == {"ok": True}
        sidecar = inv / "composites" / "base.yaml"
        assert sidecar.is_file()
        assert _read(sidecar)["name"] == "baseline-doc"
        spec = _read(inv / "spec.yaml")
        assert spec["composites"][0] == {
            "name": "base", "source": ref, "document": "./composites/base.yaml",
        }

    def test_happy_generator_source(self, ws: Path, monkeypatch: Any) -> None:
        _make_inv(ws, {"name": _INV, "composites": []})
        monkeypatch.setattr(
            _imig, "_resolve_composite_source_or_generate",
            lambda src, root: (None, True, "gen"),
        )
        monkeypatch.setattr(
            _imig, "materialize_generator_doc",
            lambda src: {"name": "gen", "state": {"x": 1}},
        )
        resp, code = cm.add_investigation_composite(
            ws, {"investigation": _INV, "name": "g", "source": "pbg_x.composites.gen"}
        )
        assert code == 200, resp
        sidecar = ws / "investigations" / _INV / "composites" / "g.yaml"
        assert _read(sidecar) == {"name": "gen", "state": {"x": 1}}

    def test_400_missing_fields(self, ws: Path) -> None:
        resp, code = cm.add_investigation_composite(ws, {"investigation": _INV})
        assert code == 400
        assert resp["error"] == "investigation, name, source required"

    def test_404_unknown_source(self, ws: Path) -> None:
        _make_inv(ws, {"name": _INV, "composites": []})
        resp, code = cm.add_investigation_composite(
            ws, {"investigation": _INV, "name": "x",
                 "source": "pbg_testws.composites.nope"}
        )
        assert code == 404

    def test_404_investigation_not_found(self, ws: Path) -> None:
        ref = _make_source(ws, "baseline", {"name": "b", "state": {}})
        resp, code = cm.add_investigation_composite(
            ws, {"investigation": "ghost", "name": "x", "source": ref}
        )
        assert code == 404
        assert resp["error"] == "investigation not found"

    def test_409_duplicate(self, ws: Path) -> None:
        ref = _make_source(ws, "baseline", {"name": "b", "state": {}})
        inv = _make_inv(ws, {"name": _INV, "composites": []})
        (inv / "composites").mkdir()
        (inv / "composites" / "base.yaml").write_text("name: b\n", encoding="utf-8")
        resp, code = cm.add_investigation_composite(
            ws, {"investigation": _INV, "name": "base", "source": ref}
        )
        assert code == 409
        assert "already exists" in resp["error"]

    def test_400_generator_not_serializable(self, ws: Path, monkeypatch: Any) -> None:
        _make_inv(ws, {"name": _INV, "composites": []})
        monkeypatch.setattr(
            _imig, "_resolve_composite_source_or_generate",
            lambda src, root: (None, True, "gen"),
        )

        def _boom(src):
            raise RuntimeError("live Process can't be dumped")

        monkeypatch.setattr(_imig, "materialize_generator_doc", _boom)
        resp, code = cm.add_investigation_composite(
            ws, {"investigation": _INV, "name": "g", "source": "pbg_x.composites.gen"}
        )
        assert code == 400
        assert "can't be serialized as a YAML sidecar" in resp["error"]


# ---------------------------------------------------------------------------
# perturb_investigation_composite
# ---------------------------------------------------------------------------


def _seed_parent(ws: Path, *, with_proc: bool = False) -> Path:
    inv = ws / "investigations" / _INV
    (inv / "composites").mkdir(parents=True, exist_ok=True)
    state = {"replication": {"_type": "process", "address": "local:Foo",
                             "config": {"rate": 1.0}}} if with_proc else {}
    (inv / "composites" / "baseline.yaml").write_text(
        yaml.safe_dump({"name": "baseline-doc",
                        "state": state,
                        "parameters": {"rate": {"default": 1.0}}}),
        encoding="utf-8",
    )
    return inv


class TestPerturbInvestigationComposite:
    def test_happy_parameter_override(self, ws: Path) -> None:
        inv = _seed_parent(ws)
        _make_inv(ws, {"name": _INV,
                       "variants": [{"name": "baseline", "document": "./composites/baseline.yaml"}]})
        # re-seed parent (spec write above overwrote nothing under composites/)
        resp, code = cm.perturb_investigation_composite(ws, {
            "investigation": _INV, "name": "high-rate", "extends": "baseline",
            "description": "Doubled rate", "parameter_overrides": {"rate": 2.0},
        })
        assert code == 200, resp
        derived = inv / "composites" / "high-rate.yaml"
        assert _read(derived)["parameters"]["rate"]["default"] == 2.0
        spec = _read(inv / "spec.yaml")
        entry = next(v for v in spec["variants"] if v["name"] == "high-rate")
        assert entry["extends"] == "baseline"
        assert entry["intervention"]["parameter_overrides"] == {"rate": 2.0}
        assert entry["intervention"]["description"] == "Doubled rate"

    def test_accepts_study_key(self, ws: Path) -> None:
        inv = _seed_parent(ws)
        _make_inv(ws, {"name": _INV, "variants": []})
        resp, code = cm.perturb_investigation_composite(ws, {
            "study": _INV, "name": "v", "extends": "baseline",
        })
        assert code == 200, resp
        # description-only (no overrides) → no intervention block attached
        spec = _read(inv / "spec.yaml")
        entry = next(v for v in spec["variants"] if v["name"] == "v")
        assert "intervention" not in entry

    def test_replace_in_place(self, ws: Path) -> None:
        inv = _seed_parent(ws)
        _make_inv(ws, {"name": _INV, "variants": [
            {"name": "v", "extends": "baseline", "document": "./composites/v.yaml",
             "stale": "old"},
        ]})
        resp, code = cm.perturb_investigation_composite(ws, {
            "investigation": _INV, "name": "v", "extends": "baseline",
            "parameter_overrides": {"rate": 3.0},
        })
        assert code == 200, resp
        spec = _read(inv / "spec.yaml")
        vs = [v for v in spec["variants"] if v["name"] == "v"]
        assert len(vs) == 1
        assert "stale" not in vs[0]  # full replace
        assert vs[0]["intervention"]["parameter_overrides"] == {"rate": 3.0}

    def test_400_missing_fields(self, ws: Path) -> None:
        resp, code = cm.perturb_investigation_composite(ws, {"investigation": _INV, "name": "v"})
        assert code == 400
        assert resp["error"] == "investigation, name, extends required"

    def test_404_investigation(self, ws: Path) -> None:
        resp, code = cm.perturb_investigation_composite(ws, {
            "investigation": "ghost", "name": "v", "extends": "baseline",
        })
        assert code == 404
        assert resp["error"] == "investigation not found"

    def test_404_parent_missing(self, ws: Path) -> None:
        _make_inv(ws, {"name": _INV, "variants": []})
        resp, code = cm.perturb_investigation_composite(ws, {
            "investigation": _INV, "name": "v", "extends": "nope",
        })
        assert code == 404
        assert "parent composite 'nope' not found" in resp["error"]

    def test_400_override_keyerror(self, ws: Path) -> None:
        _seed_parent(ws, with_proc=True)
        _make_inv(ws, {"name": _INV, "variants": []})
        resp, code = cm.perturb_investigation_composite(ws, {
            "investigation": _INV, "name": "v", "extends": "baseline",
            "process_overrides": {"ghost_proc": None},
        })
        assert code == 400
        assert "override failed" in resp["error"]


# ---------------------------------------------------------------------------
# promote_composite_to_catalog
# ---------------------------------------------------------------------------


def _seed_variant(ws: Path) -> Path:
    inv = ws / "investigations" / _INV
    (inv / "composites").mkdir(parents=True, exist_ok=True)
    (inv / "composites" / "myvar.yaml").write_text(
        yaml.safe_dump({"name": "myvar-doc", "state": {"a": 1}}), encoding="utf-8"
    )
    return inv


class TestPromoteCompositeToCatalog:
    def test_happy_with_augmentation(self, ws: Path) -> None:
        inv = _seed_variant(ws)
        _make_inv(ws, {"name": _INV, "variants": [{"name": "myvar"}]})
        _seed_variant(ws)
        resp, code = cm.promote_composite_to_catalog(ws, {
            "investigation": _INV, "variant": "myvar", "target_name": "promoted-x",
            "description": "Promoted!",
        })
        assert code == 200, resp
        assert resp["name"] == "promoted-x"
        assert resp["path"] == "pbg_testws/composites/promoted-x.composite.yaml"
        catalog = ws / "pbg_testws" / "composites" / "promoted-x.composite.yaml"
        doc = _read(catalog)
        assert doc["name"] == "promoted-x"
        assert doc["description"] == "Promoted!"
        spec = _read(inv / "spec.yaml")
        assert spec["variants"][0]["promoted"] is True

    def test_target_defaults_to_variant(self, ws: Path) -> None:
        _seed_variant(ws)
        _make_inv(ws, {"name": _INV, "variants": [{"name": "myvar"}]})
        _seed_variant(ws)
        resp, code = cm.promote_composite_to_catalog(ws, {
            "investigation": _INV, "variant": "myvar",
        })
        assert code == 200, resp
        assert resp["name"] == "myvar"

    def test_400_missing(self, ws: Path) -> None:
        resp, code = cm.promote_composite_to_catalog(ws, {"investigation": _INV})
        assert code == 400
        assert resp["error"] == "investigation, variant required"

    def test_404_investigation(self, ws: Path) -> None:
        resp, code = cm.promote_composite_to_catalog(ws, {
            "investigation": "ghost", "variant": "myvar",
        })
        assert code == 404
        assert "not found" in resp["error"]

    def test_404_variant_sidecar_missing(self, ws: Path) -> None:
        _make_inv(ws, {"name": _INV, "variants": []})
        resp, code = cm.promote_composite_to_catalog(ws, {
            "investigation": _INV, "variant": "ghostvar",
        })
        assert code == 404
        assert "sidecar not found" in resp["error"]

    def test_409_target_exists(self, ws: Path) -> None:
        _seed_variant(ws)
        _make_inv(ws, {"name": _INV, "variants": [{"name": "myvar"}]})
        _seed_variant(ws)
        cat = ws / "pbg_testws" / "composites"
        cat.mkdir(parents=True, exist_ok=True)
        (cat / "myvar.composite.yaml").write_text("name: x\n", encoding="utf-8")
        resp, code = cm.promote_composite_to_catalog(ws, {
            "investigation": _INV, "variant": "myvar",
        })
        assert code == 409
        assert "already exists" in resp["error"]


# ---------------------------------------------------------------------------
# rebuild_investigation_composite
# ---------------------------------------------------------------------------


class TestRebuildInvestigationComposite:
    def test_happy(self, ws: Path) -> None:
        inv = _seed_parent(ws)
        _make_inv(ws, {"name": _INV, "composites": [
            {"name": "high-rate", "extends": "baseline",
             "parameter_overrides": {"rate": 5.0}},
        ]})
        _seed_parent(ws)
        resp, code = cm.rebuild_investigation_composite(ws, {
            "investigation": _INV, "name": "high-rate",
        })
        assert code == 200, resp
        derived = inv / "composites" / "high-rate.yaml"
        assert _read(derived)["parameters"]["rate"]["default"] == 5.0

    def test_400_missing(self, ws: Path) -> None:
        resp, code = cm.rebuild_investigation_composite(ws, {"investigation": _INV})
        assert code == 400
        assert resp["error"] == "investigation, name required"

    def test_404_investigation(self, ws: Path) -> None:
        resp, code = cm.rebuild_investigation_composite(ws, {
            "investigation": "ghost", "name": "x",
        })
        assert code == 404
        assert resp["error"] == "investigation not found"

    def test_404_composite_not_found(self, ws: Path) -> None:
        _make_inv(ws, {"name": _INV, "composites": []})
        resp, code = cm.rebuild_investigation_composite(ws, {
            "investigation": _INV, "name": "ghost",
        })
        assert code == 404
        assert "composite 'ghost' not found" in resp["error"]

    def test_400_not_derived(self, ws: Path) -> None:
        _make_inv(ws, {"name": _INV, "composites": [{"name": "flat"}]})
        resp, code = cm.rebuild_investigation_composite(ws, {
            "investigation": _INV, "name": "flat",
        })
        assert code == 400
        assert "is not derived" in resp["error"]

    def test_404_parent_missing(self, ws: Path) -> None:
        _make_inv(ws, {"name": _INV, "composites": [
            {"name": "d", "extends": "gone"},
        ]})
        resp, code = cm.rebuild_investigation_composite(ws, {
            "investigation": _INV, "name": "d",
        })
        assert code == 404
        assert "document missing" in resp["error"]


# ---------------------------------------------------------------------------
# Behavioral commit-path tests (drive real server.Handler.* shims)
# ---------------------------------------------------------------------------


def _make_handler_and_capture():
    import vivarium_dashboard.server as _srv
    handler = object.__new__(_srv.Handler)
    captured: dict[str, Any] = {}

    def _capture_json(resp, code):
        captured["resp"] = resp
        captured["code"] = code
        return resp

    handler._json = _capture_json  # type: ignore[method-assign]
    return handler, captured


def _recorder_factory(calls: dict):
    def _recorder(commit_message, action_fn):
        calls["commit_msg"] = commit_message
        action_fn()
        return {"branch": "b", "commit": "c"}, 200
    return _recorder


class TestCompositeAddCommitPath:
    def test_commit_msg_and_delegation(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        ref = _make_source(ws, "baseline", {"name": "b", "state": {}})
        inv = _make_inv(ws, {"name": _INV, "composites": []})
        calls: dict[str, Any] = {}
        monkeypatch.setattr(_srv, "_commit_or_run", _recorder_factory(calls))
        handler, captured = _make_handler_and_capture()
        handler._post_investigation_composite_add({
            "investigation": _INV, "name": "base", "source": ref,
        })
        assert calls["commit_msg"] == f"feat(investigations/{_INV}): add composite 'base'"
        assert captured["code"] == 200
        assert (inv / "composites" / "base.yaml").is_file()

    def test_400_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_commit_or_run must NOT be called on 400")

        monkeypatch.setattr(_srv, "_commit_or_run", _boom)
        handler, captured = _make_handler_and_capture()
        handler._post_investigation_composite_add({"investigation": _INV})
        assert captured["code"] == 400

    def test_do_action_failure_is_500(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        ref = _make_source(ws, "baseline", {"name": "b", "state": {}})
        _make_inv(ws, {"name": _INV, "composites": []})
        captured_action: dict[str, Any] = {}

        def _record_and_run(commit_message, action_fn):
            # Simulate the wrapper surfacing an inner mutation failure.
            try:
                action_fn()
            except Exception as exc:  # noqa: BLE001
                captured_action["raised"] = exc
                raise
            return {"branch": "b"}, 200

        # Make the _apply mutation raise inside the wrapper.
        def _apply_boom(*a, **k):
            raise RuntimeError("disk full")

        monkeypatch.setattr(_srv._composite_mut, "_apply_add_investigation_composite", _apply_boom)
        monkeypatch.setattr(_srv, "_commit_or_run", _record_and_run)
        handler, captured = _make_handler_and_capture()
        handler._post_investigation_composite_add({
            "investigation": _INV, "name": "base", "source": ref,
        })
        assert "raised" in captured_action
        assert captured["code"] == 500
        assert "workstream error" in captured["resp"]["error"]


class TestCompositePerturbCommitPath:
    def test_commit_msg(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        _seed_parent(ws)
        _make_inv(ws, {"name": _INV, "variants": []})
        calls: dict[str, Any] = {}
        monkeypatch.setattr(_srv, "_commit_or_run", _recorder_factory(calls))
        handler, captured = _make_handler_and_capture()
        handler._post_investigation_composite_perturb({
            "investigation": _INV, "name": "v", "extends": "baseline",
        })
        assert calls["commit_msg"] == (
            f"feat(investigations/{_INV}): derive composite 'v' from 'baseline'"
        )
        assert captured["code"] == 200

    def test_404_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("must not be called on 404")

        monkeypatch.setattr(_srv, "_commit_or_run", _boom)
        handler, captured = _make_handler_and_capture()
        handler._post_investigation_composite_perturb({
            "investigation": "ghost", "name": "v", "extends": "baseline",
        })
        assert captured["code"] == 404


class TestPromoteCommitPath:
    def test_commit_msg_and_post_wrapper_augmentation(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        _seed_variant(ws)
        _make_inv(ws, {"name": _INV, "variants": [{"name": "myvar"}]})
        _seed_variant(ws)
        calls: dict[str, Any] = {}
        monkeypatch.setattr(_srv, "_commit_or_run", _recorder_factory(calls))
        handler, captured = _make_handler_and_capture()
        handler._post_composite_promote_to_catalog({
            "investigation": _INV, "variant": "myvar", "target_name": "pp",
        })
        assert calls["commit_msg"] == (
            f"feat(catalog): promote 'myvar' from investigations/{_INV} as 'pp'"
        )
        assert captured["code"] == 200
        # post-wrapper augmentation survives on the live path.
        assert captured["resp"]["name"] == "pp"
        assert captured["resp"]["path"] == "pbg_testws/composites/pp.composite.yaml"

    def test_409_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        _seed_variant(ws)
        _make_inv(ws, {"name": _INV, "variants": [{"name": "myvar"}]})
        _seed_variant(ws)
        cat = ws / "pbg_testws" / "composites"
        cat.mkdir(parents=True, exist_ok=True)
        (cat / "myvar.composite.yaml").write_text("name: x\n", encoding="utf-8")

        def _boom(commit_message, action_fn):
            raise AssertionError("must not be called on 409")

        monkeypatch.setattr(_srv, "_commit_or_run", _boom)
        handler, captured = _make_handler_and_capture()
        handler._post_composite_promote_to_catalog({
            "investigation": _INV, "variant": "myvar",
        })
        assert captured["code"] == 409


# ---------------------------------------------------------------------------
# create_from_composite (parity)
# ---------------------------------------------------------------------------


def _fixed_uuid(hex_value: str = "abcdef"):
    return lambda: types.SimpleNamespace(hex=hex_value + "000000")


def _stub_catalog(monkeypatch: Any, catalog: dict) -> None:
    monkeypatch.setattr(_clookup, "discover_all_composites", lambda root, pkg: catalog)


class TestCreateFromComposite:
    def test_happy_yaml_source(self, ws: Path, monkeypatch: Any) -> None:
        ref = _make_source(ws, "chromo", {"name": "chromo-doc", "state": {}})
        src_path = ws / "pbg_testws" / "composites" / "chromo.composite.yaml"
        _stub_catalog(monkeypatch, {
            ref: {"name": "chromo", "id": ref, "kind": "spec", "_path": str(src_path)},
        })
        monkeypatch.setattr(
            _imig, "_resolve_composite_source",
            lambda r, root: (src_path, "chromo"),
        )
        monkeypatch.setattr(cm.uuid, "uuid4", _fixed_uuid("abcdef"))

        resp, code = cm.create_from_composite(ws, {"composite_name": "chromo"})
        assert code == 200, resp
        assert resp == {"name": "study-chromo-abcdef"}
        sdir = ws / "studies" / "study-chromo-abcdef"
        spec = _read(sdir / "spec.yaml")
        assert spec["name"] == "study-chromo-abcdef"
        assert spec["baseline"] == "chromo"
        assert spec["variants"] == [{
            "name": "chromo", "source": ref,
            "document": "./composites/chromo.yaml",
        }]
        assert spec["comparisons"] == []
        assert spec["status"] == "draft"
        sidecar = sdir / "composites" / "chromo.yaml"
        assert _read(sidecar)["name"] == "chromo-doc"

    def test_match_by_id_stem(self, ws: Path, monkeypatch: Any) -> None:
        # Catalog record's YAML name differs; matched via the id-stem instead.
        ref = _make_source(ws, "widget", {"name": "totally-different", "state": {}})
        src_path = ws / "pbg_testws" / "composites" / "widget.composite.yaml"
        _stub_catalog(monkeypatch, {
            ref: {"name": "totally-different", "id": ref, "kind": "spec",
                  "_path": str(src_path)},
        })
        monkeypatch.setattr(
            _imig, "_resolve_composite_source",
            lambda r, root: (src_path, "widget"),
        )
        monkeypatch.setattr(cm.uuid, "uuid4", _fixed_uuid("111111"))
        resp, code = cm.create_from_composite(ws, {"composite_name": "widget"})
        assert code == 200, resp
        assert resp["name"] == "study-widget-111111"

    def test_happy_generator_source(self, ws: Path, monkeypatch: Any) -> None:
        ref = "pbg_testws.composites.gen"
        _stub_catalog(monkeypatch, {
            ref: {"name": "gen", "id": ref, "kind": "generator"},
        })
        import pbg_superpowers.composite_generator as _cg
        monkeypatch.setattr(_cg, "_REGISTRY", {ref: object()}, raising=False)
        monkeypatch.setattr(_cg, "build_generator",
                            lambda entry: {"name": "gen", "state": {"x": 1}})
        monkeypatch.setattr(cm.uuid, "uuid4", _fixed_uuid("222222"))
        resp, code = cm.create_from_composite(ws, {"composite_name": "gen"})
        assert code == 200, resp
        sdir = ws / "studies" / "study-gen-222222"
        assert _read(sdir / "composites" / "gen.yaml") == {"name": "gen", "state": {"x": 1}}

    def test_400_blank(self, ws: Path) -> None:
        resp, code = cm.create_from_composite(ws, {"composite_name": ""})
        assert code == 400
        assert resp["error"] == "composite_name required"

    def test_404_not_in_catalog(self, ws: Path, monkeypatch: Any) -> None:
        _stub_catalog(monkeypatch, {})
        resp, code = cm.create_from_composite(ws, {"composite_name": "ghost"})
        assert code == 404
        assert "not in workspace catalog" in resp["error"]

    def test_409_collision(self, ws: Path, monkeypatch: Any) -> None:
        ref = _make_source(ws, "chromo", {"name": "chromo-doc", "state": {}})
        src_path = ws / "pbg_testws" / "composites" / "chromo.composite.yaml"
        _stub_catalog(monkeypatch, {
            ref: {"name": "chromo", "id": ref, "kind": "spec", "_path": str(src_path)},
        })
        monkeypatch.setattr(
            _imig, "_resolve_composite_source",
            lambda r, root: (src_path, "chromo"),
        )
        monkeypatch.setattr(cm.uuid, "uuid4", _fixed_uuid("abcdef"))
        (ws / "studies" / "study-chromo-abcdef").mkdir(parents=True)
        resp, code = cm.create_from_composite(ws, {"composite_name": "chromo"})
        assert code == 409
        assert "already exists" in resp["error"]

    def test_404_generator_build_failed(self, ws: Path, monkeypatch: Any) -> None:
        ref = "pbg_testws.composites.gen"
        _stub_catalog(monkeypatch, {ref: {"name": "gen", "id": ref, "kind": "generator"}})
        import pbg_superpowers.composite_generator as _cg
        monkeypatch.setattr(_cg, "_REGISTRY", {ref: object()}, raising=False)

        def _boom(entry):
            raise RuntimeError("nope")

        monkeypatch.setattr(_cg, "build_generator", _boom)
        monkeypatch.setattr(cm.uuid, "uuid4", _fixed_uuid("333333"))
        resp, code = cm.create_from_composite(ws, {"composite_name": "gen"})
        assert code == 400
        assert "generator build failed" in resp["error"]


# ---------------------------------------------------------------------------
# create_from_composite (behavioral commit-path — drive real server shim)
# ---------------------------------------------------------------------------


class TestCreateFromCompositeCommitPath:
    def _setup(self, ws: Path, monkeypatch: Any):
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        ref = _make_source(ws, "chromo", {"name": "chromo-doc", "state": {}})
        src_path = ws / "pbg_testws" / "composites" / "chromo.composite.yaml"
        _stub_catalog(monkeypatch, {
            ref: {"name": "chromo", "id": ref, "kind": "spec", "_path": str(src_path)},
        })
        monkeypatch.setattr(
            _imig, "_resolve_composite_source",
            lambda r, root: (src_path, "chromo"),
        )
        # uuid in BOTH the server module and the lib module namespaces.
        monkeypatch.setattr(_srv.uuid, "uuid4", _fixed_uuid("abcdef"))
        monkeypatch.setattr(cm.uuid, "uuid4", _fixed_uuid("abcdef"))
        return _srv, ref

    def test_commit_msg_and_post_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        _srv, ref = self._setup(ws, monkeypatch)
        calls: dict[str, Any] = {}
        monkeypatch.setattr(_srv, "_commit_or_run", _recorder_factory(calls))
        handler, captured = _make_handler_and_capture()
        handler._post_study_create_from_composite({"composite_name": "chromo"})
        assert calls["commit_msg"] == (
            "feat(investigations/study-chromo-abcdef): create from composite 'chromo'"
        )
        assert captured["code"] == 200
        # Post-wrapper REPLACES resp with {"name": auto_name}.
        assert captured["resp"] == {"name": "study-chromo-abcdef"}
        assert (ws / "studies" / "study-chromo-abcdef" / "spec.yaml").is_file()

    def test_400_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_commit_or_run must NOT be called on 400")

        monkeypatch.setattr(_srv, "_commit_or_run", _boom)
        handler, captured = _make_handler_and_capture()
        handler._post_study_create_from_composite({"composite_name": ""})
        assert captured["code"] == 400

    def test_do_action_failure_is_500(self, ws: Path, monkeypatch: Any) -> None:
        _srv, ref = self._setup(ws, monkeypatch)
        captured_action: dict[str, Any] = {}

        def _record_and_run(commit_message, action_fn):
            try:
                action_fn()
            except Exception as exc:  # noqa: BLE001
                captured_action["raised"] = exc
                raise
            return {"branch": "b"}, 200

        def _apply_boom(*a, **k):
            raise RuntimeError("disk full")

        monkeypatch.setattr(_srv._composite_mut, "_apply_create_from_composite", _apply_boom)
        monkeypatch.setattr(_srv, "_commit_or_run", _record_and_run)
        handler, captured = _make_handler_and_capture()
        handler._post_study_create_from_composite({"composite_name": "chromo"})
        assert "raised" in captured_action
        assert captured["code"] == 500
        assert "workstream error" in captured["resp"]["error"]


class TestRebuildCommitPath:
    def test_commit_msg(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        _seed_parent(ws)
        _make_inv(ws, {"name": _INV, "composites": [
            {"name": "d", "extends": "baseline", "parameter_overrides": {"rate": 9.0}},
        ]})
        _seed_parent(ws)
        calls: dict[str, Any] = {}
        monkeypatch.setattr(_srv, "_commit_or_run", _recorder_factory(calls))
        handler, captured = _make_handler_and_capture()
        handler._post_investigation_composite_rebuild({
            "investigation": _INV, "name": "d",
        })
        assert calls["commit_msg"] == (
            f"chore(investigations/{_INV}): rebuild composite 'd'"
        )
        assert captured["code"] == 200

    def test_400_not_derived_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        _make_inv(ws, {"name": _INV, "composites": [{"name": "flat"}]})

        def _boom(commit_message, action_fn):
            raise AssertionError("must not be called on 400")

        monkeypatch.setattr(_srv, "_commit_or_run", _boom)
        handler, captured = _make_handler_and_capture()
        handler._post_investigation_composite_rebuild({
            "investigation": _INV, "name": "flat",
        })
        assert captured["code"] == 400
        assert "is not derived" in captured["resp"]["error"]
