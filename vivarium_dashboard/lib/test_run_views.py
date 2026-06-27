"""Pure builders for the two test-running POST routes.

Behaviour-preserving ports of the stdlib handlers
``server.Handler._post_study_tests_run`` and ``_post_run_tests``.  Both return
``(body, status)`` so the FastAPI route wraps every path in ``JSONResponse``
(preserving the non-200 codes verbatim).  No ``import server`` here.

``subprocess`` is bound at module level so tests monkeypatch ``subprocess.run``
with a fake ``CompletedProcess`` (or to raise ``TimeoutExpired`` / a generic
exception) and never spawn a real pytest.  ``run_study_tests`` is imported
lazily inside ``study_tests_run`` (matching the legacy handler) so tests
monkeypatch ``lib.study_tests.run_study_tests`` directly.

The workspace root is threaded explicitly as ``ws_root`` (replacing the server
``WORKSPACE`` global / ``workspace_paths()`` helper) so the module stays
importable standalone and flip-ready.  The legacy server.py handlers keep their
inline logic for now — the dedup happens at the flip.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def study_tests_run(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Run a study's pytest suite. Returns ``(response_dict, status_code)``.

    Behaviour-preserving port of ``_post_study_tests_run`` (body ``{study}``):

      * missing ``study``       → ``({"error": "missing 'study' in body"}, 400)``
      * study.yaml not on disk  → ``({"error": f"study not found: {slug}"}, 404)``
      * ``StudyTestsConcurrentError`` → ``({"error": str(e)}, 409)``
      * happy path              → ``({"summary", "tests", "note"}, 200)``
    """
    slug = (body or {}).get("study")
    if not slug:
        return {"error": "missing 'study' in body"}, 400
    spec_path = WorkspacePaths.load(ws_root).studies / slug / "study.yaml"
    if not spec_path.exists():
        return {"error": f"study not found: {slug}"}, 404
    from vivarium_dashboard.lib.study_tests import (
        run_study_tests,
        StudyTestsConcurrentError,
    )
    try:
        result = run_study_tests(ws_root, slug)
    except StudyTestsConcurrentError as e:
        return {"error": str(e)}, 409
    return {
        "summary": result.summary,
        "tests": result.tests,
        "note": result.note,
    }, 200


def run_workspace_tests(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Run pytest for the whole workspace. Returns ``(response_dict, status_code)``.

    Behaviour-preserving port of ``_post_run_tests`` (v0.3.0: no model param):

      * happy path                 → ``({"returncode", "stdout", "stderr"}, 200)``
      * ``subprocess.TimeoutExpired`` → ``({"error": "pytest timed out after 120s"}, 500)``
      * any other exception        → ``({"error": str(e)}, 500)``
    """
    test_dir = WorkspacePaths.load(ws_root).tests
    cmd = [sys.executable, "-m", "pytest", "-v", str(test_dir)]
    try:
        result = subprocess.run(
            cmd, cwd=ws_root,
            capture_output=True, text=True, timeout=120,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }, 200
    except subprocess.TimeoutExpired:
        return {"error": "pytest timed out after 120s"}, 500
    except Exception as e:
        return {"error": str(e)}, 500
