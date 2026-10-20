"""The Assurance section is reframed as a narrative — Tests = "the bar",
Audit = "is the bar sound?", Build = "was it earned?" — and the Build panel
renders the model-building loop as a trajectory (integrity ribbon + a per-test
signed-margin matrix when the loop-state carries verdicts, else an iteration
ladder with the real margin-delta values), plus a result / honest-give-up banner.

JS-source-membership + template-mount checks, matching the repo's existing
pattern (see test_sourcing_panel_render.py).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "vivarium_workbench"
JS = (ROOT / "static" / "study-detail.js").read_text(encoding="utf-8")
TPL = (ROOT / "templates" / "study-detail.html").read_text(encoding="utf-8")


def test_assurance_subtitles_reframed():
    # 5-tab layout: Tests folds into Audit (first section) and Build folds into
    # Model (Advanced). The narrative framing per concept is preserved.
    assert 'id="panel-tests"' not in TPL
    assert 'id="panel-build"' not in TPL
    audit_panel = TPL[TPL.index('id="panel-audit"'):TPL.index('id="panel-conclusions"')]
    # Tests content is still the Audit tab's first section, but the "Tests — the
    # bar" heading + "The bar"/"Is the bar sound?" explainer prose were removed
    # (presentation cleanup). The readiness-gaps mount now leads the panel.
    assert "Tests — the bar" not in audit_panel
    assert "Is the bar sound?" not in audit_panel
    assert 'id="audit-readiness"' in audit_panel
    assert "does this study pass its own bar" not in audit_panel
    # The "Advanced — build provenance" section (with its "How the model was
    # built" intro) was removed from the Model panel as presentation cleanup.
    compose_panel = TPL[TPL.index('id="panel-compose"'):TPL.index('id="panel-simulate"')]
    assert "How the model was built" not in compose_panel
    assert "build provenance" not in compose_panel
    # The build-loop rendering code (the "was it earned?" verdict framing) still
    # exists in the JS — it is simply no longer mounted in the template.
    assert "Was it earned?" in JS


def test_build_panel_renders_the_trajectory():
    # the loop rendered as a narrative, not a bare provenance dump
    assert "_buildIntegrityRibbon" in JS
    assert "_renderMarginMatrix" in JS          # per-test matrix when verdicts present
    assert "_renderIterationLadder" in JS       # fallback with real margin-delta values
    assert "Iteration trajectory" in JS
    assert "_LOOP_VERDICT_COLORS" in JS


def test_build_panel_surfaces_result_and_honesty():
    assert "Honest give-up" in JS
    assert "the tests passed, honestly" in JS or "earned by editing the model" in JS
    # integrity: reopens + locked hash still surfaced
    assert "reopen_count" in JS and "locked_tests_hash" in JS


def test_margin_matrix_is_per_test_forward_compatible():
    # reads h.tests[{name, verdict, margin}] — the enrichment the loop-state will carry
    assert "h.tests" in JS
    for v in ("within_tol", "drift", "mismatch"):
        assert v in JS
