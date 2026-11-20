import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()
JS = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def detail_view():
    """The #investigation-detail-view block, up to the next page section."""
    i = HTML.index('id="investigation-detail-view"')
    j = HTML.index('id="page-github"', i)
    return HTML[i:j]


def test_header_export_cluster_and_icon_refresh():
    dv = detail_view()
    assert 'inv-export-actions' in dv, "export cluster wrapper missing"
    cluster_start = dv.index('inv-export-actions')
    cluster = dv[cluster_start:cluster_start + 600]
    assert '_downloadInvestigationReport()' in cluster, "report button not in export cluster"
    assert '_downloadInvestigationNotebook()' in cluster, "notebook button not in export cluster"
    # Refresh is icon-only now (no text label on the button itself). The dedicated
    # detail-view refresh button was removed entirely in the toolbar-simplification
    # pass (commit 4387bd8d) — the toolbar is self-evident — so there is no longer
    # an #investigation-detail-refresh id to assert.
    assert '↻ Refresh</button>' not in HTML, "Refresh button still has a text label"


def test_report_button_downloads_not_just_opens():
    """The ↓ report action DOWNLOADS the report (saved, not opened in a tab): the
    large server-rendered file is fetched and the blob saved via an <a download>.
    An in-flight guard prevents repeat clicks from firing multiple full renders
    (the 'accidentally downloaded it 3×' bug)."""
    assert "URL.createObjectURL(blob)" in JS   # blob download of the fetched file
    assert "a.download = dl" in JS              # saved via <a download>
    assert "a.click()" in JS
    assert "_invReportInflight" in JS          # per-key guard against repeat clicks


def test_report_button_is_html_pdf_chooser():
    """The investigation 'download report' control offers HTML and PDF; PDF is
    rendered server-side via ?format=pdf."""
    assert "inv-report-menu" in JS                       # the chooser dropdown
    assert "HTML report" in JS and "PDF report" in JS     # both options
    assert "_downloadInvestigationReport(null," in JS     # passes a format arg
    assert "?format=pdf" in JS                             # PDF rendered server-side
    assert "'.pdf' : '.html'" in JS                        # format → file extension


def test_intro_is_inquiry_brief_host():
    dv = detail_view()
    # The opening is now an always-visible inquiry brief rendered by JS into
    # #investigation-detail-description — the old "About this investigation"
    # disclosure and its demoted how-to-read / glossary / biology sub-blocks
    # are gone (they moved into the brief's flat tab strip).
    assert '<summary>About this investigation</summary>' not in dv
    assert 'inv-brief-host' in dv, "inquiry-brief host class missing"
    assert 'id="investigation-detail-description"' in dv, "brief host id lost"
    for id_ in ['investigation-intro-details', 'investigation-how-to-read',
                'investigation-glossary', 'investigation-biology-story',
                'investigation-biology-story-text']:
        assert f'id="{id_}"' not in dv, f"stale block {id_} still present"


def test_needs_attention_is_a_detail_tab_not_a_toolbar_chip():
    # Needs-attention now lives INSIDE the investigation details, as its own tab —
    # it is no longer a top-bar chip (moved off the highest menu level). The
    # deterministic scan body is shared (_aigScanBodyHtml) and rendered into the
    # tab's mount; the tab button carries the flagged-count badge.
    assert 'id="investigation-needs-attention"' not in HTML, "no in-body banner"
    assert 'id="aig-needs-attention-chip"' not in HTML, \
        "needs-attention chip should be gone from the header (it is a tab now)"
    assert "inv-attention-mount" in JS, "needs-attention tab mount missing"
    assert "id: 'attention'" in JS, "needs-attention tab not pushed into the brief tabs"
    assert "function _aigScanBodyHtml" in JS, "shared scan body renderer missing"
    assert "needs-attention-banner" not in JS, "in-body banner HTML should be gone"


def test_at_a_glance_removed():
    assert 'id="investigation-at-a-glance"' not in HTML, "dead at-a-glance node still present"
    assert "getElementById('investigation-at-a-glance')" not in JS, "dead at-a-glance JS still present"


def test_dag_header_removed_toolbar_is_self_evident():
    # The "Investigation graph" title bar / #investigation-dag-lead was removed —
    # the toolbar + graph are self-evident. The legend lives as a toolbar chip.
    dv = detail_view()
    assert 'id="investigation-dag-lead"' not in dv, "dag-lead title bar should be gone"
    assert '<strong>Investigation graph</strong>' not in dv, "redundant graph heading still present"
    # The graph toolbar (single control row) + the legend chip remain.
    assert 'class="aig-toolbar"' in dv
    assert 'id="dag-legend-chip"' in dv


def test_detail_header_reworked_back_addstudy_toggles():
    """The investigation-detail header: Back lands top-left and reads 'Back to
    Investigations'; a + Study button creates a study pre-scoped to the open
    investigation; the two toggles read as Details / Graph show-hide controls."""
    # Back relabeled + is the first control in the header bar (top-left).
    assert "Back to Investigations" in HTML
    top = HTML[HTML.index('class="inv-header-top"'):]
    assert top.index('id="ws-back"') < top.index('class="inv-header-nav"'), \
        "Back should sit left of the right-hand nav cluster"
    # + Study, scoped to the current investigation.
    assert 'id="ws-add-study"' in HTML
    assert "_openBrowseCreate('study', window._currentIset)" in HTML
    # Toggles relabeled to read as reveal controls, with an active/pressed state.
    assert "> Details<" in HTML and "> Graph<" in HTML
    assert "inv-toggle-on" in JS


def test_validation_is_a_tab_without_rfc_label():
    """Validation moved into the investigation-detail tab strip as its own tab,
    and the '(RFC-0007)' label is dropped."""
    assert "id: 'validation'" in JS
    assert "inv-validation-mount" in JS
    assert "<summary>Validation (RFC-0007)</summary>" not in JS   # rendered label dropped
    # the loader fills the tab mount, not a standalone <details> panel
    assert "function _vivLoadValidationPanel" in JS


def test_export_menu_supports_open_in_browser():
    """The investigation Export menu offers open-in-browser (not only download),
    mirroring the list page: report/notebook/figures each gain an Open action."""
    i = JS.index("function _wsSetInvestigationActions")
    body = JS[i: JS.index("window._wsSetInvestigationActions", i)]
    assert "window._vivReportOpen(event)" in body       # open the HTML report
    assert "window._vivNotebookOpen(event)" in body      # open the notebook
    assert "Open in browser" in body
    assert "Download PDF" in body and "_downloadInvestigationReport(null," in body  # download still there
