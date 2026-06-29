from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_dashboard/templates/study-detail.html").read_text()


def _panel():
    start = HTML.index('id="panel-conclusions"')
    # the panel ends at the next study-tab-panel section or the template end
    nxt = HTML.find('class="study-tab-panel"', start + 10)
    return HTML[start: nxt if nxt != -1 else len(HTML)]


def test_four_group_headers_present():
    panel = _panel()
    for h in ["Verdict &amp; conclusion", "Evidence", "Follow-ups &amp; decisions", "Limitations &amp; provenance"]:
        assert h in panel or h.replace("&amp;", "&") in panel, f"missing group header: {h}"


def test_editable_verdict_basis_inputs_preserved():
    panel = _panel()
    for track in ["regression_compatibility", "biological_validation", "explanatory_gain"]:
        assert f'data-narrative-path="conclusion_verdicts.{track}.basis"' in panel


def test_js_hooks_preserved():
    panel = _panel()
    assert 'id="discovery-implications-section"' in panel
    assert 'id="followups-authored"' in panel


def test_panel_identity_unchanged():
    assert 'data-kind="conclusions" id="panel-conclusions"' in HTML


def test_group4_is_collapsed_details():
    panel = _panel()
    # the Limitations & provenance group is a <details> with no `open`
    i = panel.find("Limitations &amp; provenance")
    if i == -1:
        i = panel.find("Limitations & provenance")
    assert i != -1
    # the group header sits inside a <details ...><summary> opened just before it
    before = panel[max(0, i - 400):i]
    assert "<details" in before and "open" not in before.split("<details", 1)[1][:120]
