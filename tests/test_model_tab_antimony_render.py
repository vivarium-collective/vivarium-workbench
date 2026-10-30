"""The Model tab renders an inline model SOURCE (Antimony/SBML carried in a
process's ``config.model``) as its own labeled code block, instead of burying
it as an escaped string inside the JSON config view. JS/CSS-string scan,
matching the repo's existing JS-scan test style (see test_sourcing_panel_render.py).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "vivarium_workbench"
JS = (ROOT / "static" / "study-detail.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def _render_composite_source_body() -> str:
    fn = JS[JS.index("function _renderCompositeSource"):]
    return fn[: fn.index("window._renderCompositeSource")]


def test_inline_model_source_pulled_from_config():
    body = _render_composite_source_body()
    # reads the inline model text + its declared format off the process config
    assert "cfg.model" in body
    assert "cfg.model_format" in body
    # the big model string is removed from the JSON config view (shown separately)
    assert "restCfg" in body
    assert "k !== 'model'" in body


def test_inline_model_source_rendered_as_labeled_code_block():
    body = _render_composite_source_body()
    assert "model-source-code" in body
    assert "model-source-code-head" in body
    # label is "<FORMAT> MODEL" (e.g. "ANTIMONY MODEL"), format upper-cased
    assert "toUpperCase()" in body
    assert "' MODEL'" in body


def test_model_source_code_block_styled():
    assert ".model-source-code-head" in CSS
