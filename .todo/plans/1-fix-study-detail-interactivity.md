## Context

 The k8s /workbench subpath deployment was just fixed (ALB routing) and E2E-verified
 via curl — but the user's screenshot shows the Investigations → Study detail view
 (study "statistical") still broken: two overlapping, unstyled, non-clickable tab bars
 (Overview | Hypotheses | Model | Simulations | Results | Exports and
 Overview | Tests | Decide | Model | Simulations | Results | Exports) and unstyled
 form fields.

 Two Explore agents independently traced this to the same root cause, which I verified
 by reading the actual source:

 - The study detail view is a separate server-rendered HTML document
 (GET /studies/{slug}, vivarium_workbench/api/app.py:2668-2691 →
 lib/study_page.build_study_detail_page → render_study_detail_html, which
 Jinja2-renders templates/study-detail.html), loaded into an <iframe> on the
 Investigations page.
 - The main shell page (index_shell, app.py:2578-2610) already does base-path
 rewriting correctly: it passes base_path=request.scope.get("root_path") into
 render_workspace_report, which calls _apply_live_base_path() (lib/report.py:415-455)
 — this is why the outer page, its CSS, and its API calls all work fine under /workbench.
 - study_detail_page() does none of this: no Request param, no base_path, never
 calls _apply_live_base_path. The template hardcodes root-absolute asset refs —
 href="/style.css", src="/data-source.js", src="/configure-run.js",
 src="/study-detail.js" (templates/study-detail.html:6,1974,2087-2088) — which
 resolve to the literal domain root under a subpath, not /workbench/.... Under
 /workbench these all 404 (the ALB only routes /workbench/* to this service): CSS
 never applies (raw/unstyled buttons) and study-detail.js never runs, so its
 _setStudyPillar/_showPillarSubnav logic (static/study-detail.js:20-53,80-86)
 never collapses the two-level pillar/sub-nav into the single active view — matching
 the screenshot exactly (both nav rows fully expanded, unstyled, dead).

 This is confirmed not a problem for the published/snapshot static bundle:
 publish.py's _do_build already runs the study HTML through
 _normalize_asset_urls() (publish.py:445-475, converts /style.css →
 /assets/style.css) before its own _apply_base_path() — the live server's
 /studies/{slug} route is simply missing the equivalent step. render_study_detail_html
 is called with no base_path in both places today (publish.py:970,
 server.py's 2-arg shim), so this fix is purely additive via a new keyword-only
 base_path parameter defaulting to "" — zero behavior change for existing callers.

 A secondary, same-family gap: static/walkthrough.js:8595 builds a raw
 href="/studies/" + encodeURIComponent(...)" for a "seeded study" finding link,
 bypassing the existing _studyHref() helper (walkthrough.js:221-231) that already
 reads __DASH_CONFIG__.basePath correctly (used by _openStudyEmbedded). Same bug
 class, one-line fix, same file already being touched conceptually.

 ## Approach

 1. Relocate _normalize_asset_urls into lib/report.py, alongside
 _apply_live_base_path, so both the publish (static bundle) and live-server code
 paths share one canonical implementation instead of duplicating it. Update
 publish.py to import it from lib.report and delete its local copy (its two
 call sites, publish.py:944 and :971, are unchanged — same name/signature).
 2. lib/study_page.py:
   - render_study_detail_html(ws_root, name, spec, *, base_path: str = ""): pass
 base_path into the Jinja render context (for the one /api/study-analysis-zip
 anchor link, templates/study-detail.html:1455, which needs explicit prefixing
 since _apply_live_base_path's regex intentionally doesn't rewrite /api/ hrefs
 — it relies on a runtime fetch/XHR shim that doesn't cover plain anchor clicks).
 After tpl.render(...), run the output through
 _normalize_asset_urls() then _apply_live_base_path(html, base_path) (mirrors
 exactly what publish.py:970-972 already does, minus the snapshot-mode config
 swap).
   - build_study_detail_page(ws_root, slug, *, base_path: str = ""): thread
 base_path through to render_study_detail_html.
 3. api/app.py — study_detail_page(): add a request: Request parameter,
 compute base_path = request.scope.get("root_path") or "" (identical pattern to
 index_shell, app.py:2593-2595), pass it to build_study_detail_page(ws, slug, base_path=base_path).
 4. templates/study-detail.html:1455: prefix the study-analysis-zip anchor with
 {{ base_path }} (empty string when unset → byte-identical to today at root).
 5. static/walkthrough.js:8595: replace the manual '/studies/' + encodeURIComponent(f.seeded_study) string-build with _studyHref(f.seeded_study).
 6. Tests — add to tests/test_study_page_lib.py (mirrors its existing
 render_study_detail_html/build_study_detail_page coverage):
   - render_study_detail_html(ws, name, spec, base_path="/workbench") →
 asserts /workbench/assets/style.css, /workbench/assets/data-source.js,
 /workbench/assets/configure-run.js, /workbench/assets/study-detail.js,
 /workbench/api/study-analysis-zip all present, and
 basePath: "/workbench" appears in the injected __DASH_CONFIG__.
   - Default (base_path="") call still produces valid, resolvable asset refs
 (/assets/style.css etc.) — confirmed no existing test asserts the old literal
 /style.css string (checked via grep), so this is a safe, non-breaking change.
   - build_study_detail_page(ws, slug, base_path="/workbench") threads through
 correctly (200 path); 404 paths unaffected.

 ## Verification

 - uv run pytest tests/test_study_page_lib.py tests/test_study_detail_page.py tests/test_study_detail_template.py tests/test_publish.py -x — full local suite for every file touched,
 confirms no regression in the publish/snapshot path either.
 - Manual local check: uv run vivarium-workbench serve --workspace <ws> --base-path /workbench --port 8771, then curl -s http://localhost:8771/workbench/studies/<slug> and confirm asset
 refs are /workbench/assets/... and __DASH_CONFIG__ carries basePath.
 - Once merged, the live k8s deployment still runs the pinned 0.1.1 image — this code fix requires a new image build/push and a k8s deployment update to actually reach
 http://localhost:8080/workbench (out of scope for this change; flagged as a explicit follow-up, not silently assumed). Until that redeploy happens, the user's already-open browser tab
 against the live cluster will keep showing the bug even after this PR merges.
 - After redeploy: re-run the exact browser check from the previous session — Investigations → click "statistical" study → confirm a single, styled, collapsed pillar nav with only the
 active pillar's sub-tabs visible and clickable.

## Progress

- [x] 1. Relocate `_normalize_asset_urls` into `lib/report.py`; update `publish.py` import
- [x] 2. `lib/study_page.py` — thread `base_path` through `render_study_detail_html` / `build_study_detail_page`
- [x] 3. `api/app.py` — `study_detail_page()` reads `request.scope["root_path"]`
- [x] 4. `templates/study-detail.html:1455` — prefix study-analysis-zip anchor with `{{ base_path }}`
- [x] 5. `static/walkthrough.js:8595` — use `_studyHref()` for the seeded-study link
- [x] 6. Tests in `tests/test_study_page_lib.py` — 5 new tests + fixed the pre-existing monkeypatch stub (`fake_render` needed a `base_path` kwarg)
- [x] Verification: full pytest run across touched test files — 12 pre-existing failures confirmed identical on unmodified code (stash-diffed); zero new failures; all new tests pass
- [x] Manual local check — `uvicorn ... --root-path /workbench`: all asset/API refs correctly prefixed (`/workbench/assets/style.css` etc.), `__DASH_CONFIG__.basePath` set; root-hosting (no base_path) still resolves correctly (`/assets/style.css` etc.)
- [x] PR opened: https://github.com/vivarium-collective/vivarium-workbench/pull/465 (base: main, head: fix/study-detail-base-path, commit 861aefa)
- [x] Merged into `demo-v2ecoli` locally (clean fast-forward, no divergence) and pushed to origin — unblocks the live demo without waiting on PR review
- [ ] Follow-up (separate, explicit, not part of this fix): rebuild + push a new vivarium-workbench image and update the k8s deployment so the live cluster actually serves this fix

### Note: pre-existing test failures found during verification (NOT caused by this fix)

12 failures in `test_study_detail_page.py`/`test_study_detail_template.py`/`test_publish.py`
(tab-scaffold/panel-id/skeptic-toggle assertions, one snapshot-popout assertion) fail
identically with this branch's changes stashed out — confirmed pre-existing template/test
drift, unrelated to base-path work. Left untouched (out of scope for this fix); flagging as
a separate cleanup item if wanted.