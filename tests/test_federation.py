from pathlib import Path

from vivarium_workbench.lib import federation as federation_mod
from vivarium_workbench.lib.federation import (
    LinkedWorkspace,
    linked_workspaces,
    federated_studies,
    federated_investigation_sets,
    federated_composites,
)

FIX = Path(__file__).parent / "_fixtures" / "ws_federation_demo"


def test_linked_workspaces_finds_donor_and_skips_broken():
    links = linked_workspaces(FIX)
    repos = {lw.repo for lw in links}
    assert "donor-repo" in repos          # name from workspace.yaml
    assert all(lw.root.name != "host_ws" for lw in links)  # excludes self
    # broken external dir must not raise and must not appear
    assert "broken" not in {lw.root.name for lw in links}


def test_linked_workspaces_empty_when_no_external(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: solo\n")
    assert linked_workspaces(tmp_path) == []


def test_federated_studies_tagged_and_namespaced():
    studies = federated_studies(FIX)
    ds = next(s for s in studies if s["name"] == "donor_study")
    assert ds["origin_repo"] == "donor-repo"
    assert ds["read_only"] is True
    assert ds["id"] == "donor-repo::donor_study"


def test_federated_investigation_sets_member_studies_namespaced():
    isets = federated_investigation_sets(FIX)
    di = next(i for i in isets if i["name"] == "donor_inv")
    assert di["origin_repo"] == "donor-repo"
    assert di["id"] == "donor-repo::donor_inv"
    assert di["member_studies"] == ["donor-repo::donor_study"]


def test_federated_composites_tagged():
    comps = federated_composites(FIX)
    rec = next(r for r in comps.values() if r.get("name") == "donor_comp")
    assert rec["origin_repo"] == "donor-repo"
    assert rec["read_only"] is True


class _RaisingDir:
    """Stands in for a Path whose .is_dir() reports True but .iterdir()
    genuinely raises (e.g. a permission-denied directory) — Path.is_dir()
    swallows OSError and returns False, so this simulates the case that
    slips past that guard and hits iterdir() directly."""

    def is_dir(self):
        return True

    def iterdir(self):
        raise OSError("permission denied")


class _RaisingLayout:
    @property
    def investigations(self):
        return _RaisingDir()

    @property
    def studies(self):
        return _RaisingDir()


def test_federated_investigation_sets_never_raises_on_bad_workspace(monkeypatch):
    """One linked workspace whose investigations/ dir raises OSError mid-iteration
    must be skipped, not abort enumeration for the other linked workspaces."""
    good = linked_workspaces(FIX)
    assert good  # sanity: donor-repo is discovered
    bad_lw = LinkedWorkspace(repo="bad-repo", root=FIX, layout=_RaisingLayout())
    monkeypatch.setattr(federation_mod, "linked_workspaces", lambda ws_root: good + [bad_lw])

    isets = federation_mod.federated_investigation_sets(FIX)  # must not raise

    names = {i["name"] for i in isets}
    assert "donor_inv" in names
    assert "bad-repo" not in {i["origin_repo"] for i in isets}


def test_build_iset_detail_federation_fallback():
    """A federated investigation (shipped by a linked workspace under external/)
    must resolve on the DETAIL endpoint, not just the listing. Before the
    federation fallback in build_iset_detail this returned None -> HTTP 404
    ("shows in the list, fails to load")."""
    from vivarium_workbench.lib.report_views import build_iset_detail

    detail = build_iset_detail(FIX, "donor_inv")
    assert detail is not None
    assert detail["name"] == "donor_inv"
    assert detail["origin_repo"] == "donor-repo"
    assert detail["read_only"] is True
    # Member study resolved against the LINKED workspace, not reported "missing".
    ds = next(s for s in detail["studies"] if s["name"] == "donor_study")
    assert ds.get("status") != "missing"


def test_build_iset_detail_native_unaffected(tmp_path):
    """A native investigation still resolves and carries no federation tags."""
    import yaml as _yaml
    from vivarium_workbench.lib.report_views import build_iset_detail

    (tmp_path / "workspace.yaml").write_text("name: host\npackage_path: host\n")
    inv = tmp_path / "investigations" / "native_inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        _yaml.safe_dump({"name": "native_inv", "studies": []})
    )
    detail = build_iset_detail(tmp_path, "native_inv")
    assert detail is not None
    assert detail["name"] == "native_inv"
    assert detail["origin_repo"] is None
    assert detail["read_only"] is False


def test_build_iset_detail_unknown_returns_none():
    from vivarium_workbench.lib.report_views import build_iset_detail

    assert build_iset_detail(FIX, "does-not-exist-anywhere") is None


def test_find_composite_path_resolves_federated_and_non_pbg():
    """find_composite_path must resolve a composite that lives in a linked
    workspace under external/ (and, by the same broadened scan, any
    wheel-installed non-`pbg-` distribution). Before generalizing the fallback
    it only scanned `pbg-*` dists, so a `spatio-flux`/`viva-*` package's
    composite failed to resolve with "not a registered composite" even though
    the LISTING found it."""
    from vivarium_workbench.lib.composite_lookup import find_composite_path

    p = find_composite_path(FIX, "host", "donor.composites.donor")
    assert p is not None
    assert p.is_file()
    assert "external/donor" in str(p)


def test_find_composite_path_unknown_returns_none():
    from vivarium_workbench.lib.composite_lookup import find_composite_path

    assert find_composite_path(FIX, "host", "nope.composites.missing") is None
