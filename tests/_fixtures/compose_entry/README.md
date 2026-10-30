# compose-entry fixtures: real files, not hand-written

Each directory holds a real viva-* workspace's `pyproject.toml` and `workspace.yaml` (and `pixi.toml` where it has one),
copied byte for byte from the default branch of the repository on 2026-10-30. `tests/test_compose_entry.py` builds a git
checkout around them, so the generator is tested on what these repositories actually declare.

| Directory | Repository @ commit | What it exercises |
|---|---|---|
| `ketchup` | vivarium-collective/viva-ketchup @ `ace769dd` | ships `viva_ketchup` (real) and `pbg_ketchup` (a compatibility shim); no dev tooling |
| `artistoo` | viva-artistoo @ `fee6e8c7` | the package is `pbg_artistoo` while the registry says `viva_artistoo`; a `node_bridge` that needs Node.js; `vivarium-workbench` as a runtime dependency |
| `cpm` | viva-cpm @ `932a89cd` | a maturin (Rust) build whose module is `viva_cpm.cpm_core`; also ships `viva_cpm_studies` |
| `pennylane-adversarial` | viva-pennylane-adversarial @ `bfe4b118` | ships a package named `app`, which must never be a module root |
| `pde-particle` | viva-pde-particle @ `70438826` | a `pixi.toml`; its registry entry is correct |
| `mgen` | viva-mgen @ `10bca75d` | ships `viva_mgen`; the catalog entry is `viva-Mgen` with root `viva_Mgen` |

`registry_entries_0.7.76.json` is `GET https://compose.cam.uchc.edu/registry/entries` as production served it on
2026-10-30 (0.7.76), saved byte for byte: the catalog entries these workspaces had, and the one correct `tested` entry.

The package directories and the `node_bridge` files the tests create are empty stand-ins for what the real repositories
contain (the names come from the repositories' own file listings); the checkout around them is made fresh in a temporary
directory, with a real bare repository as its origin, so "pushed", "dirty" and "default branch" are git's own answers.
