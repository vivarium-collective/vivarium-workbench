from vivarium_workbench.lib.readouts_views import _merge_readouts, _observable_leaves


def test_node_internal_leaves_are_dropped():
    """Process/Step node wiring (config/inputs/outputs/interval/address) is not
    observable data — the Readouts panel must not list it."""
    leaves = [
        "net.OAA", "flux.net_carbon_flux", "feed.CO2", "global_time",  # real observables
        "emitter.address", "emitter.config.emit.OAA", "emitter.inputs.OAA",
        "network.config.k_rtca", "network.inputs.feed_CO2", "network.interval",
        "network.address",
    ]
    kept = _observable_leaves(leaves)
    assert set(kept) == {"net.OAA", "flux.net_carbon_flux", "feed.CO2", "global_time"}


def test_slash_form_authored_store_path_overlays_dotted_leaf():
    """An authored readout with a slash store_path (`net/OAA`) annotates the
    dotted emit leaf (`net.OAA`) with its units/description."""
    spec = {"readouts": [
        {"name": "OAA", "store_path": "net/OAA", "units": "molar", "description": "oxaloacetate"},
    ]}
    rows = _merge_readouts(spec, {"leaves": [
        "net.OAA", "emitter.address", "emitter.config.emit.OAA"]})
    assert len(rows) == 1                       # emitter node subtree dropped
    assert rows[0]["store_path"] == "net.OAA"
    assert rows[0]["units"] == "molar"
    assert rows[0]["description"] == "oxaloacetate"
    assert rows[0]["annotated"] is True



AVAIL = {"leaves": [
    "agents.0.listeners.mass.instantaneous_growth_rate",
    "agents.0.listeners.mass.cell_mass",
]}


def _row_by_path(rows, path):
    return next(r for r in rows if r["store_path"] == path)


def test_emit_leaves_become_rows_with_short_names():
    rows = _merge_readouts({"readouts": []}, AVAIL)
    paths = {r["store_path"] for r in rows}
    assert "agents.0.listeners.mass.cell_mass" in paths
    r = _row_by_path(rows, "agents.0.listeners.mass.cell_mass")
    assert r["name"] == "cell_mass"
    assert r["emit_status"] == "emitted"
    assert r["annotated"] is False


def test_authored_annotation_matches_by_lineage_stripped_path():
    spec = {"readouts": [{
        "name": "instantaneous_growth_rate", "status": "available",
        "store_path": "listeners.mass.instantaneous_growth_rate",
        "description": "the screen metric", "units": "1/s",
    }]}
    rows = _merge_readouts(spec, AVAIL)
    r = _row_by_path(rows, "agents.0.listeners.mass.instantaneous_growth_rate")
    assert r["name"] == "instantaneous_growth_rate"
    assert r["annotated"] is True
    assert r["description"] == "the screen metric"
    assert r["units"] == "1/s"
    assert r["emit_status"] == "emitted"
    # no duplicate raw row for the same leaf
    assert sum(1 for x in rows
               if x["store_path"].endswith("instantaneous_growth_rate")) == 1


def test_authored_available_not_in_plan_is_orphan():
    spec = {"readouts": [{
        "name": "phantom", "status": "available",
        "store_path": "listeners.does_not_exist",
    }]}
    rows = _merge_readouts(spec, AVAIL)
    r = _row_by_path(rows, "listeners.does_not_exist")
    assert r["emit_status"] == "not_in_emit_plan"
    assert r["annotated"] is True


def test_derived_metric_without_store_path_is_exempt():
    spec = {"readouts": [{
        "name": "effective_knob_count", "status": "derived-needed",
        "notes": "computed analysis scalar",
    }]}
    rows = _merge_readouts(spec, AVAIL)
    r = next(r for r in rows if r["name"] == "effective_knob_count")
    assert r["emit_status"] == "derived"
    assert r["store_path"] == ""
    assert r["annotated"] is True


def test_available_authored_without_store_path_flagged():
    spec = {"readouts": [{"name": "needs_path", "status": "available"}]}
    rows = _merge_readouts(spec, AVAIL)
    r = next(r for r in rows if r["name"] == "needs_path")
    assert r["emit_status"] == "not_in_emit_plan"
    assert r["store_path"] == ""


def test_legacy_observables_key_overlays(monkeypatch):
    """Fix 3: authored annotations under ``observables:`` still match emit leaves."""
    spec = {"observables": [{
        "name": "cm",
        "status": "available",
        "store_path": "listeners.mass.cell_mass",
    }]}
    rows = _merge_readouts(spec, AVAIL)
    r = _row_by_path(rows, "agents.0.listeners.mass.cell_mass")
    assert r["annotated"] is True
    assert r["name"] == "cm"
    assert r["emit_status"] == "emitted"


