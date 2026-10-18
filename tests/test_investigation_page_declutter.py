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
    # Refresh is icon-only now (no text label on the button itself)
    assert '↻ Refresh</button>' not in HTML, "Refresh button still has a text label"
    assert 'id="investigation-detail-refresh"' in dv, "refresh button id lost"


def test_report_button_downloads_not_just_opens():
    """The ↓ report action DOWNLOADS the report (saved, not opened in a tab): the
    large server-rendered file is fetched and the blob saved via an <a download>.
    An in-flight guard prevents repeat clicks from firing multiple full renders
    (the 'accidentally downloaded it 3×' bug)."""
    assert "URL.createObjectURL(blob)" in JS   # blob download of the fetched file
    assert "a.download = dl" in JS              # saved via <a download>
    assert "a.click()" in JS
    assert "_invReportBusy" in JS              # guard against repeat clicks


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


def test_needs_attention_single_surface_is_toolbar_chip():
    # Needs-attention is a SINGLE surface now: the toolbar chip. The duplicate
    # in-body banner (#investigation-needs-attention) was removed, and the chip
    # opens the deterministic scan in the same rich popover used for "all clear".
    assert 'id="investigation-needs-attention"' not in HTML, \
        "in-body needs-attention banner should be removed (chip is the only surface)"
    assert 'id="aig-needs-attention-chip"' in HTML, "toolbar needs-attention chip missing"
    assert "_aigShowAllClearInfo(this)" in JS, "chip should open the scan popover"
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
