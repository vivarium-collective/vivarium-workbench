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
    """The ↓ report action DOWNLOADS the report into the browser's download
    folder: live mode requests the endpoint with ?download=1 (the server sends
    Content-Disposition: attachment) and the click goes through an <a download>
    so the file is saved rather than opened in a tab."""
    assert "?download=1" in JS
    assert "a.download = 'investigation-'" in JS
    assert "a.click()" in JS


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


def test_needs_attention_elevated_above_intro():
    dv = detail_view()
    na = dv.index('id="investigation-needs-attention"')
    intro = dv.index('id="investigation-intro"')
    assert na < intro, "needs-attention should appear before the intro block"


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
