"""attach_process_docs: resolve per-process docstrings for the inspector."""
from vivarium_workbench.lib.process_docs import (
    attach_process_docs,
    _doc_for_address,
    _contract_for_class,
    _math_to_mathml,
)


class _WithLatexMath:
    """A contract whose governing equations are heavy LaTeX (like the Tellurium
    SBML bridge) — the case that used to leak raw \\frac onto the Model view."""

    contract = {
        "summary": "Integrates an SBML model.",
        "math": [
            r"\frac{dX}{dt} = f(X,\, p;\, t) = S\, v(X,\, p)",
            r"X(t + \Delta t) = \mathrm{CVODE}\big(f,\, X(t),\, [t,\, t + \Delta t]\big)",
            r"\text{coupled mode: } X_{n+1} = X_n + \textstyle\sum_i \big(X^{(i)} - X_n\big)",
        ],
    }


# Tokens that mean LaTeX leaked through unrendered — none may survive into the
# MathML the Model view inserts.
_RAW_LATEX = (r"\frac", r"\mathrm", r"\sum", r"\Delta", r"\big", r"\text", r"\textstyle")


def test_contract_math_renders_to_mathml_no_raw_latex():
    """GUARD: heavy-LaTeX equations must render to MathML with no raw source left.

    This is what keeps 'raw \\frac on the Model tab' from ever shipping again — a
    regression here (lib removed, renderer broken) fails CI loudly.
    """
    c = _contract_for_class(_WithLatexMath)
    assert c is not None
    assert len(c["math_mathml"]) == len(c["math"])
    for src, mathml in zip(c["math"], c["math_mathml"]):
        assert mathml.lstrip().startswith("<math"), f"did not render to MathML: {src!r}"
        for tok in _RAW_LATEX:
            assert tok not in mathml, f"raw LaTeX {tok!r} leaked into MathML for {src!r}"


def test_math_to_mathml_degrades_on_bad_input():
    # Empty / unparseable input returns '' so the client falls back to plain text,
    # never emitting a broken <math> fragment.
    assert _math_to_mathml("") == ""
    assert _math_to_mathml("   ") == ""


from vivarium_workbench.lib.process_docs import attach_model_antimony


def test_attach_model_antimony_inline_config():
    # A process whose config IS the Antimony (model_format=antimony) gets it shown
    # verbatim — no files, no converter needed.
    doc = {"tell": {"_type": "process", "address": "local:T",
                    "config": {"model": "S1 -> S2; k = 0.3", "model_format": "antimony"}}}
    attach_model_antimony("/nonexistent", doc)
    assert doc["tell"]["_antimony"]["format"] == "antimony"
    assert "S1 -> S2" in doc["tell"]["_antimony"]["text"]


def test_attach_model_antimony_prefers_sibling_ant(tmp_path):
    # An SBML model_file with a sibling .ant shows the human-authored Antimony,
    # and resolves by basename even when the stored path is absolute/stale.
    (tmp_path / "m.xml").write_text("<sbml/>", encoding="utf-8")
    (tmp_path / "m.ant").write_text("S1 -> S2; k = 0.3", encoding="utf-8")
    doc = {"tell": {"_type": "process",
                    "config": {"model_file": "/stale/ci/path/m.xml", "model_format": "sbml"}}}
    attach_model_antimony(tmp_path, doc)
    assert "S1 -> S2" in doc["tell"]["_antimony"]["text"]
    assert doc["tell"]["_antimony"]["source"].endswith(".ant")


def test_attach_model_antimony_absent_when_no_model():
    doc = {"math": {"_type": "process", "config": {"expressions": []}}}
    attach_model_antimony("/x", doc)
    assert "_antimony" not in doc["math"]


class _WithFullContract:
    """A class declaring a full (plain-dict) contract, like a ProcessContract.to_dict()."""

    contract = {
        "summary": "Binds ligand to TF.",
        "description": "Longer prose.",
        "status": "",
        "math": ["dX/dt = -k*X"],
        "symbols": {"X": "species count (counts)"},
        "inputs": {"bulk": "Reads ligand + TF counts."},
        "outputs": {"bulk": "Writes the binding delta."},
        "config": {"rate": "Binding rate k (1/s)."},
        "assumptions": ["Fast reactions reach equilibrium each step."],
        "references": ["Sekimizu 1987."],
    }


class _NoContract:
    pass


