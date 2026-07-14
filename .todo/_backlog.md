- a.) implement pydantic-settings for any/all env variable definitions.
  Implementation lands in `vivarium_workbench/environment.py` (currently untracked
  WIP) — a pydantic-settings `BaseSettings` that manages all repo + demo env vars
  (`VIVARIUM_WORKBENCH_*`, GH client id, remote-pinned config, allowed origins,
  etc.), mirroring the pattern in `~/sms/sms-api/sms_api/config.py`. Consolidates
  the env-var reads currently scattered across `lib/`. 

- b.) enable more attractive, sleek, production grade, robust, ux-friendly visual feedback to any long-running ui-triggered process, 
starting with the first such use case of running a simulation in the RUn against pinned build card within the simulations tab of a given investigation - study in http://localhost:8080/workbench#investigations.
im thinking a very visaully helpful progress bar, but also keep in mind things like spinners etc, or def a combination of both.
  → **PROMOTED to `.todo/plans/7-pinned-run-progress-ux.md`** (2026-07-14). Item
  kept here for context; the tracked plan is canonical.



## Prompt Queue

_(drained 2026-07-14 — both requests promoted to tracked plans per the todo protocol)_

1. ✅ item b.) → `.todo/plans/7-pinned-run-progress-ux.md`
2. ✅ auto-parameterize embedded Pathway Tools from a study's Exports `.tsv` on the
   remote smsvpctest deployment → `.todo/plans/8-autoparam-ptools-from-exports-tsv.md`