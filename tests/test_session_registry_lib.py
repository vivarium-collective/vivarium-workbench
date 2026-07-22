"""Slice 1 of the workspace-context refactor: SessionRegistry + WorkspaceContext.

Verifies the per-session routing seam is behavior-preserving — an unbound
session (and every cookie-less client) resolves to the process default
workspace, while an explicit bind routes that session elsewhere.
"""
from pathlib import Path

import pytest

from vivarium_workbench.lib import (
    active_workspace,
    session_registry,
    workspace_context,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    session_registry.clear()
    saved = active_workspace.get_workspace_root()
    yield
    session_registry.clear()
    if saved is not None:
        active_workspace.set_workspace_root(saved)


# ---------------------------------------------------------------------------
# SessionRegistry
# ---------------------------------------------------------------------------
def test_mint_key_is_unguessable_and_unique():
    keys = {session_registry.mint_key() for _ in range(100)}
    assert len(keys) == 100
    assert all(len(k) >= 32 for k in keys)


def test_unbound_session_has_no_entry():
    assert session_registry.get("never-bound") is None
    assert session_registry.get(None) is None


def test_rebind_then_get(tmp_path):
    session_registry.rebind("s1", tmp_path)
    entry = session_registry.get("s1")
    assert entry is not None and entry.source_path == tmp_path


def test_drop_forgets_binding(tmp_path):
    session_registry.rebind("s1", tmp_path)
    session_registry.drop("s1")
    assert session_registry.get("s1") is None


# ---------------------------------------------------------------------------
# WorkspaceContext resolution — the behavior-preservation contract
# ---------------------------------------------------------------------------
def test_unbound_resolves_to_process_default(tmp_path):
    """A cookie-less / unbound session resolves to the registered global root —
    exactly the pre-seam behavior."""
    active_workspace.set_workspace_root(tmp_path)
    ctx = workspace_context.resolve(None)
    assert ctx.ws_root == tmp_path.resolve()
    assert ctx.session_key is None


def test_unknown_key_resolves_to_process_default(tmp_path):
    active_workspace.set_workspace_root(tmp_path)
    ctx = workspace_context.resolve("stale-key-not-in-registry")
    assert ctx.ws_root == tmp_path.resolve()


def test_bound_session_resolves_to_its_path(tmp_path):
    """A bound session routes to its own workspace, not the global default."""
    other = tmp_path / "other-ws"
    other.mkdir()
    active_workspace.set_workspace_root(tmp_path)          # global default
    session_registry.rebind("s1", other)                    # this session bound elsewhere

    assert workspace_context.resolve("s1").ws_root == other  # bound → its own
    assert workspace_context.resolve(None).ws_root == tmp_path.resolve()  # unbound → default


# ---------------------------------------------------------------------------
# Slice 4: per-session switch semantics (endpoint-level, in-process)
# ---------------------------------------------------------------------------
def test_switch_is_isolated_across_sessions(tmp_path, monkeypatch):
    """The load-bearing slice-4 property: session A switching does not move
    session B, nor the process-global default."""
    from fastapi.testclient import TestClient
    from pbg_superpowers import workspace_catalog
    from vivarium_workbench.api.app import create_app
    from vivarium_workbench.lib import _root

    session_registry.clear()
    default = tmp_path / "default"; default.mkdir(); (default / "workspace.yaml").write_text("name: d\n")
    other = tmp_path / "other"; other.mkdir(); (other / "workspace.yaml").write_text("name: o\n")
    _root.set_workspace_root(default)
    monkeypatch.setattr(workspace_catalog, "list_workspaces",
                        lambda: [{"path": str(other), "name": "o"}])

    app = create_app()
    a = TestClient(app)   # each TestClient keeps its own cookie jar = its own session
    b = TestClient(app)

    # Establish both sessions (first GET mints each cookie).
    assert a.get("/health").status_code == 200
    assert b.get("/health").status_code == 200

    # Session A switches to `other`.
    r = a.post("/api/source/switch", json={"path": str(other)})
    assert r.status_code == 200

    key_a = a.cookies.get(session_registry.SESSION_COOKIE)
    key_b = b.cookies.get(session_registry.SESSION_COOKIE)
    assert key_a and key_b and key_a != key_b

    # A is bound to `other`; B is untouched (unbound → default); global unmoved.
    assert session_registry.get(key_a).source_path == Path(str(other))
    assert session_registry.get(key_b) is None
    assert _root.get_workspace_root() == default.resolve()
