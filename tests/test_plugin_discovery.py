"""Task 2: entry-point discovery + opt-in enablement."""


def test_enabled_names_union(tmp_path, monkeypatch):
    (tmp_path / "workspace.yaml").write_text("plugins: [a, b]\n")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PLUGINS", "b, c")
    from vivarium_workbench.plugins.discovery import enabled_plugin_names

    assert enabled_plugin_names(tmp_path) == {"a", "b", "c"}


def test_enabled_names_missing_or_malformed(tmp_path, monkeypatch):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_PLUGINS", raising=False)
    from vivarium_workbench.plugins.discovery import enabled_plugin_names

    assert enabled_plugin_names(None) == set()
    (tmp_path / "workspace.yaml").write_text("plugins: not-a-list\n")
    assert enabled_plugin_names(tmp_path) == set()


def test_select_enabled_dedups_by_name():
    from vivarium_workbench.plugins import WorkbenchPlugin
    from vivarium_workbench.plugins.discovery import select_enabled

    class A(WorkbenchPlugin):
        name = "x"

    class B(WorkbenchPlugin):
        name = "x"

    out = select_enabled([A(), B()], {"x"})
    assert len(out) == 1 and isinstance(out[0], A)
