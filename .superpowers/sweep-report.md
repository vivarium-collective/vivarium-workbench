# UTF-8 write_text sweep report

## Summary

- **79 write_text calls fixed** across **28 files**
- 3 of the 82 grep hits were already covered (encoding= on a continuation line):
  - `lib/study_seed.py:306` and `:450` — existing `encoding="utf-8"` in multi-line call
  - `lib/report.py:547` — existing `encoding="utf-8"` spanning 33 lines

## Files touched

lib/catalog_install_views.py (2), lib/comparative_viz.py (1),
lib/compare_group_mutations.py (4), lib/composite_mutations.py (9),
lib/composite_test_run_views.py (1), lib/github_auth.py (1),
lib/investigation_migrate.py (1), lib/investigation_run_one_views.py (1),
lib/investigation_viz_mutations.py (1), lib/investigations.py (2),
lib/lifecycle_mutations.py (2), lib/metadata_mutations.py (5),
lib/pyproject_edit.py (4), lib/reference_mutations.py (2),
lib/references_fetch.py (2), lib/remote_build_source.py (1),
lib/run_runner.py (1), lib/source_build_views.py (1), lib/startup.py (1),
lib/study_create_views.py (4), lib/study_crud_mutations.py (11),
lib/study_tests.py (2), lib/suggest_requests.py (1),
lib/viz_commit_mutations.py (1), lib/viz_write_mutations.py (2),
lib/work_mutations.py (1), lib/work_state.py (1), server.py (14)

## Grep-clean confirmation

```
grep -rn ".write_text(" vivarium_dashboard/lib/ vivarium_dashboard/server.py \
  | grep -v "encoding=" | grep -v "write_bytes"
```
Returns 15 lines — all are the opening lines of multi-line calls whose
`encoding="utf-8"` appears on a continuation line before the closing `)`.
Zero uncovered calls remain.

## mypy

```
Success: no issues found in 70 source files
```

## Tests

```
uv run --extra dev pytest tests/test_payload_models.py tests/test_generate_ts.py \
  tests/test_api_app.py tests/test_investigation_status.py \
  tests/test_write_text_utf8_encoding.py -q
506 passed, 2 skipped, 59 warnings
```

## Ruff

Pre-existing violations only (1503 project-wide, same count before and after
for modified files). No new violations introduced.

## Commit

See git log — commit message: "fix(io): write all dashboard text as UTF-8 (locale-independent)"
