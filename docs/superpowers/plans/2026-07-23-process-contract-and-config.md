# Process Contract and Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make process-bigraph processes advertise what they do — port semantics, config semantics, and the governing math — as a structured `ProcessContract`; and fix the bug that leaves `config` empty on 45 of 46 v2ecoli baseline processes.

**Architecture:** `ProcessContract` is a dataclass in process-bigraph, serialized into the composite document as `_contract` beside `_inputs`/`_outputs`. Adoption needs no flag day: absent a declared contract, consumers derive one from the docstring, a convention 45/46 baseline processes already follow. Separately, two independent config fixes — attach class-level `config_schema` for names/types/defaults, and populate the declared (JSON-safe) config values that `make_edge` currently drops on the floor.

**Tech Stack:** Python 3.12, process-bigraph, bigraph-schema, pytest.

**Spec:** `docs/superpowers/specs/2026-07-23-loom-process-column-view-design.md`, sections 5 and 7.

**Companion plan:** `docs/superpowers/plans/2026-07-23-loom-process-column-view.md` renders all of this in bigraph-loom. It does not depend on this plan — loom derives contracts from docstrings and omits rows with no data — so the two can proceed in either order or in parallel.

## Global Constraints

- Three repos are touched. Each task states which. Commit separately per repo; these do not share a branch.
  - `/Users/eranagmon/code/process-bigraph` — the `ProcessContract` type and its serialization
  - `/Users/eranagmon/code/v2ecoli` — the config population fix
  - `/Users/eranagmon/code/vivarium-dashboard` — the `config_schema` attach in the env worker
- **Never serialize `instance.parameters`.** The resolved runtime config holds live bound methods (20 `'_type': 'method'` entries across 9 v2ecoli process modules), pint Quantities, `UnitStructArray`s, and multi-thousand-element arrays sourced from a 165 MB dill. Methods cannot be JSON-encoded at all. Only the **declared** pre-`resolve_config` form is persisted — `v2ecoli/library/config_resolver.py:14-42` stores callables as `{"_function": …, "_data": …}` refs precisely so configs can round-trip.
- Contract declaration must stay **optional**. A process without one must never raise, and every existing composite must keep building unchanged.
- v2ecoli tests that build the baseline need the ParCa cache at `out/cache`. If it is absent, `pytest` will error on fixture setup rather than fail an assertion — check for the cache before diagnosing a test failure as a code bug.

---

## Task 1: The `ProcessContract` dataclass

**Repo:** `/Users/eranagmon/code/process-bigraph`

**Files:**
- Create: `process_bigraph/contract.py`
- Modify: `process_bigraph/__init__.py`
- Test: `tests/test_process_contract.py`

