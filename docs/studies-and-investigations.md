# Studies & Investigations

This is the guide to the two objects you spend most of your time authoring in the
Workbench — a **study** and an **investigation** — and to the single idea that
connects them to the engine underneath.

> **The one idea.** A study and an investigation are not new kinds of runtime
> object. They are the **Workbench's on-disk, authorable form of a
> process-bigraph *template*** — a document with open *sites* (holes). Filling
> the holes turns a study or an investigation into a runnable `Composite`. The
> Workbench stores the specs and does the filling for you; the engine does the
> running.
>
> If you want the engine-level picture first, work through
> **[process-bigraph Tutorial 4 — Study & Investigation Templates](https://vivarium-collective.github.io/process-bigraph/notebooks/tutorial_4.html)**
> ([source](https://github.com/vivarium-collective/process-bigraph/blob/main/process_bigraph/templates.py)).
> This document is the Workbench half of that story.

---

## 1. A study

A **study** is one question asked of one model: *run this composite, under this
configuration, and turn the result into a verdict / figure / report card.* On
disk it is a directory:

```
studies/<slug>/
  study.yaml          # the spec: what to run, how, what to look at
  runs.db             # durable run artifacts (SQLite), written by runs
  viz/<name>.html     # generated visualizations
```

### The execution interface

The part of `study.yaml` the engine actually consumes is normalized by
[`study_spec.study_interface()`](https://github.com/vivarium-collective/vivarium-workbench/blob/main/vivarium_workbench/lib/study_spec.py)
into a small, stable shape:

```yaml
name: succinate_growth
description: Growth of the baseline cell on succinate.
composite: local.composites.ecoli_baseline      # the model to run (a registered composite)
config:                                          # overrides fed to the composite's generator
  media: succinate
inputs:                                          # results pulled from OTHER studies (see §3)
  - {artifact: sim_data, from: parca, into: cache_dir}
outputs: [dry_mass, growth_rate]                 # observables to record
emitter: sqlite
```

`study_interface` returns `{composite, config, inputs, outputs, emitter}`, with
`inputs`/`outputs` defaulting to `[]` and `config` to `{}` — legacy specs that
declare no interface at all still load. **Everything about *what runs* comes from
this interface**; the rest of `study.yaml` is authoring and narrative.

### The rest of a study spec

`study.yaml` also carries the fields that make a study read as science rather
than a job description — `baseline:`/`variants:` (the model and its
perturbations), `question`, `study_card`, `status`, and the per-study narrative
fields (`biological_role`, `primary_claim`, `primary_visualization`, …). Those
are documented in **[investigation-narrative-schema.md](investigation-narrative-schema.md)**;
they render automatically into the report and dashboard.

The spec is **versioned** (v2 → v3 → v4), and specs are migrated on load, so
treat the loader — [`investigations.load_spec()`](https://github.com/vivarium-collective/vivarium-workbench/blob/main/vivarium_workbench/lib/investigations.py)
and [`spec_migration.py`](https://github.com/vivarium-collective/vivarium-workbench/blob/main/vivarium_workbench/lib/spec_migration.py)
— as the authority on the exact current fields, not a frozen list here.

### How a study maps to a process-bigraph template

A study is a **study template**: a fixed analysis network (the report/flush
steps that produce observables) with the **model left as a site**. Its
`composite` + `config` is what fills that site; running it fills the study's
`results` site with a durable handle. That is exactly §2–§3 of pbg Tutorial 4,
one level down.

---

## 2. An investigation

An **investigation** is a set of studies that build a cumulative argument. On
disk:

```
investigations/<slug>/
  investigation.yaml   # the spec: members + narrative
```

The spec is deliberately small — it names its member studies and carries the
investigation-level narrative:

```yaml
name: metabolism_comparison
description: Compare baseline growth across carbon sources.
members: [parca, glucose_growth, succinate_growth, acetate_growth]
```

The member key is read by
[`investigation_member_slugs()`](https://github.com/vivarium-collective/vivarium-workbench/blob/main/vivarium_workbench/lib/investigation_members.py):
it accepts `studies:` (the pre-migration key) or `members:` (post-migration),
preferring `studies:` when both are present. Each member is a study slug
resolving to a `studies/<slug>/study.yaml`, or a nested investigation.

### How an investigation maps to a process-bigraph template

An investigation is an **investigation template**: the *same* idea as a study,
one level up — **one open site per member study**. Filling a member admits it to
the run; leaving it open *prunes* it. Gating is expressed as filling, not as a
scheduler decision (pbg Tutorial 4, §5).

---

## 3. How they run: convert → trigger → pull-or-compute

The Workbench does not invent an execution model for investigations — it
**lowers the workspace specs into a process-bigraph investigation document and
runs the engine's `trigger` over it.** The bridge is one module:

**[`lib/investigation_composite.py`](https://github.com/vivarium-collective/vivarium-workbench/blob/main/vivarium_workbench/lib/investigation_composite.py)**
— the generic, workspace-driven converter. It reads `investigation.yaml` +ⁿ
`study.yaml` and builds a flat `{member_name: region}` document where each region
carries `_study` metadata (`{id, config, inputs, kind}`) plus a placeholder
`sim` compute node — precisely the shape
[`process_bigraph.templates.trigger`](https://github.com/vivarium-collective/process-bigraph/blob/main/process_bigraph/templates.py)
consumes.

`trigger(document, target)` then does **pull-or-compute** on the results axis:

- **open** results site → the study **computes** (runs its `SimulationStep`),
- **filled** results site → the study is already satisfied and its consumers read
  the **cached handle** — it does *not* re-run,
- for a target study, it resolves each prerequisite's **content address** and
  fills its site from `.pbg/artifacts/<hash>/` if present (**pull**) or computes
  just that prerequisite; non-ancestors are **pruned**.

This is what "**run one study**" and "**continue from here without rerunning the
expensive upstream**" mean concretely — e.g. reuse a cached ParCa fit and only
recompute the downstream growth study.

### Addressing lock-step (why the cache hits)

The converter builds each member's `_study` metadata so that
`process_bigraph.templates.study_address` computes the **same** `artifact_id`
that the Workbench pipeline
([`lib/artifacts/pipeline.resolve_study`](https://github.com/vivarium-collective/vivarium-workbench/blob/main/vivarium_workbench/lib/artifacts/pipeline.py))
already wrote to disk. The id is a hash of `composite_id + canonical(config) +
sorted(input_ids) + workspace_git_commit`. Both sides feed that formula the same
inputs, so `trigger` finds the artifacts the pipeline produced. **Change one
side's formula and both must change together** — the converter's module docstring
carries the full lock-step note.

---

## 4. The mapping at a glance

| Workbench (authoring) | process-bigraph (engine) | Where in code |
|---|---|---|
| `studies/<slug>/study.yaml` | a **study template** (model as a site) | [`study_spec.py`](https://github.com/vivarium-collective/vivarium-workbench/blob/main/vivarium_workbench/lib/study_spec.py) · [`templates.template_document`](https://github.com/vivarium-collective/process-bigraph/blob/main/process_bigraph/templates.py) |
| `investigations/<slug>/investigation.yaml` | an **investigation template** (site per member) | [`investigations.py`](https://github.com/vivarium-collective/vivarium-workbench/blob/main/vivarium_workbench/lib/investigations.py) · [`templates.investigation_document`](https://github.com/vivarium-collective/process-bigraph/blob/main/process_bigraph/templates.py) |
| `composite` + `config` in a study | the filler dropped into the model site | `study_interface()` |
| a study's recorded result | a **filled `results` site** (cached handle) | [`artifacts.py`](https://github.com/vivarium-collective/process-bigraph/blob/main/process_bigraph/artifacts.py) |
| "run this study / continue from here" | `trigger(document, target)` (pull-or-compute) | [`investigation_composite.py`](https://github.com/vivarium-collective/vivarium-workbench/blob/main/vivarium_workbench/lib/investigation_composite.py) · `templates.trigger` |
| gating a member on a prerequisite | leaving a member's site open → **prune** | `templates.prune_open_regions` |
| `.pbg/artifacts/<hash>/` | content-addressed artifact store | `lib/artifacts/pipeline.py` |

---

## 5. Authoring and running

Studies and investigations are files, so you can write them by hand, but the
Workbench gives you a CLI and an HTTP API (and the `viva-*` skills drive that
API — see [ai-onboarding.md](ai-onboarding.md)).

```bash
# CLI (from the workspace root; `vwb` is an alias for `vivarium-workbench`)
vwb serve --workspace .                 # serve UI + API; writes .pbg/server/server-info
vwb study    <slug>                     # create / run a study
vwb investigation <slug>                # create / run an investigation
vwb run      <composite>                # run a composite directly
vwb rerun    <...>                      # re-run using cached upstream artifacts (pull-or-compute)
vwb runs | status | logs                # inspect
```

```bash
# API (base URL from .pbg/server/server-info; no auth for no-Origin requests)
BASE=$(python3 -c "import json;print(json.load(open('.pbg/server/server-info'))['url'])")
curl -s "$BASE/api/workspace-manifest"        # studies, investigations, registry, health — orient here
curl -s "$BASE/api/linkage-index"             # the study/investigation reference graph
curl -s "$BASE/openapi.json"                   # authoritative request/response shapes
```

Runs are asynchronous: a run returns a `run_id`; poll its status, and check the
run's **result field** (a failed run can still return HTTP 200). The durable
artifact is `studies/<slug>/runs.db`, and every write commits to git.

---

## 6. See also

- **[process-bigraph Tutorial 4 — Study & Investigation Templates](https://vivarium-collective.github.io/process-bigraph/notebooks/tutorial_4.html)** — the engine-level, executable version of this idea (sites, fill, gating, on-disk `*.template.yaml`).
- **[process-bigraph `doc/architecture.md`](https://github.com/vivarium-collective/process-bigraph/blob/main/doc/architecture.md)** — one object, one operation, one law; the eight invariants.
- **[investigation-narrative-schema.md](investigation-narrative-schema.md)** — the per-study and per-investigation narrative fields that render into reports.
- **[ai-onboarding.md](ai-onboarding.md)** — scaffolding a workspace and driving it (including the LLM layer).
- **[ARCHITECTURE.md](ARCHITECTURE.md)** · **[USAGE.md](USAGE.md)** — the Workbench server and deployment.