def test_contract_for_class_forwards_full_contract():
    # The loom Inspector renders per-port inputs/outputs, assumptions and
    # references — _contract_for_class must FORWARD them, not drop them.
    c = _contract_for_class(_WithFullContract)
    assert c is not None
    assert c["inputs"] == {"bulk": "Reads ligand + TF counts."}
    assert c["outputs"] == {"bulk": "Writes the binding delta."}
    assert c["config"] == {"rate": "Binding rate k (1/s)."}
    assert c["assumptions"] == ["Fast reactions reach equilibrium each step."]
    assert c["references"] == ["Sekimizu 1987."]
    assert c["math"] == ["dX/dt = -k*X"]
    assert c["symbols"] == {"X": "species count (counts)"}


def test_contract_for_class_none_without_summary():
    # No declared contract (or no summary) → None, so no Contract callout renders.
    assert _contract_for_class(_NoContract) is None
    class _NoSummary:
        contract = {"math": ["x=1"]}
    assert _contract_for_class(_NoSummary) is None


def test_doc_for_address_resolves_full_dotted_path():
    # json.JSONDecoder has a docstring; a full local:<module>.<Class> resolves.
    assert _doc_for_address("local:json.JSONDecoder")


def test_doc_for_address_skips_bare_and_bad():
    assert _doc_for_address("local:SQLiteEmitter") == ""   # bare registry name
    assert _doc_for_address("") == ""
    assert _doc_for_address("not-an-address") == ""
    assert _doc_for_address("local:nonexistent.module.Cls") == ""


def test_attach_only_processes_get_doc():
    doc = {"state": {
        "p": {"_type": "process", "address": "local:json.JSONDecoder"},
        "s": {"_type": "store", "x": 1},
        "lst": [1, {"_type": "step", "address": "local:json.JSONEncoder"}],
    }}
    attach_process_docs(doc)
    assert doc["state"]["p"].get("doc")          # process gets a doc
    assert "doc" not in doc["state"]["s"]        # store does not
    assert doc["state"]["lst"][1].get("doc")     # step nested in a list does


def test_attach_is_safe_on_arbitrary_json():
    # must never raise, even on weird shapes
    attach_process_docs({"a": [1, 2, {"b": None}], "c": "x"})
    attach_process_docs([])
    attach_process_docs(None)


def test_existing_doc_not_overwritten():
    doc = {"_type": "process", "address": "local:json.JSONDecoder", "doc": "custom"}
    attach_process_docs(doc)
    assert doc["doc"] == "custom"


from vivarium_workbench.lib.process_docs import summarize_large_values


def test_summarize_collapses_large_lists_keeps_structure():
    doc = {"state": {"agents": {"0": {
        "bulk": [["x", 0, 0.0]] * 100,                       # big array → summary
        "small": [1, 2, 3],                                   # small → kept
        "p": {"_type": "process", "address": "local:json.JSONDecoder",
              "inputs": {"a": ["bulk"]}},                     # wiring untouched
    }}}}
    out = summarize_large_values(doc, max_list=40)
    a = out["state"]["agents"]["0"]
    assert a["bulk"] == "⟨100 items⟩"
    assert a["small"] == [1, 2, 3]
    assert a["p"]["inputs"] == {"a": ["bulk"]}   # process wiring preserved


def test_summarize_handles_tuples_and_long_strings():
    assert summarize_large_values(tuple(range(50)), max_list=40) == "⟨50 items⟩"
    assert summarize_large_values("x" * 5000, max_str=100).endswith("…")


from vivarium_workbench.lib.process_docs import attach_contract_mathml


def test_attach_contract_mathml_fills_env_worker_contract():
    # Simulates the env worker (older workbench) returning a _contract with `math`
    # but no `math_mathml`: the in-process pass must render it so the Model view
    # shows equations even when the worker predates _math_to_mathml.
    doc = {"tell": {"_type": "process",
                    "_contract": {"summary": "x", "math": [r"\frac{dX}{dt} = f(X,\, p)"]}}}
    attach_contract_mathml(doc)
    ml = doc["tell"]["_contract"]["math_mathml"]
    assert len(ml) == 1 and ml[0].lstrip().startswith("<math") and "\\frac" not in ml[0]


def test_attach_contract_mathml_idempotent():
    doc = {"tell": {"_type": "process",
                    "_contract": {"summary": "x", "math": ["a"], "math_mathml": ["<math>keep</math>"]}}}
    attach_contract_mathml(doc)
    assert doc["tell"]["_contract"]["math_mathml"] == ["<math>keep</math>"]