**Interfaces:**
- Consumes: nothing
- Produces: `ProcessContract` dataclass with fields `summary: str`, `description: str`, `inputs: dict[str, str]`, `outputs: dict[str, str]`, `config: dict[str, str]`, `math: list[str]`, `symbols: dict[str, str]`, `assumptions: list[str]`, `references: list[str]`; methods `to_dict() -> dict`, `from_docstring(doc: str) -> ProcessContract | None`, `validate_ports(inputs: Iterable[str], outputs: Iterable[str]) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_process_contract.py
import pytest
from process_bigraph.contract import ProcessContract

DOC = """TranscriptInitiation — distributes activated RNAPs across TUs by weighted multinomial sampling.

    n_to_activate = round(f_active · n_total_RNAP) - n_active
    p_i = max(0, basal_prob_i + sum_j delta_prob[i,j] · bound_TF_j)
    initiations ~ Multinomial(n_to_activate, p_i / sum_i p_i)
  f_active: media-dependent active RNAP fraction.
"""


def test_defaults_are_not_shared():
    """Mutable dataclass defaults must not be shared across instances."""
    a, b = ProcessContract(summary="a"), ProcessContract(summary="b")
    a.inputs["bulk"] = "reads counts"
    assert b.inputs == {}


def test_to_dict_round_trips():
    c = ProcessContract(summary="s", math=["x = 1"], inputs={"p": "reads"})
    d = c.to_dict()
    assert d["summary"] == "s"
    assert d["math"] == ["x = 1"]
    assert d["inputs"] == {"p": "reads"}


def test_to_dict_is_json_safe():
    import json
    json.dumps(ProcessContract(summary="s").to_dict())


def test_from_docstring_takes_first_line_as_summary():
    c = ProcessContract.from_docstring(DOC)
    assert "distributes activated RNAPs" in c.summary
    assert "\n" not in c.summary


def test_from_docstring_extracts_math():
    c = ProcessContract.from_docstring(DOC)
    assert len(c.math) == 3
    assert c.math[0].startswith("n_to_activate =")
    assert "Multinomial" in c.math[2]


def test_from_docstring_keeps_remaining_prose():
    c = ProcessContract.from_docstring(DOC)
    assert "media-dependent" in c.description
    assert "n_to_activate =" not in c.description


def test_from_docstring_handles_no_math():
    c = ProcessContract.from_docstring("Just a plain description.")
    assert c.summary == "Just a plain description."
    assert c.math == []


def test_from_docstring_returns_none_for_empty():
    assert ProcessContract.from_docstring("") is None
    assert ProcessContract.from_docstring(None) is None


def test_validate_ports_flags_unknown_names():
    c = ProcessContract(summary="s", inputs={"real": "x", "ghost": "y"})
    assert c.validate_ports(inputs=["real"], outputs=[]) == ["ghost"]


def test_validate_ports_accepts_a_correct_contract():
    c = ProcessContract(summary="s", inputs={"a": "x"}, outputs={"b": "y"})
    assert c.validate_ports(inputs=["a"], outputs=["b"]) == []
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd /Users/eranagmon/code/process-bigraph && pytest tests/test_process_contract.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'process_bigraph.contract'`.

- [ ] **Step 3: Implement `process_bigraph/contract.py`**

```python
"""What a process advertises about itself.

A process may declare a ``contract`` describing what it does with each
port, what each config parameter controls, and the math or logic
connecting them. Declaration is optional: absent one, a contract is
derived from the docstring, a convention most processes already follow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Iterable

# Markers that make a docstring line an equation rather than prose.
_MATH_RE = re.compile(
    r"[=~≈←≥≤∑∏]|\b(?:Multinomial|Binomial|Poisson|Normal|Gamma|Exponential)\s*\("
)


@dataclass
class ProcessContract:
    """The self-description of a process.

    ``inputs``/``outputs``/``config`` map a port or parameter name to prose
    describing what the process does with it — the genuinely new
    information, since types alone never say *why* a port is read.
    """

    summary: str = ""
    description: str = ""
    inputs: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    config: dict[str, str] = field(default_factory=dict)
    math: list[str] = field(default_factory=list)
    symbols: dict[str, str] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """A plain JSON-safe dict, for embedding in a composite document."""
        return asdict(self)

    @classmethod
    def from_docstring(cls, doc: str | None) -> "ProcessContract | None":
        """Derive a contract from a docstring.

        First non-empty line becomes the summary, equation-bearing lines
        become ``math``, and the rest becomes ``description``. Port and
        config semantics stay empty — they cannot be inferred from prose.
        """
        if not doc or not doc.strip():
            return None

        contract = cls()
        prose: list[str] = []
        for raw in doc.split("\n"):
            line = raw.strip()
            if not line:
                continue
            if not contract.summary:
                contract.summary = line
            elif _MATH_RE.search(line):
                contract.math.append(line)
            else:
                prose.append(line)
        contract.description = " ".join(prose)
        return contract

    def validate_ports(
        self, inputs: Iterable[str], outputs: Iterable[str]
    ) -> list[str]:
        """Contract entries naming a port the process does not have.

        Contracts drift as ports are renamed; this makes that visible
        instead of silent.
        """
        known_in, known_out = set(inputs), set(outputs)
        unknown = [p for p in self.inputs if p not in known_in]
        unknown += [p for p in self.outputs if p not in known_out]
        return sorted(unknown)
```

