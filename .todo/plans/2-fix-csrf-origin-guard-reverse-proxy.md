## 2. **(.todo/plans/2-fix-csrf-origin-guard-reverse-proxy.md)**:

### Name

Fix: CSRF/origin guard 403s all POST/DELETE behind ALB reverse-proxy subpath deployment

### Status

IMPLEMENTED + COMMITTED + DEPLOYED — but ❌ **STILL BROKEN in the browser**.
The fix (opt-in `trust_forwarded`/`forwarded_host` on `is_request_allowed()` +
`--trust-proxy`/`VIVARIUM_WORKBENCH_TRUST_PROXY=1`) landed in `481b3f2` on
`demo-v2ecoli`, targeted suites pass, and the flag is present in the running
pod's args (verified via `kubectl get deployment ... -o jsonpath`). Despite that,
the 2026-07-13 browser walkthrough still hit `{"error":"cross-origin request
forbidden"}`, and `POST /workbench/api/study-run-baseline → 403` is reproduced
server-side in the pod logs. **The code fix is correct in isolation but is not
taking effect through the ALB→SSM-tunnel→k8s hop chain; that root cause is NOT
yet found.** See "Post-deploy diagnosis" below. Independent of items #1/#3.

### Bug report

`demos/v2ecoli/bugs/broken-runs.png` — Investigations tab, "statistical" study:
clicking **Run** returns `{"error":"cross-origin request forbidden"}` (403)
instead of starting the composite run, when accessed through the ALB reverse
proxy at the `/workbench` subpath (`sms-proxy.sh -s smsvpctest` → `sms-api-stanford-test`
k8s namespace).

### Root cause (verified against live source)

`vivarium_workbench/lib/csrf.py:20-37`:
```python
def is_request_allowed(origin, host, *, disabled):
    if disabled: return True
    if not origin: return True
    netloc = urlsplit(origin).netloc
    return bool(netloc) and netloc == (host or "")
```
Called from `vivarium_workbench/api/app.py:467-487` (`_csrf_mw`, a
`@app.middleware("http")` gating every POST/DELETE), reading
`request.headers.get("origin")` / `.get("host")` straight off the raw request —
no code anywhere reads `X-Forwarded-Host`/`X-Forwarded-Proto`/`Forwarded`, and
uvicorn is launched (`lib/startup.py:120`) with no `--proxy-headers`/
`--forwarded-allow-ips`. Across the ALB → SSM tunnel → k8s Service → pod hop
chain, the raw `Host` header the app sees can diverge from what the browser's
`Origin` reflects, so every mutating request 403s while GETs (page load, asset
fetch) are unaffected — matches the observed symptom exactly.

This is a completely separate code path from the recent PR #465 base-path/
`root_path` work (`_BasePathStripMiddleware` in `lib/startup.py:26-49`) — that
middleware only rewrites `scope["path"]`/`scope["root_path"]` for route
matching, never touches headers, so it doesn't help here. `git log` on
`lib/csrf.py` confirms the base-path series never touched it.

Security intent to preserve (commit `2ca15ea6`, `tests/test_csrf_origin_guard.py`):
a stateless same-origin check defending the FastAPI POST/DELETE surface (much of
which can run git/shell/pip) against a malicious webpage firing cross-site
requests at the dashboard. No Origin (curl/CLI) → allow. Present Origin must
match the effective host → else 403. `VIVARIUM_WORKBENCH_DISABLE_CSRF=1` escape
hatch must keep working.

### Fix design

1. **`lib/csrf.py`** — extend `is_request_allowed()` additively:
   ```python
   def is_request_allowed(
       origin: str | None, host: str | None, *, disabled: bool,
       forwarded_host: str | None = None, trust_forwarded: bool = False,
   ) -> bool:
       if disabled: return True
       if not origin: return True
       effective_host = (forwarded_host if (trust_forwarded and forwarded_host) else host) or ""
       netloc = urlsplit(origin).netloc
       return bool(netloc) and netloc == effective_host
   ```
   New kwargs default to today's exact behavior — every existing call site
   (stdlib handler, all current tests) is unaffected. `trust_forwarded` gates
   whether `X-Forwarded-Host` is even consulted; it must NEVER be inferred from
   the header's mere presence (that would let an attacker on a direct connection
   spoof it and bypass the guard) — it requires the explicit opt-in below.
   Add `is_trust_proxy_via_env(env)` next to `is_disabled_via_env`, same shape
   (dual-reads `VIVARIUM_DASHBOARD_TRUST_PROXY` via `lib/env_compat.get_env`).

2. **`lib/env_compat.py`** — add `TRUST_PROXY_ENV = NEW_PREFIX + "TRUST_PROXY"`
   to the suffix-constant list (next to `DISABLE_CSRF_ENV`).

3. **`api/app.py:467-487`** (`_csrf_mw`) — pass the new args through:
   ```python
   if not _csrf.is_request_allowed(
       request.headers.get("origin"),
       request.headers.get("host"),
       disabled=_csrf.is_disabled_via_env(os.environ),
       forwarded_host=request.headers.get("x-forwarded-host"),
       trust_forwarded=_csrf.is_trust_proxy_via_env(os.environ),
   ):
   ```

4. **`cli.py`** — add `--trust-proxy` to `p_serve`'s argparser (mirrors
   `--base-path` at `cli.py:435-437`); in `cmd_serve`, when set,
   `os.environ["VIVARIUM_WORKBENCH_TRUST_PROXY"] = "1"` before calling
   `serve_fastapi(...)`. No signature change needed to `serve_fastapi`/
   `create_app` — verified `create_app()` takes no config params today, and the
   existing `DISABLE_CSRF` escape hatch is already read straight from
   `os.environ` at request time; this follows the identical, smallest-diff
   pattern.

