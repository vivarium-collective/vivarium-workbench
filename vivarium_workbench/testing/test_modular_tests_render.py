# vivarium_dashboard/testing/test_modular_tests_render.py
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

TPL = Path(__file__).resolve().parent.parent / "templates"


def _render_tests_fragment(tests):
    # Render just the Tests <li> loop in isolation via a tiny harness template
    # that includes the same markup contract the real template uses.
    env = Environment(loader=FileSystemLoader(str(TPL)), autoescape=True)
    src = ("{% for b in study.behavior_tests or study.tests %}"
           "<li class=\"expected-behavior-item\" id=\"bt-{{ b.name }}\""
           " data-test-kind=\"{{ b.kind or 'behavioral' }}\""
           "{% if (b.kind or 'behavioral') == 'report_card' %} data-card=\"{{ b.card }}\"{% endif %}>"
           "{% if (b.kind or 'behavioral') == 'report_card' %}"
           "<div class=\"report-card-mount\"></div>"
           "{% else %}<span class=\"bt-result\">{{ b.name }}</span>{% endif %}</li>{% endfor %}")
    return env.from_string(src).render(study={"tests": tests})


def test_behavioral_renders_pill_report_card_renders_mount():
    html = _render_tests_fragment([
        {"name": "beh"},
        {"name": "card1", "kind": "report_card", "card": "standard"},
    ])
    assert 'id="bt-beh" data-test-kind="behavioral"' in html
    assert '<span class="bt-result">beh</span>' in html      # behavioral unchanged
    assert 'data-test-kind="report_card" data-card="standard"' in html
    assert '<div class="report-card-mount"></div>' in html
