"""Goldens for what the built-in AI may not reach (docs/backend-adapters.md step P2, Routes Rt2), taken on the base
before the SMS plugin's route literals left ``lib/ai_tools.py``'s ``EXCLUDED_PATHS`` for ``ProviderRouter.ai_excluded``.

With providers on, the operations the AI tool index leaves out (so, the OpenAPI being unchanged, the index's
(method, path) set) and the Capabilities popover's ``excluded`` list are identical to the base.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic_ai")

from vivarium_workbench.api import app as appmod  # noqa: E402
from vivarium_workbench.lib import ai_tools  # noqa: E402
from vivarium_workbench.lib import backends  # noqa: E402

#: Every OpenAPI operation the AI tool index leaves out, with providers on (the base: 299 operations, 250 indexed).
EXCLUDED_OPERATIONS = [
    ("DELETE", "/api/ai/credentials/{provider}"), ("GET", "/api/ai/capabilities"), ("GET", "/api/ai/status"),
    ("GET", "/api/auth/github/orgs"), ("GET", "/api/auth/github/poll"), ("GET", "/api/auth/github/status"),
    ("GET", "/api/composite-run/{run_id}/download"), ("GET", "/api/data-source-file"), ("GET", "/api/events"),
    ("GET", "/api/events/log"), ("GET", "/api/guidance"), ("GET", "/api/investigation-notebook/{slug}"),
    ("GET", "/api/investigation/{slug}/figure/{n}.{ext}"), ("GET", "/api/investigation/{slug}/figures.zip"),
    ("GET", "/api/investigation/{slug}/report"), ("GET", "/api/simulation-run-download"),
    ("GET", "/api/source/builds"), ("GET", "/api/source/manifest"), ("GET", "/api/source/materialization"),
    ("GET", "/api/source/remote-health"), ("GET", "/api/study-analysis-zip"), ("GET", "/api/study-export"),
    ("GET", "/api/study/{slug}/figures.zip"), ("GET", "/api/study/{slug}/notebook"),
    ("GET", "/api/study/{slug}/outputs.zip"), ("GET", "/api/workspaces"), ("POST", "/api/ai/credentials"),
    ("POST", "/api/ai/ollama-models"), ("POST", "/api/ai/select"), ("POST", "/api/auth/github/logout"),
    ("POST", "/api/auth/github/start"), ("POST", "/api/auth/github/token"), ("POST", "/api/branch/push"),
    ("POST", "/api/chat/turn"), ("POST", "/api/expert-doc"), ("POST", "/api/remote-run-build"),
    ("POST", "/api/remote-run-start"), ("POST", "/api/source/build-remote"), ("POST", "/api/source/materialize-repo"),
    ("POST", "/api/source/switch"), ("POST", "/api/source/switch-build"), ("POST", "/api/work-create-pr"),
    ("POST", "/api/work-link-branch"), ("POST", "/api/work-push"), ("POST", "/api/workspaces/add"),
    ("POST", "/api/workspaces/cleanup-stale"), ("POST", "/api/workspaces/forget"), ("POST", "/api/workspaces/start"),
    ("POST", "/api/workspaces/stop"),
]

#: ``capabilities(app)["excluded"]`` with providers on.
CAPABILITIES_EXCLUDED = [
    "tag: AI", "tag: Auth", "tag: Downloads (reads)", "/api/ai/", "/api/chat/", "/api/events", "/api/source/",
    "/api/workspaces", "/api/branch/push", "/api/composite-run/{run_id}/download", "/api/expert-doc",
    "/api/remote-run-build", "/api/remote-run-start", "/api/simulation-run-download", "/api/study-analysis-zip",
    "/api/work-create-pr", "/api/work-link-branch", "/api/work-push",
]


@pytest.fixture(autouse=True)
def _fresh_registry():
    backends.reset_registry()
    yield
    backends.reset_registry()


def _left_out(app) -> list[tuple[str, str]]:
    index = ai_tools.build_index(app)
    served = {(e["method"], e["path"]) for e in index.values()}
    every = {(m.upper(), p) for p, item in app.openapi()["paths"].items() for m in ai_tools._METHODS if item.get(m)}
    return sorted(every - served)


@pytest.mark.plugin
def test_with_providers_on_the_ai_index_leaves_out_what_the_base_did():
    app = appmod.create_app()
    assert _left_out(app) == EXCLUDED_OPERATIONS


@pytest.mark.plugin
def test_with_providers_on_the_capabilities_popover_lists_what_the_base_did():
    app = appmod.create_app()
    assert ai_tools.capabilities(app)["excluded"] == CAPABILITIES_EXCLUDED


# --- declared change: with providers off the plugin's two paths are absent, so they leave the excluded list ---------

def test_with_providers_off_the_plugin_paths_leave_the_excluded_list(monkeypatch):
    """Declared (step P2 Routes Rt2): with providers off the SMS routes do not exist, so their paths are not in the
    popover's ``excluded`` list either. Everything the core excludes stays excluded."""
    from vivarium_workbench.lib.backends import provider as provider_mod

    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    app = appmod.create_app()
    assert app.state.provider_ai_excluded == frozenset()
    sms = {"/api/remote-run-start", "/api/remote-run-build"}
    assert ai_tools.capabilities(app)["excluded"] == [e for e in CAPABILITIES_EXCLUDED if e not in sms]
    plugin_ops = {("POST", p) for p in sms} | {("GET", "/api/source/builds"), ("POST", "/api/source/build-remote"),
                                              ("POST", "/api/source/switch-build")}
    assert _left_out(app) == [op for op in EXCLUDED_OPERATIONS if op not in plugin_ops]


@pytest.mark.plugin
def test_with_providers_on_the_plugin_declares_its_pushing_routes():
    app = appmod.create_app()
    assert app.state.provider_ai_excluded == frozenset({"/api/remote-run-start", "/api/remote-run-build"})


@pytest.mark.plugin
def test_ai_tools_names_no_plugin_route():
    """The plugin's paths are its own declaration (``ProviderRouter.ai_excluded``), not ``ai_tools``' literals."""
    import inspect

    from vivarium_workbench_sms import routes

    text = inspect.getsource(ai_tools)
    assert "/api/remote-run-" not in text
    plugin_paths = {r.path for r in routes.build_router().routes}
    assert plugin_paths >= routes.AI_EXCLUDED
    for path in plugin_paths:
        assert f'"{path}"' not in text, path