5. **Explicitly out of scope**: IP-allowlisting the proxy peer
   (uvicorn's `--forwarded-allow-ips` model). The ALB → SSM-tunnel → k8s hop
   chain makes "the peer IP" a fuzzy concept anyway; the opt-in flag (explicit
   operator action required) is the correctly-sized mitigation matching this
   repo's existing `--disable-csrf` trust model. Flag as possible future
   hardening, not required now.

### Tests

**Must keep passing unchanged** (zero behavior change on the default/no-flag path):
- `tests/test_csrf_lib.py` (all)
- `tests/test_csrf_origin_guard.py` (all: `test_post_cross_origin_is_rejected_403`,
  `test_post_same_origin_is_allowed`, `test_post_no_origin_is_allowed`,
  `test_csrf_predicate_verdicts`, `test_csrf_predicate_env_disable_bypasses`)
- `tests/test_api_app.py::TestCsrfMiddleware` (`test_cross_origin_post_403`,
  `test_same_origin_post_passes`, `test_no_origin_post_passes`)

**New tests to add:**
- `tests/test_csrf_lib.py` — parametrized cases on `is_request_allowed`:
  trusted+forwarded-host-present → allow when it matches Origin even if raw
  Host doesn't; untrusted (default) + same inputs → deny (proves the header is
  ignored without opt-in — the core security property); trusted +
  forwarded-host empty/absent → falls back to `host` (no regression).
- `tests/test_csrf_lib.py::test_is_trust_proxy_via_env` — mirrors
  `test_is_disabled_via_env` (new-prefix set → True; unset → False; deprecated
  old-prefix dual-read → True + `DeprecationWarning`).
- `tests/test_api_app.py::TestCsrfMiddleware` — a test that sets
  `VIVARIUM_WORKBENCH_TRUST_PROXY=1` via `monkeypatch.setenv` and asserts a POST
  with `Origin` matching `X-Forwarded-Host` (but not `TestClient`'s default
  `Host: testserver`) now passes; a companion test with the env unset asserts
  the same request still 403s (regression guard).
- `tests/test_csrf_origin_guard.py` — an end-to-end `dashboard_client` case with
  `VIVARIUM_WORKBENCH_TRUST_PROXY=1` in the child env, proving the same
  pass/fail split via real HTTP.

### Verification

- Local: `uv run pytest tests/test_csrf_lib.py tests/test_csrf_origin_guard.py tests/test_api_app.py -k csrf -x`.
- Local manual repro (no AWS needed): serve locally, send a POST with mismatched
  `Host` vs `Origin` vs `X-Forwarded-Host` — 403 without `--trust-proxy`,
  passes with it.
- **Deployed-environment-only**: confirm via `curl -v` through the live
  `sms-proxy.sh -s smsvpctest` tunnel which forwarded headers the ALB chain
  actually sends (some ALB configs only forward `X-Forwarded-For`/`-Proto`, not
  `-Host`) *before* flipping `--trust-proxy` on in the k8s deployment args/env —
  if `X-Forwarded-Host` isn't present, the fix needs to also read a `Forwarded:
  host=` header or the deployment needs a k8s/ingress annotation to add one.

### Post-deploy diagnosis (the actual remaining gap)

The designed fix shipped and deployed but did NOT resolve the browser symptom.
Three live hypotheses for why `--trust-proxy` isn't taking effect — this is
exactly the "Deployed-environment-only" verification caveat above coming true
(the ALB chain may not forward `X-Forwarded-Host` at all):

1. **ALB/tunnel never sets `X-Forwarded-Host`** (only `-For`/`-Proto`, or
   nothing) — `is_request_allowed()`'s `forwarded_host` is then empty and
   silently falls back to the mismatching raw `Host`. Most likely.
2. **`is_trust_proxy_via_env()` reads the wrong env var** — possible
   legacy/aliasing mismatch (old `VIVARIUM_DASHBOARD_*` prefix vs new
   `VIVARIUM_WORKBENCH_*`, or wrong suffix constant in `lib/env_compat.py`).
3. **Uvicorn's `ProxyHeadersMiddleware` strips `X-Forwarded-Host`** before the
   CSRF middleware sees it (it appears in the bug-3 traceback stack), if not
   configured with the right trusted-proxy CIDR.

**Next diagnostic step**: read `lib/csrf.py` (`is_request_allowed`,
`is_trust_proxy_via_env`) + `api/app.py`'s `_csrf_mw` side by side, and inspect
the header values actually arriving — either `curl -v` through the live
`sms-proxy.sh -s smsvpctest` tunnel, or a temporary debug-log redeploy. Likely
resolves to either a manifest-level uvicorn `--proxy-headers`/trusted-host config
change, a k8s/ingress annotation to inject `X-Forwarded-Host`, or a code fix to
the env-var check.

### Progress notes

- **2026-07-13 (plan)**: Scoped and written. Root-caused via `Agent(Explore)`
  tracing `_csrf_ok`/`_csrf_mw`/reverse-proxy header handling; fix designed via
  `Agent(Plan)`; cross-checked against `lib/csrf.py`, `lib/env_compat.py`,
  `api/app.py`, `cli.py`.
- **2026-07-13 (implemented)**: Fix landed in `481b3f2`. Targeted suites
  (`test_csrf_lib.py`, `test_csrf_origin_guard.py`,
  `test_api_app.py::TestCsrfMiddleware`) pass.
- **2026-07-13 (deployed + FAILED verification)**: Image `481b3f2` deployed with
  `--trust-proxy` in the pod args; browser walkthrough **still hit the 403**,
  reproduced server-side in pod logs. Root cause of why the fix isn't taking
  effect not yet found — see "Post-deploy diagnosis" above. **This is where the
  work stopped.**

---
