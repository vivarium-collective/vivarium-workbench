from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text()
JS = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text()


def page_investigations():
    """The #page-investigations header region, up to the first modal."""
    i = HTML.index('id="page-investigations"')
    j = HTML.index('id="new-iset-modal"', i)
    return HTML[i:j]


def test_filter_input_present_and_dead_div_gone():
    p = page_investigations()
    assert 'id="investigations-filter"' in p
    assert 'oninput="_filterInvestigations()"' in p
    assert 'actions now live in' not in p  # dead actions comment/div removed


def test_lead_dropped():
    # #540 (chore: drop the page title + lead text) removed the investigations
    # header prose entirely — no verbose lead, no condensed lead, no title.
    p = page_investigations()
    assert 'preserved as artifacts' not in p          # old verbose lead gone
    assert 'open its study graph' not in p            # condensed lead also dropped
    assert 'page-lead' not in p                       # no lead element at all
    assert 'page-title' not in p                      # title dropped too


def test_list_container_not_inline_grid():
    p = page_investigations()
    i = p.index('id="investigations-list"')
    assert 'grid-template-columns' not in p[i:i + 200]  # grid moved to .investigations-grid


def test_render_groups_and_filter_function():
    assert 'function _filterInvestigations' in JS
    assert 'window._filterInvestigations' in JS
    assert 'iset-group-head' in JS
    assert "_groupHtml('Active'" in JS and "_groupHtml('Closed'" in JS
    assert 'investigations-grid' in JS and 'grid-template-columns' in JS
    assert 'data-iset-status' in JS


def test_card_decluttered():
    i = JS.index('function _renderInvestigationSets')
    block = JS[i:i + 4200]
    assert 'click to open DAG' not in block      # filler removed
    assert 'font-family:monospace' not in block  # standalone slug row removed


def test_investigations_tab_resets_to_list_on_same_hash():
    # Re-clicking the Investigations menu-link while already on #investigations
    # (viewing a detail) fires no hashchange, so an explicit click handler must
    # force the reset via _switchPage('investigations').
    assert '.menu-link[data-page="investigations"]' in JS
    i = JS.index('.menu-link[data-page="investigations"]')
    block = JS[i:i + 400]
    assert "addEventListener('click'" in block
    assert "=== 'investigations'" in block
    assert "_switchPage('investigations')" in block