def test_duplicate_store_path_neither_flagged_not_in_emit_plan():
    """Fix 4: two authored readouts sharing a store_path that IS an emit leaf →
    neither should appear as not_in_emit_plan."""
    spec = {"readouts": [
        {"name": "cm_first", "status": "available",
         "store_path": "listeners.mass.cell_mass"},
        {"name": "cm_second", "status": "available",
         "store_path": "listeners.mass.cell_mass"},
    ]}
    rows = _merge_readouts(spec, AVAIL)
    orphans = [r for r in rows if r["emit_status"] == "not_in_emit_plan"]
    assert orphans == [], (
        f"Expected no not_in_emit_plan rows for dup store_path covered by emit leaf, "
        f"got: {orphans}"
    )


def test_build_study_readouts_honors_nested_workspace_layout(tmp_path):
    """A workspace.yaml that nests studies under workspace/studies (v2ecoli
    layout) must still resolve the study — not 404 'study not found'."""
    import yaml as _yaml
    from vivarium_workbench.lib.readouts_views import build_study_readouts

    (tmp_path / "workspace.yaml").write_text(_yaml.safe_dump({
        "name": "ws",
        "layout": {"studies": "workspace/studies",
                   "investigations": "workspace/investigations"},
    }))
    sd = tmp_path / "workspace" / "studies" / "demo"
    sd.mkdir(parents=True)
    # No baseline -> the worker returns 422 (found, but no composite), proving
    # the study was RESOLVED via the layout rather than 404'd.
    (sd / "study.yaml").write_text(_yaml.safe_dump({"name": "demo", "readouts": []}))

    body, status = build_study_readouts(tmp_path, "demo")
    assert status != 404, body
    assert status == 422, body  # found via layout, but no baseline composite
    assert body.get("error") != "study not found: demo"


def test_build_study_readouts_extracts_v4_conditions_baseline(tmp_path):
    """A schema_version 4 study carries its baseline composite under
    conditions.baseline.composite — the worker must project it (not 422 with
    'study has no baseline composite')."""
    import yaml as _yaml
    from vivarium_workbench.lib.readouts_views import build_study_readouts

    sd = tmp_path / "studies" / "v4demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(_yaml.safe_dump({
        "schema_version": 4,
        "name": "v4demo",
        "conditions": {"baseline": {"composite": "some.composite.ref"}},
        "readouts": [],
    }))
    body, status = build_study_readouts(tmp_path, "v4demo")
    # Baseline extraction must succeed (not 400 parse / not "no baseline"); the
    # ref then fails to build in a bare tmp workspace -> 422 with a note.
    assert body.get("error") != "study has no baseline composite", body
    assert status == 422, body
    assert body.get("composite") == "some.composite.ref", body


def test_readouts_remote_build_degrades_softly(tmp_path, monkeypatch):
    """On a remote build (.viv-build.json present), a composite-build failure is
    EXPECTED (no local ParCa cache) → soft 200 degrade with authored rows tagged
    'unverified', not a 422 error with the misleading 'not_in_emit_plan' tag."""
    import yaml as _yaml
    from vivarium_workbench.lib import readouts_views as rv

    sd = tmp_path / "studies" / "rdemo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(_yaml.safe_dump({
        "name": "rdemo",
        "baseline": [{"composite": "nonexistent.composite"}],
        "readouts": [{"name": "panel-x", "store_path": "listeners.foo.bar"}],
    }))
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66, "commit": "abc"}')
    monkeypatch.setattr(
        rv, "_available_observables_for_ref",
        lambda ws, ref: (_ for _ in ()).throw(FileNotFoundError("out/cache/initial_state.json")),
    )
    body, status = rv.build_study_readouts(tmp_path, "rdemo")
    assert status == 200, body
    assert body.get("remote_build") is True
    assert "remote build" in (body.get("note") or "").lower()
    panel = next(r for r in body["rows"] if r["name"] == "panel-x")
    assert panel["emit_status"] == "unverified"


# ---------------------------------------------------------------------------
# Readouts rebuild (Task 2, study-spine-reorg spec §3.1): emitter block +
# per-path dtype/shape/bytes.
# ---------------------------------------------------------------------------

def test_merge_readouts_emitted_rows_carry_dtype_and_shape():
    rows = _merge_readouts({"readouts": []}, AVAIL, n_steps=42)
    r = _row_by_path(rows, "agents.0.listeners.mass.cell_mass")
    assert r["dtype"] == "float64"
    assert r["shape"] == [42]
    assert r["bytes"] == 42 * 8


def test_merge_readouts_shape_symbolic_when_n_steps_unknown():
    rows = _merge_readouts({"readouts": []}, AVAIL)  # no n_steps passed
    r = _row_by_path(rows, "agents.0.listeners.mass.cell_mass")
    assert r["shape"] == ["n_steps"]
    assert "bytes" not in r  # unresolved dim -> best-effort bytes omitted


def test_merge_readouts_array_leaf_shape_from_catalog():
    avail = {
        "leaves": ["agents.0.listeners.mass.cell_mass"],
        "catalogs": {"listeners.mass.cell_mass": ["a", "b", "c"]},
    }
    rows = _merge_readouts({"readouts": []}, avail, n_steps=10)
    r = _row_by_path(rows, "agents.0.listeners.mass.cell_mass")
    assert r["shape"] == [10, 3]
    assert r["bytes"] == 10 * 3 * 8


