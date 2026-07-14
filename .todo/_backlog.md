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
3. /plan let's focus on .todo/plans/7-*. Please proceed as follows to complete the ## **Task** as follows: 
  
  ## Relevant Information
  - last night, the demo branch we were working on in last sessions was merged into main with proper release as discussed/expected (my
  coworker did it)                                                                                                         
  - my coworker also did the previously discussed merge of sms-api's pr/branch into main with release as discussed/expected :)
  - keep in mind that ive already switched to a dedicated branch in this repo (./) for plan 7...IF CHANGES ARE NEEDED IN
    SMS-API, PLEASE CREATE A NEW DEDICATED CORRESPOINDING BRANCH/PR THERE (as we did before; currently ~/sms/sms-api is on latest main, so
    create new branch off of latest main if needed in sms-api)
  
  ## **Task** 
  1. Given the "Relevant Information" above, please comprehensively and carefully revise and rethink if needed the plan for 7.
  2. Once the final plan is manifested, please show it/report it to me and standby for my approval.
  3. once the plan is approved by me, update the .todo/plans/7-* and MANIFEST in .todo/ if needed to reflect the final approved plan. 
  4. standby for my approval