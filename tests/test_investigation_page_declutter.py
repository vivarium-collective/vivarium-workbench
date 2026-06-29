import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_dashboard/templates/index.html.j2").read_text()
JS = (ROOT / "vivarium_dashboard/static/walkthrough.js").read_text()


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
    assert '_generateInvestigationReport()' in cluster, "report button not in export cluster"
    assert '_downloadInvestigationNotebook()' in cluster, "notebook button not in export cluster"
    # Refresh is icon-only now (no text label on the button itself)
    assert '↻ Refresh</button>' not in HTML, "Refresh button still has a text label"
    assert 'id="investigation-detail-refresh"' in dv, "refresh button id lost"


def test_one_about_disclosure_with_demoted_subblocks():
    dv = detail_view()
    # Exactly one <summary> remains in the detail view: the About disclosure.
    assert dv.count('<summary>') == 1, f"expected 1 <summary>, got {dv.count('<summary>')}"
    assert '<summary>About this investigation</summary>' in dv
    # Standalone collapsibles gone; ids preserved as plain blocks.
    assert '<summary>How to read this</summary>' not in dv
    assert '<summary>Glossary</summary>' not in dv
    for id_ in ['investigation-detail-description', 'investigation-how-to-read',
                'investigation-glossary', 'investigation-biology-story',
                'investigation-biology-story-text']:
        assert f'id="{id_}"' in dv, f"lost id {id_}"
    # About open by default.
    about_start = dv.index('id="investigation-intro-details"')
    assert ' open' in dv[about_start:about_start + 120], "About disclosure not open by default"


def test_needs_attention_elevated_above_intro():
    dv = detail_view()
    na = dv.index('id="investigation-needs-attention"')
    intro = dv.index('id="investigation-intro"')
    assert na < intro, "needs-attention should appear before the intro block"


def test_at_a_glance_removed():
    assert 'id="investigation-at-a-glance"' not in HTML, "dead at-a-glance node still present"
    assert "getElementById('investigation-at-a-glance')" not in JS, "dead at-a-glance JS still present"


def test_dag_lead_condensed_with_tooltip():
    dv = detail_view()
    i = dv.index('id="investigation-dag-lead"')
    j = dv.index('</div>', i)
    block = dv[i:j]
    # Chip carries the full explanation.
    assert 'viv-info-chip' in block and 'data-tooltip=' in block, "info chip missing from dag-lead"
    assert 'knowledge-producing' in block, "full explanation lost"
    # The verbose explanation is no longer in the *visible* caption (only in the tooltip attr).
    visible = re.sub(r'<[^>]+>', '', block)
    assert 'knowledge-producing' not in visible, "verbose phrase still in visible caption"
    assert 'builds understanding of the mechanism' not in visible, "verbose phrase still visible"