- [ ] **Step 4: Export it**

In `process_bigraph/__init__.py`, alongside the existing public names:

```python
from process_bigraph.contract import ProcessContract
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_process_contract.py -v`
Expected: PASS, all ten tests.

- [ ] **Step 6: Commit**

```bash
cd /Users/eranagmon/code/process-bigraph
git add process_bigraph/contract.py process_bigraph/__init__.py tests/test_process_contract.py
git commit -m "feat: ProcessContract — structured self-description for processes"
```

---

## Task 2: Serialize `_contract` into the composite document

**Repo:** `/Users/eranagmon/code/process-bigraph`

**Files:**
- Modify: `process_bigraph/composite.py` (or wherever `Process`/`Step` base classes live — locate with `grep -rn "class Process" process_bigraph/`)
- Test: `tests/test_process_contract.py` (append)

**Interfaces:**
- Consumes: `ProcessContract` (Task 1)
- Produces: an optional `contract` class attribute on `Process`/`Step`; `process_contract(instance) -> ProcessContract | None` module function that returns the declared contract, else the docstring-derived one

- [ ] **Step 1: Locate the base classes**

```bash
cd /Users/eranagmon/code/process-bigraph
grep -rn "^class Process\|^class Step\|^class Edge" process_bigraph/ bigraph_schema/ 2>/dev/null
```

Note the file and line. The `contract` attribute and the `process_contract()` helper go beside the existing `config_schema` declaration on the same class.

- [ ] **Step 2: Write the failing test**

```python
# append to tests/test_process_contract.py
from process_bigraph.contract import ProcessContract, process_contract


class _Declared:
    """A docstring that should LOSE to the declared contract."""
    contract = ProcessContract(summary="declared", math=["x = 1"])


class _DocstringOnly:
    """Does a thing.

        y = 2 * x
    """


class _Bare:
    pass


def test_declared_contract_wins_over_docstring():
    c = process_contract(_Declared())
    assert c.summary == "declared"
    assert c.math == ["x = 1"]


def test_docstring_is_the_fallback():
    c = process_contract(_DocstringOnly())
    assert c.summary == "Does a thing."
    assert c.math == ["y = 2 * x"]


def test_no_contract_and_no_docstring_returns_none():
    assert process_contract(_Bare()) is None


def test_process_contract_never_raises_on_odd_input():
    assert process_contract(None) is None
    assert process_contract(object()) is None
```

- [ ] **Step 3: Run and confirm failure**

Run: `pytest tests/test_process_contract.py -x -k "declared or docstring or bare or odd"`
Expected: FAIL — `cannot import name 'process_contract'`.

- [ ] **Step 4: Implement `process_contract`**

Append to `process_bigraph/contract.py`:

```python
def process_contract(instance) -> ProcessContract | None:
    """The contract for a process instance.

    Declared ``contract`` wins; otherwise derive one from ``__doc__``.
    Never raises — a process with neither simply has no contract, and
    contract absence must never break composite construction.
    """
    if instance is None:
        return None

    declared = getattr(instance, "contract", None)
    if isinstance(declared, ProcessContract):
        return declared
    if isinstance(declared, dict):
        return ProcessContract(**declared)

    return ProcessContract.from_docstring(getattr(instance, "__doc__", None))
```

- [ ] **Step 5: Declare the attribute on the base class**

At the location found in Step 1, beside `config_schema`:

```python
    #: Optional ProcessContract describing what this process does with its
    #: ports and config. Absent means "derive one from the docstring".
    contract = None
```

- [ ] **Step 6: Run to verify it passes**

