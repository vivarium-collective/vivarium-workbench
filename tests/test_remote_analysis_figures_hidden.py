"""The figure listing hides chromosome_state_view (per-timestep noise) from the
gallery, and only for viz listings — ptools/other prefixes are untouched."""
from __future__ import annotations

from vivarium_workbench.lib import remote_analysis_figures as raf


def test_figure_base_name_strips_variant_suffix():
    assert raf._figure_base_name(
        "viz/chromosome_state_view__variant=0_seed=0_gen=3_agent=000.html"
    ) == "chromosome_state_view"
    assert raf._figure_base_name("viz/mass_fraction_summary__variant=0.html") == "mass_fraction_summary"
    assert raf._figure_base_name("mass_fraction_summary.html") == "mass_fraction_summary"


class _FakeS3:
    """Minimal list_objects_v2 stub returning a fixed key set (no pagination)."""
    def __init__(self, keys):
        self._keys = keys

    def list_objects_v2(self, **kw):
        prefix = kw.get("Prefix", "")
        contents = [{"Key": k, "Size": 10} for k in self._keys if k.startswith(prefix)]
        return {"Contents": contents, "IsTruncated": False}


def test_list_prefix_hides_chromosome_only_under_viz():
    keys = [
        "out/analyses/a1/viz/chromosome_state_view__variant=0_gen=0_agent=0.html",
        "out/analyses/a1/viz/chromosome_state_view__variant=0_gen=1_agent=00.html",
        "out/analyses/a1/viz/mass_fraction_summary__variant=0.html",
    ]
    s3 = _FakeS3(keys)

    # viz listing: chromosome_state_view is filtered, the useful one stays.
    figs = raf._list_prefix(s3, "b", "out/analyses/a1/viz", "out/analyses/a1", raf._FIGURE_SUFFIXES)
    names = sorted(raf._figure_base_name(f["path"]) for f in figs)
    assert names == ["mass_fraction_summary"]
    assert all("chromosome_state_view" not in f["path"] for f in figs)

    # a NON-viz listing over the same keys keeps chromosome_state_view (the hide
    # is scoped to viz/, so ptools and other prefixes are never touched).
    all_html = raf._list_prefix(s3, "b", "out/analyses/a1", "out/analyses/a1", raf._FIGURE_SUFFIXES)
    assert any("chromosome_state_view" in f["path"] for f in all_html)


# --- study_remote_figures: cold-cache candidate sourcing + honest S3-auth error ---

def _remote_row(sid, slug, status="completed"):
    return {"study_slug": slug, "status": status,
            "remote_origin": {"simulation_id": sid}, "sim_name": f"sim{sid}"}


def test_study_remote_figures_sources_candidates_from_remote_list(monkeypatch):
    """Candidates come from list_remote_simulations even when the SWR-cached
    combined index is cold (returns no remote rows) — the bug that collapsed a
    figure-bearing study to 'no-remote-sims'."""
    from vivarium_workbench.lib import remote_simulations as rs
    from vivarium_workbench.lib import simulations_index as si
    monkeypatch.setattr(rs, "list_remote_simulations",
                        lambda ws, **k: [_remote_row(1086, "cd2-antibiotic-cocktail"),
                                         _remote_row(1087, "other-study")])
    # cold combined index: no remote rows (the pre-fix source)
    monkeypatch.setattr(si, "build_simulations_data_cached",
                        lambda ws, **k: {"simulations": []})
    monkeypatch.setattr(raf, "list_remote_analysis_figures",
                        lambda client, sid: {"available": True, "reason": "ok",
                                             "analyses": [{"name": "analysis-percell-run3",
                                                           "status": "completed",
                                                           "figures": [{"path": "viz/a.html"}],
                                                           "ptools": []}]})
    out = raf.study_remote_figures("/ws", client=object(), slug="cd2-antibiotic-cocktail")
    assert out["available"] is True and out["reason"] == "ok"
    assert out["total_completed_remote_sims"] == 1   # only the cocktail-tagged sim
    assert out["total_figures_across_shown"] == 1


def test_study_remote_figures_reports_s3_auth_error(monkeypatch):
    """Candidates exist but every S3 walk comes back empty AND creds don't
    resolve -> reason 's3-auth-error', not the misleading 'no-figures'."""
    from vivarium_workbench.lib import remote_simulations as rs
    monkeypatch.setattr(rs, "list_remote_simulations",
                        lambda ws, **k: [_remote_row(1086, "cd2-antibiotic-cocktail")])
    monkeypatch.setattr(raf, "list_remote_analysis_figures",
                        lambda client, sid: {"available": True, "reason": "ok", "analyses": []})
    monkeypatch.setattr(raf, "_aws_creds_ok", lambda: False)
    out = raf.study_remote_figures("/ws", client=object(), slug="cd2-antibiotic-cocktail")
    assert out["available"] is False
    assert out["reason"] == "s3-auth-error"
    assert out["total_completed_remote_sims"] == 1


def test_study_remote_figures_no_figures_when_creds_ok(monkeypatch):
    """Same empty walk but creds DO resolve -> honest 'no-figures'."""
    from vivarium_workbench.lib import remote_simulations as rs
    monkeypatch.setattr(rs, "list_remote_simulations",
                        lambda ws, **k: [_remote_row(1086, "cd2-antibiotic-cocktail")])
    monkeypatch.setattr(raf, "list_remote_analysis_figures",
                        lambda client, sid: {"available": True, "reason": "ok", "analyses": []})
    monkeypatch.setattr(raf, "_aws_creds_ok", lambda: True)
    out = raf.study_remote_figures("/ws", client=object(), slug="cd2-antibiotic-cocktail")
    assert out["reason"] == "no-figures"
