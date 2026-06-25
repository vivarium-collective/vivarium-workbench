"""Parity and unit tests for vivarium_dashboard.lib.investigation_views.

Tests verify that the lib builders return the expected dict shapes, and that
the legacy stdlib server.py handler shims produce identical bodies to the lib
builders on the same fixtures (``TestServerShimParity``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from vivarium_dashboard.lib import investigation_views as inv_views


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_workspace(tmp_path: Path) -> Path:
    """Create a minimal fixture workspace for the investigation-views builders.

    Layout::

        <tmp_path>/
          investigations/
            my-inv/
              investigation.yaml
          studies/
            my-inv/
              study.yaml
              viz/
                run-123/
                  chart.html
                  summary.html
              composites/
                my-comp.yaml
    """
    # investigations/my-inv/investigation.yaml
    inv_dir = tmp_path / "investigations" / "my-inv"
    inv_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text(
        yaml.dump({
            "name": "my-inv",
            "title": "My Investigation",
            "studies": ["my-inv"],
            "hypotheses": [
                {"id": "H1", "statement": "X causes Y"},
            ],
        }),
        encoding="utf-8",
    )

    # studies/my-inv/study.yaml
    study_dir = tmp_path / "studies" / "my-inv"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        yaml.dump({
            "name": "my-inv",
            "baseline": [
                {"name": "baseline-v1", "composite": "pbg_ws.composites.baseline", "params": {}},
            ],
        }),
        encoding="utf-8",
    )

    # studies/my-inv/viz/run-123/
    viz_dir = study_dir / "viz" / "run-123"
    viz_dir.mkdir(parents=True)
    (viz_dir / "chart.html").write_text("<html>chart</html>")
    (viz_dir / "summary.html").write_text("<html>summary</html>")

    # studies/my-inv/composites/my-comp.yaml
    composites_dir = study_dir / "composites"
    composites_dir.mkdir()
    (composites_dir / "my-comp.yaml").write_text(
        yaml.dump({"process": "MyProcess", "config": {"n": 10}}),
        encoding="utf-8",
    )

    return tmp_path


# ---------------------------------------------------------------------------
# build_investigation_viz_html
# ---------------------------------------------------------------------------

class TestBuildInvestigationVizHtml:
    def test_happy_path(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        result = inv_views.build_investigation_viz_html(ws, "my-inv", "run-123")
        assert "viz_files" in result
        files = result["viz_files"]
        assert len(files) == 2
        names = {f["name"] for f in files}
        assert names == {"chart", "summary"}
        for f in files:
            assert "html_path" in f
            assert not f["html_path"].startswith("/")  # workspace-relative

    def test_html_path_is_workspace_relative(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        result = inv_views.build_investigation_viz_html(ws, "my-inv", "run-123")
        for f in result["viz_files"]:
            assert str(ws) not in f["html_path"]

    def test_missing_viz_dir_returns_empty(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        result = inv_views.build_investigation_viz_html(ws, "my-inv", "nonexistent-run")
        assert result == {"viz_files": []}

    def test_missing_investigation_raises_400(self, tmp_path: Path) -> None:
        with pytest.raises(inv_views.InvViewError) as exc_info:
            inv_views.build_investigation_viz_html(tmp_path, "", "run-123")
        assert exc_info.value.status == 400
        assert exc_info.value.body["viz_files"] == []
        assert "error" in exc_info.value.body

    def test_missing_run_id_raises_400(self, tmp_path: Path) -> None:
        with pytest.raises(inv_views.InvViewError) as exc_info:
            inv_views.build_investigation_viz_html(tmp_path, "my-inv", "")
        assert exc_info.value.status == 400

    def test_files_sorted(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        result = inv_views.build_investigation_viz_html(ws, "my-inv", "run-123")
        names = [f["name"] for f in result["viz_files"]]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# build_investigation_composites
# ---------------------------------------------------------------------------

class TestBuildInvestigationComposites:
    def test_happy_path(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        result = inv_views.build_investigation_composites(ws, "my-inv")
        assert "composites" in result
        assert len(result["composites"]) == 1
        c = result["composites"][0]
        assert c["name"] == "baseline-v1"
        assert c["source"] == "pbg_ws.composites.baseline"
        assert c["params"] == {}

    def test_missing_name_raises_400(self, tmp_path: Path) -> None:
        with pytest.raises(inv_views.InvViewError) as exc_info:
            inv_views.build_investigation_composites(tmp_path, "")
        assert exc_info.value.status == 400
        assert "investigation is required" in str(exc_info.value)

    def test_not_found_raises_404(self, tmp_path: Path) -> None:
        with pytest.raises(inv_views.InvViewError) as exc_info:
            inv_views.build_investigation_composites(tmp_path, "nonexistent")
        assert exc_info.value.status == 404
        assert "not found" in exc_info.value.body["error"]

    def test_empty_baseline_returns_empty_list(self, tmp_path: Path) -> None:
        """A valid spec with no baseline list still returns {composites: []}."""
        study_dir = tmp_path / "studies" / "empty-inv"
        study_dir.mkdir(parents=True)
        # Must be a valid spec shape (legacy single-composite): needs "composite" key.
        (study_dir / "study.yaml").write_text(
            yaml.dump({
                "name": "empty-inv",
                "composite": "pbg_ws.composites.baseline",
            }),
            encoding="utf-8",
        )
        result = inv_views.build_investigation_composites(tmp_path, "empty-inv")
        assert result == {"composites": []}


# NOTE: investigation-rigor is intentionally NOT ported in this batch (deferred
# to Batch 3 — needs the per-study run-merging loader that pbg_superpowers.rigor
# reads via spec["runs"]). No lib builder / parity test for rigor here.


# ---------------------------------------------------------------------------
# build_investigation_composite_doc
# ---------------------------------------------------------------------------

class TestBuildInvestigationCompositeDoc:
    def test_happy_path(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        result = inv_views.build_investigation_composite_doc(ws, "my-inv", "my-comp")
        assert "state" in result
        assert result["state"]["process"] == "MyProcess"
        assert result["state"]["config"]["n"] == 10

    def test_missing_investigation_raises_400(self, tmp_path: Path) -> None:
        with pytest.raises(inv_views.InvViewError) as exc_info:
            inv_views.build_investigation_composite_doc(tmp_path, "", "my-comp")
        assert exc_info.value.status == 400
        assert "required" in exc_info.value.body["error"]

    def test_missing_composite_raises_400(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        with pytest.raises(inv_views.InvViewError) as exc_info:
            inv_views.build_investigation_composite_doc(ws, "my-inv", "")
        assert exc_info.value.status == 400

    def test_composite_not_found_raises_404(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        with pytest.raises(inv_views.InvViewError) as exc_info:
            inv_views.build_investigation_composite_doc(ws, "my-inv", "nonexistent")
        assert exc_info.value.status == 404
        assert "not found" in exc_info.value.body["error"]

    def test_invalid_yaml_raises_500(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        (ws / "studies" / "my-inv" / "composites" / "bad.yaml").write_bytes(
            b": invalid: yaml: {{{"
        )
        with pytest.raises(inv_views.InvViewError) as exc_info:
            inv_views.build_investigation_composite_doc(ws, "my-inv", "bad")
        assert exc_info.value.status == 500
        assert "parse failed" in exc_info.value.body["error"]


# ---------------------------------------------------------------------------
# build_investigation_hypotheses
# ---------------------------------------------------------------------------

class TestBuildInvestigationHypotheses:
    def test_happy_path(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        result = inv_views.build_investigation_hypotheses(ws, "my-inv")
        assert "hypotheses" in result
        assert "investigation" in result
        assert result["investigation"] == "my-inv"
        # At minimum the authored hypothesis is present
        assert len(result["hypotheses"]) >= 1
        assert result["hypotheses"][0]["id"] == "H1"

    def test_missing_investigation_returns_empty(self, tmp_path: Path) -> None:
        result = inv_views.build_investigation_hypotheses(tmp_path, "nonexistent")
        assert result == {"hypotheses": [], "investigation": "nonexistent"}

    def test_never_raises(self, tmp_path: Path) -> None:
        """build_investigation_hypotheses must never raise (always 200)."""
        result = inv_views.build_investigation_hypotheses(tmp_path, "")
        assert isinstance(result, dict)
        assert "hypotheses" in result

    def test_empty_hypotheses_returns_empty_list(self, tmp_path: Path) -> None:
        inv_dir = tmp_path / "investigations" / "no-hyp"
        inv_dir.mkdir(parents=True)
        (inv_dir / "investigation.yaml").write_text(
            yaml.dump({"name": "no-hyp", "studies": []}), encoding="utf-8"
        )
        result = inv_views.build_investigation_hypotheses(tmp_path, "no-hyp")
        assert result["hypotheses"] == []


# ---------------------------------------------------------------------------
# InvViewError
# ---------------------------------------------------------------------------

class TestInvViewError:
    def test_body_and_status(self) -> None:
        err = inv_views.InvViewError({"error": "oops", "extra": 1}, 400)
        assert err.status == 400
        assert err.body == {"error": "oops", "extra": 1}
        assert str(err) == "oops"

    def test_body_with_empty_error(self) -> None:
        err = inv_views.InvViewError({}, 404)
        assert str(err) == ""


# ---------------------------------------------------------------------------
# TestServerShimParity: legacy handler body == lib-builder body
# ---------------------------------------------------------------------------

class TestServerShimParity:
    """Verify that the legacy stdlib shim and the lib builder produce identical
    JSON bodies (and status codes) on the same fixture.

    The handlers now delegate to ``lib.investigation_views``, so these tests
    exercise the actual wiring: query-string parsing, the WORKSPACE plumbing,
    and the status-code mapping — by invoking the real ``server.Handler``
    methods (constructed via ``__new__`` so we bypass the socket-bound
    ``__init__`` and capture the ``self._json(body, status)`` call).
    """

    @staticmethod
    def _invoke(monkeypatch: Any, ws_root: Path, method_name: str, path: str = "/") -> dict:
        import vivarium_dashboard.server as server
        monkeypatch.setattr(server, "WORKSPACE", ws_root)
        handler = server.Handler.__new__(server.Handler)
        captured: dict = {}

        def _fake_json(data: dict, code: int) -> None:
            captured["body"] = data
            captured["status"] = code

        handler._json = _fake_json  # type: ignore[method-assign]
        handler.path = path
        getattr(handler, method_name)()
        return captured

    # --- investigation-viz-html ---

    def test_viz_html_200_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        path = "/api/investigation-viz-html?investigation=my-inv&run_id=run-123"
        captured = self._invoke(monkeypatch, ws, "_get_investigation_viz_html", path)
        lib_body = inv_views.build_investigation_viz_html(ws, "my-inv", "run-123")
        assert captured["status"] == 200
        assert captured["body"] == lib_body

    def test_viz_html_400_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        captured = self._invoke(
            monkeypatch, tmp_path, "_get_investigation_viz_html",
            "/api/investigation-viz-html",
        )
        assert captured["status"] == 400
        assert "error" in captured["body"]
        assert captured["body"]["viz_files"] == []
        # Same body the lib builder raises
        try:
            inv_views.build_investigation_viz_html(tmp_path, "", "")
        except inv_views.InvViewError as exc:
            assert captured["body"] == exc.body

    # --- investigation-composites ---

    def test_composites_200_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        captured = self._invoke(
            monkeypatch, ws, "_get_investigation_composites",
            "/api/investigation-composites?investigation=my-inv",
        )
        lib_body = inv_views.build_investigation_composites(ws, "my-inv")
        assert captured["status"] == 200
        assert captured["body"] == lib_body

    def test_composites_400_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        captured = self._invoke(
            monkeypatch, tmp_path, "_get_investigation_composites",
            "/api/investigation-composites",
        )
        assert captured["status"] == 400
        try:
            inv_views.build_investigation_composites(tmp_path, "")
        except inv_views.InvViewError as exc:
            assert captured["body"] == exc.body

    def test_composites_404_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        captured = self._invoke(
            monkeypatch, tmp_path, "_get_investigation_composites",
            "/api/investigation-composites?investigation=missing",
        )
        assert captured["status"] == 404
        try:
            inv_views.build_investigation_composites(tmp_path, "missing")
        except inv_views.InvViewError as exc:
            assert captured["body"] == exc.body

    # NOTE: investigation-rigor stays on the legacy handler (deferred to
    # Batch 3) — no shim parity test for it here.

    # --- investigation-composite-doc ---

    def test_composite_doc_200_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        captured = self._invoke(
            monkeypatch, ws, "_get_investigation_composite_doc",
            "/api/investigation-composite-doc?investigation=my-inv&composite=my-comp",
        )
        lib_body = inv_views.build_investigation_composite_doc(ws, "my-inv", "my-comp")
        assert captured["status"] == 200
        assert captured["body"] == lib_body

    def test_composite_doc_400_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        captured = self._invoke(
            monkeypatch, tmp_path, "_get_investigation_composite_doc",
            "/api/investigation-composite-doc",
        )
        assert captured["status"] == 400
        try:
            inv_views.build_investigation_composite_doc(tmp_path, "", "")
        except inv_views.InvViewError as exc:
            assert captured["body"] == exc.body

    def test_composite_doc_404_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        captured = self._invoke(
            monkeypatch, ws, "_get_investigation_composite_doc",
            "/api/investigation-composite-doc?investigation=my-inv&composite=missing",
        )
        assert captured["status"] == 404
        try:
            inv_views.build_investigation_composite_doc(ws, "my-inv", "missing")
        except inv_views.InvViewError as exc:
            assert captured["body"] == exc.body

    # --- investigation-hypotheses ---

    def test_hypotheses_200_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        # _investigation_hypotheses_test is a static method on Handler.
        import vivarium_dashboard.server as server
        json_bytes, status = server.Handler._investigation_hypotheses_test(ws, "my-inv")
        lib_body = inv_views.build_investigation_hypotheses(ws, "my-inv")
        assert status == 200
        assert json.loads(json_bytes) == lib_body

    def test_hypotheses_missing_inv_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        import vivarium_dashboard.server as server
        json_bytes, status = server.Handler._investigation_hypotheses_test(tmp_path, "nonexistent")
        lib_body = inv_views.build_investigation_hypotheses(tmp_path, "nonexistent")
        assert status == 200
        assert json.loads(json_bytes) == lib_body