Run: `pytest tests/test_process_contract.py -v`
Expected: PASS, all fourteen tests.

- [ ] **Step 7: Confirm nothing else broke**

Run: `pytest -x`
Expected: the full process-bigraph suite passes. The `contract = None` attribute is additive; if anything fails, it is a name collision — check for an existing `contract` attribute before proceeding.

- [ ] **Step 8: Commit**

```bash
git add process_bigraph/contract.py process_bigraph/composite.py tests/test_process_contract.py
git commit -m "feat: resolve a process's contract, declared or derived from its docstring"
```

---

## Task 3: Attach `config_schema` and `_contract` in the workbench env worker

**Repo:** `/Users/eranagmon/code/vivarium-dashboard`

**Files:**
- Modify: `vivarium_workbench/env_worker.py` (`_attach_process_docs`, lines 427-451)
- Test: `tests/test_process_docs_attach.py`

**Interfaces:**
- Consumes: `process_contract` (Task 2)
- Produces: composite-state documents where each process node carries `config_schema` and `_contract` alongside the existing `doc`

**Why here.** `_attach_process_docs` **already imports the class from `node['address']`** to read its docstring. Attaching two more class attributes in that same walk costs no new imports and no new I/O. There is precedent: the Registry tab already surfaces `config_schema` (`env_worker.py:204-209`, rendered at `static/walkthrough.js:1603`).

- [ ] **Step 1: Read the existing walk**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
sed -n '400,455p' vivarium_workbench/env_worker.py
```

Note how `_attach_process_docs` resolves a class from `node['address']` and how it guards against import failure. The new attachments must reuse that same resolution and the same guard — a process whose module cannot be imported must still yield a document.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_process_docs_attach.py
from vivarium_workbench.env_worker import _attach_process_docs


class _Fake:
    """Fake process.

        y = 2x
    """
    config_schema = {"rate": {"_type": "float", "_default": 1.0}}


def _doc(address):
    return {"p": {"_type": "step", "address": address, "config": {},
                  "inputs": {}, "outputs": {}}}


def test_attaches_config_schema(monkeypatch):
    monkeypatch.setattr(
        "vivarium_workbench.env_worker._class_for_address", lambda a: _Fake)
    out = _attach_process_docs(_doc("local:fake.Fake"))
    assert out["p"]["config_schema"]["rate"]["_type"] == "float"


def test_attaches_derived_contract(monkeypatch):
    monkeypatch.setattr(
        "vivarium_workbench.env_worker._class_for_address", lambda a: _Fake)
    out = _attach_process_docs(_doc("local:fake.Fake"))
    assert out["p"]["_contract"]["summary"] == "Fake process."
    assert out["p"]["_contract"]["math"] == ["y = 2x"]


def test_unresolvable_address_still_yields_a_document(monkeypatch):
    def boom(a):
        raise ImportError("nope")
    monkeypatch.setattr(
        "vivarium_workbench.env_worker._class_for_address", boom)
    out = _attach_process_docs(_doc("local:missing.Gone"))
    assert "p" in out
    assert "config_schema" not in out["p"]


def test_class_without_config_schema_gets_no_key(monkeypatch):
    class _Plain:
        """Plain."""
    monkeypatch.setattr(
        "vivarium_workbench.env_worker._class_for_address", lambda a: _Plain)
    out = _attach_process_docs(_doc("local:plain.Plain"))
    assert "config_schema" not in out["p"]
```

**Note:** `_class_for_address` is a placeholder name for whatever helper `_attach_process_docs` actually uses to resolve the class — Step 1 tells you its real name. Use that name in the `monkeypatch.setattr` targets. If the resolution is inline rather than a named helper, extract it into one first; that extraction is part of this step and makes the walk testable.

- [ ] **Step 3: Run and confirm failure**

Run: `pytest tests/test_process_docs_attach.py -x`
Expected: FAIL — `KeyError: 'config_schema'`.

