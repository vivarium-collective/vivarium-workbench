from vivarium_dashboard.lib.run_commands import study_run_commands


def test_baseline_and_variant_commands():
    spec = {
        "name": "demo-study",
        "conditions": {
            "baseline": {"composite": "pkg.composites.baseline"},
            "variants": [
                {"name": "knockout", "parameter_overrides": {"k": 1}},
            ],
        },
        "simulation_set": [
            {"name": "ensemble-a", "base_model": "baseline"},
        ],
    }
    cmds = study_run_commands(spec, "demo-study")
    assert cmds["baseline"] == "vdash run study demo-study"
    assert cmds["variants"] == [
        {"name": "knockout",
         "cmd": "vdash run study demo-study --variant knockout"}
    ]
    assert cmds["simulations"] == [
        {"name": "ensemble-a", "cmd": "vdash run study demo-study"}
    ]
    assert cmds["rerun_hint"] == "vdash rerun <run-id>"


def test_no_variants_no_simset():
    cmds = study_run_commands({"name": "s"}, "s")
    assert cmds["baseline"] == "vdash run study s"
    assert cmds["variants"] == []
    assert cmds["simulations"] == []
