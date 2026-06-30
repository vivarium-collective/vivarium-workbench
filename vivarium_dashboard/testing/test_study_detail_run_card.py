from pathlib import Path

_PKG = Path(__file__).parent.parent
JS = (_PKG / "static" / "study-detail.js").read_text()
HTML = (_PKG / "templates" / "study-detail.html").read_text()


def test_run_card_wired():
    assert 'id="reproduce-card"' in HTML
    assert "_renderReproduceCard" in JS
    assert "run_commands" in JS