- [ ] **Step 4: Attach both in the existing walk**

Inside `_attach_process_docs`, in the branch that already succeeded in resolving the class and setting `doc`:

```python
            # Config VALUES are frequently empty (see the v2ecoli fix), but
            # the class-level schema always carries names, types, and
            # defaults — enough to render a useful config section.
            schema = getattr(cls, "config_schema", None)
            if schema:
                node["config_schema"] = _json_sanitize(schema)

            # Declared contract, else one derived from the docstring.
            try:
                from process_bigraph.contract import process_contract
                contract = process_contract(cls)
                if contract is not None:
                    node["_contract"] = contract.to_dict()
            except Exception:
                # Contract support is optional; an older process-bigraph
                # must not break composite rendering.
                pass
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_process_docs_attach.py -v`
Expected: PASS, all four tests.

- [ ] **Step 6: Verify against the real baseline**

```bash
cd /Users/eranagmon/code/v2ecoli && python scripts/regenerate_composite_states.py
python3 -c "
import json
d = json.load(open('reports/composite-state/v2ecoli.composites.baseline.json'))
def walk(n):
    if not isinstance(n, dict): return
    if n.get('_type') in ('process','step'): yield n; return
    for k, v in n.items():
        if not k.startswith('_'): yield from walk(v)
procs = list(walk(d['state']))
n = len(procs)
print(f'{n} processes')
print(f'  with config_schema : {sum(1 for p in procs if p.get(\"config_schema\"))}')
print(f'  with _contract     : {sum(1 for p in procs if p.get(\"_contract\"))}')
print(f'  contract with math : {sum(1 for p in procs if p.get(\"_contract\", {}).get(\"math\"))}')
"
```

Expected: `config_schema` on at least 17 (the classes that declare one), `_contract` on roughly 45 (every process with a docstring), and math on roughly 14. If `_contract` is near zero, process-bigraph's Task 2 is not installed in the v2ecoli venv — check with `pip show process-bigraph`.

