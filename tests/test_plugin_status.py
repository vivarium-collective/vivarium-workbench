"""`plugin_status` combines discovered (installed) plugins with the enabled set so
`vwb plugins` can show what's installed and what's on for a workspace."""
from vivarium_workbench.plugins import discovery
from vivarium_workbench.plugins.base import WorkbenchPlugin


class _P(WorkbenchPlugin):
    def __init__(self, name):
        self.name = name

    def register(self, reg):  # pragma: no cover - not exercised here
        pass


def test_plugin_status_marks_installed_and_enabled(monkeypatch):
    monkeypatch.setattr(discovery, "discover_plugins", lambda env=None: [_P("beta"), _P("alpha")])
    env = {discovery.PLUGINS_ENV: "alpha,gamma"}  # gamma enabled but not installed

    rows = discovery.plugin_status(None, env)

    assert [r["name"] for r in rows] == ["alpha", "beta", "gamma"]  # sorted by name
    by = {r["name"]: r for r in rows}
    assert by["alpha"]["installed"] is True and by["alpha"]["enabled"] is True
    assert by["beta"]["installed"] is True and by["beta"]["enabled"] is False
    assert by["gamma"]["installed"] is False and by["gamma"]["enabled"] is True
    assert by["alpha"]["api_version"] == WorkbenchPlugin.api_version
