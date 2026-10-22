# compose-api fixtures: real artifacts, not hand-written

Each file was produced by the real system, recorded on 2026-10-22, so the tests that read them check the
workbench against what compose-api and its runners actually produce.

| File | What it is | How it was made |
|---|---|---|
| `particle_diffusion.pbg` | viva-pde-particle's `particle_diffusion` composite (`n_particles=200`) as the compose mode exports it: `local:<module>.<Class>` addresses and a `user_emitter` recording `particle_totals` | `export_composite_pbg(..., address_form="registry", document_hook=inject_emitter_for_declared_paths)` run inside the environment's own image, `ghcr.io/vivarium-collective/viva-pde-particle-compose@sha256:7ada6256…`, on viva-pde-particle `65f9a62` |
| `results_particle_diffusion.zip` | that document's results, as compose-api's job zips them | `python -m viva_pde_particle.compose_runner run particle_diffusion.pbg -o output -n 0.05` in the same image, then `cd output && zip -r ../results.zip ./*` (the job script's command) |
| `results_4706_no_emitter.zip` | the `.pber` of production simulation 4706, a pilot run whose document had no emitter: `{}` | its one `.pber` member copied byte for byte from `GET /results/simulation/results/file?simulation_id=4706` |
| `viva-pde-particle.manifest.json` | the environment's interface manifest | `GET https://compose.cam.uchc.edu/registry/environments/viva-pde-particle/manifest` (0.7.33) |
| `environments.json`, `entries.json` | the registry's environments and entries | `GET /registry/environments`, `GET /registry/entries` on the same deployment |

`../../_contracts/compose_api_openapi_0.7.35.json` is the `openapi.json` shipped in the locked `compose-api-client`
(biosimulations/compose-api tag `0.7.35`, `367ccb2`, which compose.cam.uchc.edu runs); `test_backend_dispatch_compose.py`
fails when the two differ.