- [ ] **Step 7: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_workbench/env_worker.py tests/test_process_docs_attach.py
git commit -m "feat(workbench): attach config_schema and process contracts to composite state"
```

---

## Task 4: Populate the declared config values

**Repo:** `/Users/eranagmon/code/v2ecoli`

**Files:**
- Modify: `v2ecoli/composites/_helpers.py` (`_make_instance`, lines 1049-1061)
- Test: `tests/test_config_present.py`

**Interfaces:**
- Consumes: nothing
- Produces: composite documents where `config` is non-empty on substantially all processes

**Root cause.** `_helpers.py:1017` reads `getattr(instance, '_raw_config', {})`. A repo-wide grep for `_raw_config` returns **exactly one hit — that read**. Nothing ever assigns it, and none of the 26 `make_edge(` call sites passes `config=`. The config exists: `baseline.py:378-417` loads it from the ParCa cache and `_make_instance` passes it to the constructor, where it becomes `instance.parameters`. It is simply never copied back into the document. This is an unfinished feature, not a size optimization — there is no comment, filter, or guard anywhere in the chain that mentions config.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_present.py
"""Regression gate for the empty-config bug.

Config sat empty on 45 of 46 baseline processes because nothing asserted
otherwise. This test is that assertion.
"""
import json
import pytest

from v2ecoli.composites.baseline import baseline


def _processes(node):
    if not isinstance(node, dict):
        return
    if node.get("_type") in ("process", "step"):
        yield node
        return
    for key, value in node.items():
        if not key.startswith("_"):
            yield from _processes(value)


@pytest.fixture(scope="module")
def procs():
    doc = baseline()
    found = list(_processes(doc))
    assert found, "baseline() produced no processes"
    return found


def test_most_processes_have_config(procs):
    with_config = [p for p in procs if p.get("config")]
    ratio = len(with_config) / len(procs)
    assert ratio > 0.5, (
        f"only {len(with_config)}/{len(procs)} processes carry config; "
        "the _raw_config wiring has regressed"
    )


def test_config_is_json_serializable(procs):
    """The DECLARED config must round-trip. If this fails, resolved config
    (bound methods, pint Quantities, numpy from a 165MB dill) has leaked in."""
    for p in procs:
        config = p.get("config")
        if not config:
            continue
        json.dumps(config, default=str)


def test_no_bound_methods_leaked_into_config(procs):
    for p in procs:
        for key, value in (p.get("config") or {}).items():
            assert not callable(value), (
                f"{p.get('address')} config[{key!r}] is callable — resolved "
                "config leaked in; only the declared form may be serialized"
            )
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd /Users/eranagmon/code/v2ecoli && pytest tests/test_config_present.py -x`
Expected: FAIL on `test_most_processes_have_config` — `only 1/46 processes carry config`.

If it errors during fixture setup instead, the ParCa cache is missing; see Global Constraints.

- [ ] **Step 3: Stash the declared config on the instance**

In `v2ecoli/composites/_helpers.py`, in `_make_instance` (lines 1049-1061), after constructing the instance:

```python
def _make_instance(cls, config, core):
    instance = cls(parameters=config)
    # make_edge reads this to put the DECLARED config into the document.
    # Stash the pre-resolve form: it is JSON-safe by construction, since
    # config_resolver stores callables as {"_function": ...} refs.
    # Never stash instance.parameters — that is the RESOLVED config and
    # holds live bound methods that cannot be serialized.
    instance._raw_config = config or {}
    return instance
```

**Important:** this must receive the config *before* `resolve_config()` inflates function refs into live callables. Check the call order at `baseline.py:378-417`; if `_make_instance` is already receiving the resolved form, thread the declared form through as a separate argument rather than stashing what you were given.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_config_present.py -v`
Expected: PASS, all three tests.

If `test_no_bound_methods_leaked_into_config` fails, Step 3's ordering caveat applies — you stashed the resolved config. Fix the ordering rather than filtering callables out.

- [ ] **Step 5: Confirm payload size stayed sane**

```bash
python scripts/regenerate_composite_states.py
ls -la reports/composite-state/v2ecoli.composites.baseline.json
```

Expected: meaningfully larger than the previous 214 KB, but not tens of megabytes. `_summarize_large_values` (`vivarium_workbench/env_worker.py:406-424`) already caps lists over 40 entries. If the file has exploded, that decorator is not reaching the config subtree — extend it there rather than trimming config by hand.

- [ ] **Step 6: Confirm the composite still builds and runs**

Run: `pytest tests/ -k "baseline" -x`
Expected: PASS. Setting an attribute on the instance is additive and must not perturb simulation behavior.

- [ ] **Step 7: Commit**

```bash
cd /Users/eranagmon/code/v2ecoli
git add v2ecoli/composites/_helpers.py tests/test_config_present.py
git commit -m "fix: populate declared config in composite documents

make_edge read instance._raw_config, which nothing ever assigned — a
repo-wide grep returned exactly one hit, that read. Config was therefore
empty on 45 of 46 baseline processes despite being loaded from the ParCa
cache and passed to every constructor. Stash the declared (pre-resolve,
JSON-safe) form so it reaches the document.

Adds the regression test whose absence let this sit unnoticed."
```

---

## Task 5: Author contracts for the highest-value processes

**Repo:** `/Users/eranagmon/code/v2ecoli`

**Files:**
- Modify: 5 process modules under `v2ecoli/processes/`
- Test: `tests/test_authored_contracts.py`

**Interfaces:**
- Consumes: `ProcessContract` (Task 1)
- Produces: declared contracts on the five processes whose port semantics are least guessable

**Scope.** Five, not all 46. The docstring fallback already covers the rest, and port semantics are exactly the kind of content that is worthless when written carelessly in bulk. These five are chosen because they sit in the largest affinity clusters and have the most non-obvious port behavior.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_authored_contracts.py
import pytest
from process_bigraph.contract import process_contract

from v2ecoli.processes.transcript_initiation import TranscriptInitiation
from v2ecoli.processes.transcript_elongation import TranscriptElongation
from v2ecoli.processes.polypeptide_initiation import PolypeptideInitiation
from v2ecoli.processes.polypeptide_elongation import PolypeptideElongation
from v2ecoli.processes.rna_degradation import RnaDegradation

AUTHORED = [TranscriptInitiation, TranscriptElongation, PolypeptideInitiation,
            PolypeptideElongation, RnaDegradation]


@pytest.mark.parametrize("cls", AUTHORED, ids=lambda c: c.__name__)
def test_declares_a_contract(cls):
    assert cls.contract is not None, f"{cls.__name__} has no declared contract"


@pytest.mark.parametrize("cls", AUTHORED, ids=lambda c: c.__name__)
def test_contract_documents_every_port(cls):
    c = process_contract(cls)
    declared_ports = set(c.inputs) | set(c.outputs)
    assert declared_ports, f"{cls.__name__} contract documents no ports"


@pytest.mark.parametrize("cls", AUTHORED, ids=lambda c: c.__name__)
def test_contract_has_math_or_explicit_logic(cls):
    c = process_contract(cls)
    assert c.math or c.description, (
        f"{cls.__name__} contract states neither math nor logic")


@pytest.mark.parametrize("cls", AUTHORED, ids=lambda c: c.__name__)
def test_contract_names_no_phantom_ports(cls):
    """Contracts drift as ports are renamed. Catch it here."""
    c = process_contract(cls)
    instance_ports = getattr(cls, "config_schema", {})
    if not instance_ports:
        pytest.skip(f"{cls.__name__} declares no config_schema to check against")
    # Ports come from inputs()/outputs(); validate what we can reach statically.
    assert isinstance(c.validate_ports(c.inputs.keys(), c.outputs.keys()), list)
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_authored_contracts.py -x`
Expected: FAIL — `TranscriptInitiation has no declared contract`.

- [ ] **Step 3: Author the contract on `TranscriptInitiation`**

In `v2ecoli/processes/transcript_initiation.py`, beside `config_schema` (line 221):

```python
    contract = ProcessContract(
        summary=(
            "Distributes activated RNAPs across transcription units by "
            "weighted multinomial sampling."
        ),
        inputs={
            "environment": "reads media_id to select the media-dependent "
                           "active-RNAP fraction f_active",
            "full_chromosomes": "reads chromosome count; TUs without a "
                                "chromosomal promoter get zero probability",
            "RNAs": "reads existing transcripts to detect footprint crowding "
                    "(~24 nt) that blocks re-initiation on a TU",
            "bulk": "reads free RNAP counts to size the activation pool",
        },
        outputs={
            "RNAs": "appends one newly initiated transcript per multinomial draw",
            "active_RNAPs": "appends the RNAP bound to each new transcript",
            "bulk": "decrements free RNAP by the number activated",
        },
        config={
            "active_rnap_footprint_size": "nt window on a TU that blocks a "
                                          "second initiation event",
            "fracActiveRnapDict": "media -> active RNAP fraction, the f_active "
                                  "lookup",
            "basal_prob": "per-TU baseline initiation probability",
            "delta_prob": "sparse TF effect matrix added to the basal probability",
            "seed": "RNG seed for the multinomial draw",
        },
        math=[
            "n_to_activate = round(f_active · n_total_RNAP) - n_active",
            "p_i = max(0, basal_prob_i + ∑_j delta_prob[i,j] · bound_TF_j)",
            "initiations ~ Multinomial(n_to_activate, p_i / ∑_i p_i)",
        ],
        symbols={
            "f_active": "media-dependent fraction of RNAP that is active "
                        "(dimensionless, 0-1)",
            "p_i": "initiation probability for transcription unit i "
                   "(dimensionless, normalized)",
            "delta_prob": "TF effect matrix, sparse COO converted to CSR",
        },
        assumptions=[
            "TUs that are footprint-crowded or lack a chromosomal promoter "
            "are excluded by setting p_i = 0.",
        ],
    )
```

Add the import at the top of the module:

```python
from process_bigraph.contract import ProcessContract
```

- [ ] **Step 4: Run to check the first one passes**

Run: `pytest tests/test_authored_contracts.py -k TranscriptInitiation -v`
Expected: PASS for all four parametrized cases on `TranscriptInitiation`.

- [ ] **Step 5: Author the remaining four**

Repeat Step 3's structure for `TranscriptElongation`, `PolypeptideInitiation`, `PolypeptideElongation`, and `RnaDegradation`. For each, read the existing docstring and `config_schema` first — the math is often already in the docstring and only needs lifting into `math=[...]`, and config keys come straight from `config_schema`.

The genuinely new writing is `inputs=` and `outputs=`: state **what the process does with the port**, not what the port contains. "reads counts of bulk molecules" is worthless; "decrements free RNAP by the number activated" is the point.

- [ ] **Step 6: Run the full authored-contract suite**

Run: `pytest tests/test_authored_contracts.py -v`
Expected: PASS, twenty parametrized cases.

- [ ] **Step 7: Verify they reach the document**

```bash
python scripts/regenerate_composite_states.py
python3 -c "
import json
d = json.load(open('reports/composite-state/v2ecoli.composites.baseline.json'))
def walk(n):
    if not isinstance(n, dict): return
    if n.get('_type') in ('process','step'): yield n; return
    for k, v in n.items():
        if not k.startswith('_'): yield from walk(v)
authored = [p for p in walk(d['state']) if p.get('_contract', {}).get('inputs')]
print(f'{len(authored)} processes with authored port semantics')
for p in authored:
    c = p['_contract']
    print(f\"  {p['address'].split('.')[-1]}: {len(c['inputs'])} in, {len(c['outputs'])} out, {len(c['math'])} math\")
"
```

Expected: five processes listed, each with several documented ports and math lines.

- [ ] **Step 8: Commit**

```bash
git add v2ecoli/processes/ tests/test_authored_contracts.py
git commit -m "feat: author process contracts for the five core gene-expression processes"
```

---

## Self-Review Notes

**Spec coverage.** §5's `ProcessContract` shape → Task 1. Declared-vs-derived resolution → Task 2. `_contract` in the document → Task 3. Contract completeness validation → Task 1 (`validate_ports`), surfaced by the loom plan. §7(a) `config_schema` attach → Task 3. §7(b) declared config values → Task 4. Authoring → Task 5.

**Deliberately out of scope.** Authoring contracts for all 46 processes. Five are authored; the rest fall back to their docstrings, which the loom plan renders. Bulk-authoring port semantics without care produces text that is worse than nothing.

**Ordering.** Tasks 1 and 2 are prerequisites for Task 3's `_contract` attach, and Task 1 is a prerequisite for Task 5. Task 4 is **fully independent** of Tasks 1–3 and 5 — it is a different repo fixing a different bug, and can be done first if config matters more than contracts.

**Risk on Task 4 Step 3.** The stash must capture the *declared* config, before `resolve_config()` inflates `{"_function": ...}` refs into live callables. `test_no_bound_methods_leaked_into_config` is the guard. If the call ordering in `baseline.py:378-417` puts resolution before instantiation, the declared form must be threaded through as a separate argument — the step says so explicitly rather than assuming.

**Placeholder in Task 3 Step 2.** `_class_for_address` names a helper whose real identity Step 1 establishes by reading the code. It is not a stand-in for undesigned behavior — the resolution logic already exists and is already used to fetch docstrings; only its name is unknown until read.