def test_merge_readouts_derived_row_has_no_shape():
    spec = {"readouts": [{
        "name": "effective_knob_count", "status": "derived-needed",
    }]}
    rows = _merge_readouts(spec, AVAIL, n_steps=10)
    r = next(r for r in rows if r["name"] == "effective_knob_count")
    assert "shape" not in r
    assert "dtype" not in r


def _write_readouts_study(root, slug, extra_spec, ws_extra=None):
    import yaml as _yaml
    if ws_extra:
        (root / "workspace.yaml").write_text(_yaml.safe_dump({"name": "ws", **ws_extra}))
    sd = root / "studies" / slug
    sd.mkdir(parents=True)
    spec = {"name": slug, "readouts": []}
    spec.update(extra_spec)
    (sd / "study.yaml").write_text(_yaml.safe_dump(spec))
    return sd


def test_build_study_readouts_emitter_block_default_present(tmp_path):
    """Even a bare (no baseline) study gets an `emitter` block — the identity
    is computable from the spec/workspace alone, never gated on the composite
    build succeeding."""
    from vivarium_workbench.lib.readouts_views import build_study_readouts

    _write_readouts_study(tmp_path, "no-baseline", {})
    body, status = build_study_readouts(tmp_path, "no-baseline")
    assert status == 422, body
    em = body.get("emitter")
    assert isinstance(em, dict), body
    assert em.get("name")  # default emitter name present
    assert em.get("module")  # module path present
    assert em.get("scope") == "declared"


def test_build_study_readouts_emitter_honors_runtime_default_emitter(tmp_path):
    """A study's runtime.default_emitter overrides the workspace default —
    the emitter block must reflect the STUDY's declared choice."""
    from vivarium_workbench.lib.readouts_views import build_study_readouts

    _write_readouts_study(tmp_path, "sqlite-study",
                           {"runtime": {"default_emitter": "sqlite"}})
    body, status = build_study_readouts(tmp_path, "sqlite-study")
    assert status == 422, body
    em = body["emitter"]
    assert em["name"] == "sqlite"
    assert em["class_name"] == "SQLiteEmitter"
    assert em["output_kind"] == "sqlite"


def test_build_study_readouts_emitter_present_on_remote_degrade(tmp_path, monkeypatch):
    """The remote-build soft-degrade (200, .viv-build.json present) still
    carries the emitter block — spec §3.1 says ALWAYS render it."""
    from vivarium_workbench.lib import readouts_views as rv

    sd = _write_readouts_study(tmp_path, "remote-study", {
        "baseline": [{"composite": "nonexistent.composite"}],
    })
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 1, "commit": "abc"}')
    monkeypatch.setattr(
        rv, "_available_observables_for_ref",
        lambda ws, ref: (_ for _ in ()).throw(FileNotFoundError("no cache")),
    )
    body, status = rv.build_study_readouts(tmp_path, "remote-study")
    assert status == 200, body
    assert isinstance(body.get("emitter"), dict), body
    assert body["emitter"].get("name")


def test_build_study_readouts_local_degrade_still_carries_emitter(tmp_path, monkeypatch):
    from vivarium_workbench.lib import readouts_views as rv

    sd = _write_readouts_study(tmp_path, "local-study", {
        "baseline": [{"composite": "nonexistent.composite"}],
    })
    monkeypatch.setattr(
        rv, "_available_observables_for_ref",
        lambda ws, ref: (_ for _ in ()).throw(FileNotFoundError("no cache")),
    )
    body, status = rv.build_study_readouts(tmp_path, "local-study")
    assert status == 422, body
    assert isinstance(body.get("emitter"), dict), body


def test_readouts_local_build_failure_still_422_but_unverified(tmp_path, monkeypatch):
    """A LOCAL workspace (no .viv-build.json) where the composite fails to build
    keeps the hard 422 (a real problem to fix), but the authored row is tagged
    'unverified' (it wasn't checked) rather than the misleading 'not_in_emit_plan'."""
    import yaml as _yaml
    from vivarium_workbench.lib import readouts_views as rv

    sd = tmp_path / "studies" / "ldemo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(_yaml.safe_dump({
        "name": "ldemo",
        "baseline": [{"composite": "nonexistent.composite"}],
        "readouts": [{"name": "panel-y", "store_path": "listeners.foo.baz"}],
    }))
    monkeypatch.setattr(
        rv, "_available_observables_for_ref",
        lambda ws, ref: (_ for _ in ()).throw(FileNotFoundError("out/cache/initial_state.json")),
    )
    body, status = rv.build_study_readouts(tmp_path, "ldemo")
    assert status == 422, body
    panel = next(r for r in body["rows"] if r["name"] == "panel-y")
    assert panel["emit_status"] == "unverified"
