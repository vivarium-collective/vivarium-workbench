"""AI Settings has no separate Provider control: the Model menu qualifies every model by provider, so choosing a model
chooses the provider, and the API-key / Base-URL fields follow it. (The behaviour is exercised in the JS tests of
`modelTree`/`parseQualified`; this pins the markup and script so the dropdown cannot quietly come back.)
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "vivarium_workbench"
TEMPLATE = (ROOT / "templates" / "index.html.j2").read_text(encoding="utf-8")
SCRIPT = (ROOT / "static" / "ai-login.js").read_text(encoding="utf-8")


def _card():
    i = TEMPLATE.index('id="viv-ai-card"')
    return TEMPLATE[i:TEMPLATE.index("</aside>", i)]


def test_the_settings_sheet_has_no_provider_dropdown():
    card = _card()
    assert 'id="viv-ai-provider"' not in card and "<select" not in card
    assert "<span>Provider</span>" not in card


def test_the_model_menu_comes_first_and_the_key_and_url_fields_remain():
    card = _card()
    assert card.index('id="viv-ai-model"') < card.index('id="viv-ai-status"') < card.index('id="viv-ai-key"')
    assert 'id="viv-ai-url"' in card and 'id="viv-ai-save"' in card


def test_the_script_keeps_the_provider_in_a_variable_not_in_a_removed_control():
    assert "viv-ai-provider" not in SCRIPT
    assert not re.search(r"\bel\.provider\b", SCRIPT)          # `sel.provider` (the server's selection) is fine
    assert "var provider = " in SCRIPT
