"""Thin HTTP client for the sms-api endpoints the remote-run pipeline calls.

Stdlib-only (urllib) to avoid adding a dependency, matching server.py's existing
outbound-HTTP approach. Pure HTTP — no DB, no orchestration. Parameterized by
base_url (the SSM tunnel, default http://localhost:8080).
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SmsApiError(Exception):
    """Raised when an sms-api call fails (non-200 or connection error).

    ``status`` carries the HTTP status code when the failure was an HTTP error
    (e.g. 404 from a deployment that predates an endpoint), else ``None`` for
    connection-level failures — so callers can distinguish "old server" from
    "unreachable" without parsing the message.
    """

    def __init__(self, message: str, status: "int | None" = None) -> None:
        super().__init__(message)
        self.status = status


#: Multi-GB native-store / results.zip / workspace-tarball downloads must not run
#: on the same 30s default used for small JSON status calls (CD2 pipeline audit
#: §3.12) — a slow SSM tunnel pulling a multi-GB store easily exceeds that.
#: Callers can still pass an explicit ``timeout=`` per call.
DOWNLOAD_TIMEOUT = 1800.0  # 30 minutes

#: Bounded retry policy for idempotent GET/status calls ONLY. Never applied to
#: POST (a retried `run_simulation`/`compose_submit`/`register_simulator` could
#: double-submit a run) or DELETE. Exponential backoff between attempts:
#: ``backoff * 2**attempt``.
_GET_RETRIES = 3
_RETRY_BACKOFF = 0.5


def _http_error_detail(e: HTTPError, limit: int = 4000) -> str:
    """Best-effort extraction of the server's error body for diagnostics.

    Without this, a FastAPI 422/500 with a JSON ``{"detail": ...}`` body reaches
    the operator as only ``"POST <url> -> 422"`` (CD2 pipeline audit §3.12) —
    ``HTTPError.read()`` is never called. Returns a string like
    ``": missing field 'foo'"`` ready to append to the summary message, or
    ``""`` if the body couldn't be read/decoded (never raises — surfacing a
    better error must not itself produce a worse one). Truncated so a stray
    HTML error page (e.g. from a proxy in front of sms-api) can't blow up log
    lines.
    """
    try:
        raw = e.read()
    except Exception:  # noqa: BLE001 — reading the error body must never mask the real error
        return ""
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    else:
        if isinstance(parsed, dict) and "detail" in parsed:
            detail = parsed["detail"]
            text = detail if isinstance(detail, str) else json.dumps(detail)
    if len(text) > limit:
        text = text[:limit] + "... [truncated]"
    return f": {text}" if text else ""


#: Header viva-api reads caller identity from where a deployment names one
#: (its IDENTITY_HEADER setting). Default matches oauth2-proxy's, which is what
#: sms-api-stanford-test is configured for.
IDENTITY_HEADER = "X-Auth-Request-Email"

#: GitHub session sources that identify a PERSON. `token` is deliberately absent:
#: it is `VIVARIUM_WORKBENCH_GH_TOKEN`, a shared machine credential supplied as a
#: k8s Secret, and on a deployed workbench EVERY user resolves to it. Forwarding
#: that would give every user the same identity -- so they could all cancel each
#: other's tasks, while the record claimed a specific owner. That is worse than
#: anonymous: it looks like attribution and provides none.
_PERSONAL_SOURCES = ("device_flow", "gh_cli")


def caller_identity() -> str | None:
    """The signed-in GitHub login, when a PERSON is signed in. Else ``None``.

    NOT authentication, and viva-api's own docs are explicit that its header is
    not either: this is the best attribution the workbench can currently offer,
    which is a real `@login` GitHub already verified, forwarded so a task has an
    owner instead of being unowned and cancellable by anyone.

    The proper answer is the `Principal` that `session_registry.SessionEntry`
    already reserves space for -- a workbench identity that does not depend on a
    user happening to have signed into GitHub for an unrelated reason.

    Never raises. Identity is a nicety on every path that calls it, and an
    unreachable keyring or a slow `gh` must not fail the request it decorates.
    """
    try:
        from vivarium_workbench.lib import github_auth

        session = github_auth.current_session()
    except Exception:  # noqa: BLE001 - see docstring
        return None
    if session is None or session.source not in _PERSONAL_SOURCES:
        return None
    login = (session.login or "").strip()
    # Qualify it: a bare `octocat` next to `you@example.com` in the same column
    # reads as an email that lost its domain. This says where it came from.
    return f"{login}@github" if login else None


class SmsApiClient:
    def __init__(self, base_url: str = "http://localhost:8080", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self, accept: str = "application/json") -> dict[str, str]:
        """Request headers, carrying the caller's identity when there is one.

        Sent on every request rather than only on task submits: viva-api ignores
        an unrecognised header, and a client that identified itself for some
        calls and not others would be harder to reason about than one that
        always does.
        """
        headers = {"Accept": accept}
        identity = caller_identity()
        if identity:
            headers[IDENTITY_HEADER] = identity
        return headers

    def _get(
        self,
        path: str,
        params: dict | None = None,
        *,
        retries: int = _GET_RETRIES,
        backoff: float = _RETRY_BACKOFF,
    ) -> dict:
        """GET a JSON endpoint, retrying transient failures.

        GET is idempotent (unlike ``_post``), so a bounded exponential-backoff
        retry is safe here: connection errors/timeouts and 5xx responses are
        retried up to ``retries`` attempts total; a 4xx is a client error, not a
        transient one, and is raised immediately without retrying.
        """
        url = self.base_url + path
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        req = Request(url, method="GET", headers=self._headers())
        attempt = 0
        while True:
            attempt += 1
            try:
                with urlopen(req, timeout=self.timeout) as r:  # noqa: S310 — fixed scheme, internal tunnel
                    return json.loads(r.read().decode())
            except HTTPError as e:
                if e.code >= 500 and attempt < retries:
                    time.sleep(backoff * (2 ** (attempt - 1)))
                    continue
                raise SmsApiError(f"GET {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
            except (URLError, OSError) as e:
                if attempt < retries:
                    time.sleep(backoff * (2 ** (attempt - 1)))
                    continue
                raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e

    def latest_simulator(self, repo_url: str, branch: str) -> dict:
        return self._get("/core/v1/simulator/latest", {"git_branch": branch, "git_repo_url": repo_url})

    def register_simulator(self, repo_url: str, branch: str, commit: str) -> dict:
        """POST /core/v1/simulator/upload — register a repo@commit build (async image build)."""
        return self._post("/core/v1/simulator/upload", json_body={
            "git_repo_url": repo_url, "git_branch": branch, "git_commit_hash": commit,
        })

    # -- env workers (REFACTOR-PLAN §2A.8, #942) ----------------------------
    # The workbench cannot create Jobs (§2B.2 gives it no cluster access), so it
    # asks viva-api to run a simulator image as a worker. We tell it where to
    # dial back and with what token — we already know our own address, so
    # viva-api needs to discover nothing.

    def start_env_worker(self, *, commit: str, callback_host: str, callback_port: int,
                         token: str, workspace: str | None = None,
                         session_key: str | None = None) -> dict:
        """POST /env-worker/v1/workers — run the prebuilt image for ``commit``."""
        body: dict = {
            "commit": commit,
            "callback_host": callback_host,
            "callback_port": callback_port,
            "token": token,
        }
        if workspace:
            body["workspace"] = workspace
        if session_key:
            body["session_key"] = session_key
        return self._post("/env-worker/v1/workers", json_body=body)

    def env_worker_status(self, job_name: str, *, include_logs: bool = False) -> dict:
        return self._get(f"/env-worker/v1/workers/{job_name}",
                         {"include_logs": "true"} if include_logs else None)

    def stop_env_worker(self, job_name: str) -> dict:
        """DELETE /env-worker/v1/workers/{job_name} — idempotent."""
        return self._delete(f"/env-worker/v1/workers/{job_name}")

    # -- relay (plan §C) ----------------------------------------------------
    #
    # The three above run the IN-CLUSTER shape: we tell viva-api where to dial
    # back, because we can be dialled. A laptop cannot — its SSM tunnel is
    # laptop-initiated with no inbound path — so these hand the socket to
    # viva-api instead and reach the worker over HTTP.

    def start_relayed_env_worker(self, *, commit: str, workspace: str | None = None,
                                 session_key: str | None = None,
                                 accept_timeout: float | None = None) -> dict:
        """POST /env-worker/v1/relay/workers — viva-api holds the connection.

        Note what is ABSENT versus ``start_env_worker``: no callback host, port
        or token. viva-api binds its own listener and mints its own token, which
        is the whole point — we have no address a worker could dial.
        """
        body: dict = {"commit": commit}
        if workspace:
            body["workspace"] = workspace
        if session_key:
            body["session_key"] = session_key
        if accept_timeout is not None:
            body["accept_timeout"] = accept_timeout
        return self._post("/env-worker/v1/relay/workers", json_body=body)

    def call_relayed_env_worker(self, job_name: str, *, method: str,
                                params: dict | None = None,
                                timeout: float | None = None) -> dict:
        """POST /env-worker/v1/relay/workers/{job}/call — one JSON-RPC call."""
        body: dict = {"method": method, "params": params or {}}
        if timeout is not None:
            body["timeout"] = timeout
        return self._post(f"/env-worker/v1/relay/workers/{job_name}/call", json_body=body)

    def stop_relayed_env_worker(self, job_name: str) -> dict:
        """DELETE /env-worker/v1/relay/workers/{job_name} — idempotent."""
        return self._delete(f"/env-worker/v1/relay/workers/{job_name}")

    # -- the task tier (plan §E option (e)) ---------------------------------
    #
    # For calls that cannot be a synchronous HTTP request. `run_study` runs a
    # study's baseline and every variant to completion; holding a socket open
    # for that is what produced the double-run bug, because the socket timeout
    # fired and the pool re-ran the whole study.

    def submit_env_worker_task(self, job_name: str, *, method: str,
                               params: dict | None = None) -> dict:
        """POST /env-worker/v1/tasks — 202 with a task_id; the row exists first."""
        return self._post("/env-worker/v1/tasks", json_body={
            "job_name": job_name, "method": method, "params": params or {},
        })

    def get_env_worker_task(self, task_id: int) -> dict:
        return self._get(f"/env-worker/v1/tasks/{task_id}")

    def cancel_env_worker_task(self, task_id: int) -> dict:
        return self._delete(f"/env-worker/v1/tasks/{task_id}")

    def simulator_status(self, simulator_id: int) -> dict:
        return self._get("/core/v1/simulator/status", {"simulator_id": simulator_id})

    def list_simulators(self) -> dict:
        """GET /core/v1/simulator/versions — all registered simulator builds."""
        return self._get("/core/v1/simulator/versions")

    def capabilities(self) -> dict:
        """GET /core/v1/capabilities — ``{version, capabilities: [str, ...]}``.

        The deployment's capability advertisement (viva-api #262, dual-engine
        W4/Q5). Clients branch on MEMBERSHIP in ``capabilities``, never on
        ``version`` (which is for humans/logs). A deployment predating the
        endpoint 404s — callers use ``lib.server_capabilities.fetch_capabilities``,
        which maps that to "advertises nothing" per the endpoint's own contract.
        """
        return self._get("/core/v1/capabilities")

    def ping(self, timeout: float | None = None) -> str:
        """GET /version — lightweight reachability probe for the health indicator.

        Returns the sms-api version string; raises :class:`SmsApiError` if the
        endpoint is unreachable. Uses a short timeout by default (min of 5 s and
        the client timeout) so a health check never hangs the UI.
        """
        url = self.base_url + "/version"
        req = Request(url, method="GET", headers=self._headers())
        try:
            with urlopen(req, timeout=timeout or min(self.timeout, 5.0)) as r:  # noqa: S310 — fixed scheme, internal tunnel
                body = r.read().decode().strip()
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return body
        if isinstance(parsed, dict):
            return str(parsed.get("version") or parsed.get("__version__") or body)
        return str(parsed)

    def list_build_simulations(self, simulator_id: int) -> list:
        """GET /api/v1/simulations?simulator_id=N — simulation runs on the
        deployment. The ``simulator_id`` query param is required by the API but
        does not actually filter (the server returns every recorded simulation),
        so callers must filter the returned list by ``simulator_id`` themselves.
        Returns the raw list of simulation records."""
        return self._get("/api/v1/simulations", {"simulator_id": simulator_id})

    def composite_resolve(self, simulator_id: int, composite_ref: str,
                          overrides: dict | None = None, timeout: float | None = None) -> dict:
        """Resolve a composite IN a build's environment, on the deployment.

        POST /core/v1/simulator/{id}/composite-resolve — sms-api runs build_core
        for ``composite_ref`` (with ``overrides``) inside build ``simulator_id``'s
        image and returns the resolved-composite JSON (shape-compatible with the
        dashboard's local /api/composite-resolve). Raises SmsApiError on failure.
        """
        return self._post(
            f"/core/v1/simulator/{simulator_id}/composite-resolve",
            json_body={"composite_ref": composite_ref, "overrides": overrides or {}},
        )

    def download_workspace(self, simulator_id: int, dest_dir: Path, timeout: float | None = None) -> Path:
        """Stream a build's repo@commit workspace tarball (SP1's endpoint) to
        dest_dir/workspace.tar.gz."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = dest_dir / "workspace.tar.gz"
        url = f"{self.base_url}/api/v1/simulations/workspace?simulator_id={simulator_id}"
        req = Request(url, method="GET", headers=self._headers("application/gzip"))
        to = timeout if timeout is not None else DOWNLOAD_TIMEOUT
        try:
            with urlopen(req, timeout=to) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        return out_path

    def simulation_status(self, simulation_id: int) -> dict:
        return self._get(f"/api/v1/simulations/{simulation_id}/status")

    def get_simulation(self, simulation_id: int) -> dict:
        """GET /api/v1/simulations/{id} — the full simulation record sms-api
        holds server-side: ``config`` (the actually-merged run config),
        ``simulator_id``, ``parca_dataset_id``, ``experiment_id``, ``num_seeds``.

        Unlike :meth:`simulation_status` (status/error_message only), this is
        what the P1-11 remote-run-provenance fix reads to record the REAL
        merged config and requested seed count sms-api resolved, instead of
        recomputing/guessing them on the landing laptop (audit §3.9)."""
        return self._get(f"/api/v1/simulations/{simulation_id}")

    def simulator_commit(self, simulator_id: int) -> str | None:
        """Resolve ``simulator_id`` -> the git commit sms-api actually built
        and ran, from sms-api's OWN simulator registry (``GET
        /core/v1/simulator/versions``) — never the landing laptop's local
        checkout (that mismatch was the P1-11 "partly wrong" finding, audit
        §3.9). ``None`` (not raised) when the id can't be resolved — e.g. an
        old/pruned registry — since this is best-effort provenance and must
        never block landing."""
        try:
            versions = self.list_simulators().get("versions") or []
        except SmsApiError:
            return None
        for row in versions:
            if isinstance(row, dict) and row.get("database_id") == simulator_id:
                return row.get("git_commit_hash")
        return None

    def simulation_chain_progress(self, simulation_id: int) -> dict:
        """Backlog item 6: real per-seed aggregate progress for a chain-dispatch
        campaign (viva-api PR #257) — {seeds_total, seeds_succeeded, seeds_failed,
        seeds_in_progress, terminal, status}. 404 unknown simulation, 409 when the
        simulation exists but isn't a chain-dispatch campaign (nothing to
        aggregate — callers should use ``simulation_status`` for those)."""
        return self._get(f"/api/v1/simulations/{simulation_id}/chain-progress")

    def observables(self, simulation_id: int, names: list[str], seed: int = 0) -> dict:
        params = {"seed": seed}
        if names:
            params["names"] = ",".join(names)
        return self._get(f"/api/v1/simulations/{simulation_id}/observables", params)

    def _delete(self, path: str) -> dict:
        url = self.base_url + path
        req = Request(url, method="DELETE", headers=self._headers())
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310 — fixed scheme, internal tunnel
                body = r.read().decode()
                return json.loads(body) if body else {}
        except HTTPError as e:
            raise SmsApiError(f"DELETE {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"DELETE {url} failed (sms-api unreachable): {e}") from e

    def _post(self, path: str, params: dict | None = None, json_body: dict | None = None) -> dict:
        # doseq=True so list-valued params become repeated keys (?observables=a&observables=b)
        url = self.base_url + path
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        data = json.dumps(json_body).encode() if json_body is not None else None
        headers = self._headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, method="POST", headers=headers)
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                return json.loads(r.read().decode())
        except HTTPError as e:
            raise SmsApiError(f"POST {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"POST {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e

    def upload_simulator(self, simulator: dict, force: bool = False) -> dict:
        params = {"force": "true"} if force else None
        return self._post("/core/v1/simulator/upload", params=params, json_body=simulator)

    def run_simulation(
        self,
        *,
        simulator_id: int,
        num_generations: int,
        num_seeds: int,
        run_parca: bool,
        observables: list[str],
        experiment_id: str | None = None,
        description: str | None = None,
        analysis_options: dict | None = None,
        extra_params: dict | None = None,
    ) -> dict:
        params: dict = {
            "simulator_id": simulator_id,
            "num_generations": num_generations,
            "num_seeds": num_seeds,
            "run_parca": run_parca,
        }
        if experiment_id is not None:
            params["experiment_id"] = experiment_id
        if description is not None:
            params["description"] = description
        if observables:
            params["observables"] = observables  # list → repeated key via doseq
        # analysis_options/extra_params are both nested-dict-shaped bodies with no
        # Query()/Body() wrapper on the sms-api side — FastAPI reads them from the
        # JSON request body, not the query string (nested dicts don't survive
        # urlencode sensibly), so they go in json_body rather than alongside the
        # other flat/scalar params above.
        json_body: dict = {}
        if analysis_options:
            json_body["analysis_options"] = analysis_options
        if extra_params:
            json_body["extra_params"] = extra_params
        return self._post("/api/v1/simulations", params=params, json_body=json_body or None)

    def run_analysis(self, simulation_id: int, modules: dict) -> dict:
        """POST /api/v1/simulations/{id}/analysis — trigger standalone analysis on
        a completed simulation's output. Returns immediately with a job_id and
        (for Ray-backend simulators) a database_id -- pass the latter to
        analysis_status() to poll for real completion."""
        # modules is read via query param (?modules=<json>) on the sms-api side,
        # not a request body -- matches the endpoint's own OpenAPI shape.
        return self._post(
            f"/api/v1/simulations/{simulation_id}/analysis",
            params={"modules": json.dumps(modules)},
        )

    def analysis_status(self, analysis_id: int) -> dict:
        """GET /analyses/{id}/status — poll a triggered analysis's real status.
        Only meaningful when run_analysis() returned a database_id (Ray-backend
        simulators); resolved server-side via S3-exists probe, since there is
        no persistent job-status API for the backing K8s Job."""
        return self._get(f"/analyses/{analysis_id}/status")

    # ------------------------------------------------------------------
    # Compose endpoints (generic .pbg runner, Phase C)
    # ------------------------------------------------------------------

    def compose_check(self, pbg_bytes: bytes) -> dict:
        """GET /compose/v1/simulation/check — verify compose endpoint reachability.

        Raises :exc:`SmsApiError` if the server is unreachable or returns a
        non-200 status.
        """
        return self._get("/compose/v1/simulation/check")

    def compose_submit(
        self,
        pbg_bytes: bytes,
        extra_pip_deps: list[str] | None = None,
        interval_time: float = 1.0,
        filename: str = "composite.pbg",
        analysis_options: dict | None = None,
    ) -> int:
        """POST /compose/v1/simulation/run — submit a .pbg file for execution.

        The file is uploaded as multipart/form-data with the field name
        ``uploaded_file`` (required by the sms-api endpoint).  Any
        ``extra_pip_deps`` are appended as repeated ``extra_pip_deps`` query
        parameters so the container can install them before running.

        Parameters
        ----------
        pbg_bytes:
            Raw bytes of the ``.pbg`` JSON document.
        extra_pip_deps:
            Additional pip-installable dependencies (e.g.
            ``["git+https://github.com/org/repo.git@sha"]``).
        interval_time:
            Step interval forwarded to the sms-api run endpoint.
        filename:
            Filename reported in the multipart header (cosmetic).
        analysis_options:
            v2ecoli-shaped ``{scale: {name: params}}`` analyses to run
            server-side (composite-auto-results Task 8). Sent as a
            JSON-encoded string in a multipart ``analysis_options`` form
            field — NOT a query param. sms-api's ``/compose/v1/simulation/run``
            route reads this via ``Form()`` + ``json.loads()``, not
            ``Query()``; a query param there is silently dropped by FastAPI
            and no analyses ever run (the bug behind #1022 being a silent
            no-op). Omitted entirely (no field at all) when ``None``.

        Returns
        -------
        int
            ``simulation_database_id`` from the response.
        """
        boundary = "----vivdash00boundary"
        parts = [
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="uploaded_file"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n"
                "\r\n"
            ).encode() + pbg_bytes + b"\r\n"
        ]
        if analysis_options:
            parts.append(
                (
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="analysis_options"\r\n'
                    "\r\n"
                    f"{json.dumps(analysis_options)}\r\n"
                ).encode()
            )
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        content_type = f"multipart/form-data; boundary={boundary}"

        params: dict = {"interval_time": interval_time}
        if extra_pip_deps:
            params["extra_pip_deps"] = extra_pip_deps  # list → repeated key via doseq

        url = self.base_url + "/compose/v1/simulation/run"
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"

        req = Request(
            url,
            data=body,
            method="POST",
            headers={**self._headers(), "Content-Type": content_type},
        )
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                data = json.loads(r.read().decode())
        except HTTPError as e:
            raise SmsApiError(f"POST {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(
                f"POST {url} failed (sms-api unreachable — is the tunnel up?): {e}"
            ) from e
        return int(data["simulation_database_id"])

    def compose_status(self, task_id: int) -> dict:
        """GET /compose/v1/simulation/{id}/status — poll run status."""
        return self._get(f"/compose/v1/simulation/{task_id}/status")

    def compose_status_batch(self, ids: "list[int]") -> "list[dict]":
        """GET /compose/v1/simulations/status/batch?ids=… — many runs, one call.

        viva-api returns a JSON **list** here (``list[ComposeHpcRun]``), unlike
        every other endpoint on this client, so the ``_get`` result is widened
        rather than trusted as a dict. Existing to serve reconcile-style polling:
        a caller holding N in-flight ``simulation_id``s asks once instead of N
        times (REFACTOR-PLAN §2A.8 / run-orchestration-consolidation §A2').
        """
        if not ids:
            return []
        raw: Any = self._get("/compose/v1/simulations/status/batch",
                             {"ids": [int(i) for i in ids]})
        return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []

    def download_compose_results(self, sim_id: int, dest: Path, timeout: float | None = None) -> Path:
        """GET /compose/v1/simulation/{id}/results — stream results.tar.gz to dest.

        The route is backend-aware server-side (compose-results-land-p0 T5a):
        a Ray/Batch (GovCloud) simulation streams a gzip tarball of its S3
        output prefix (mirroring the study path's ``download_data``), while a
        SLURM simulation's SSH/SCP branch is unchanged. Both are served under
        the same ``.tar.gz`` contract this client now expects, so
        ``land_remote_run``/``fold_analyses`` (which already read ``.tar.gz``)
        work unmodified once this lands.

        Returns
        -------
        Path
            ``dest / "results.tar.gz"``
        """
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        out_path = dest / "results.tar.gz"
        url = f"{self.base_url}/compose/v1/simulation/{sim_id}/results"
        req = Request(url, method="GET", headers=self._headers("application/gzip"))
        to = timeout if timeout is not None else DOWNLOAD_TIMEOUT
        try:
            with urlopen(req, timeout=to) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(
                f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}"
            ) from e
        return out_path

    def download_data(self, simulation_id: int, dest_dir: Path, timeout: float | None = None) -> Path:
        """Stream the run's native-store tar.gz (POST /data) to dest_dir/sim_<id>.tar.gz."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = dest_dir / f"sim_{simulation_id}.tar.gz"
        url = f"{self.base_url}/api/v1/simulations/{simulation_id}/data"
        req = Request(url, data=b"", method="POST", headers=self._headers("application/gzip"))
        to = timeout if timeout is not None else DOWNLOAD_TIMEOUT
        try:
            with urlopen(req, timeout=to) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"POST {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"POST {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        return out_path
