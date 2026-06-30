"""Thin HTTP client for the sms-api endpoints the remote-run pipeline calls.

Stdlib-only (urllib) to avoid adding a dependency, matching server.py's existing
outbound-HTTP approach. Pure HTTP — no DB, no orchestration. Parameterized by
base_url (the SSM tunnel, default http://localhost:8080).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SmsApiError(Exception):
    """Raised when an sms-api call fails (non-200 or connection error)."""


class SmsApiClient:
    def __init__(self, base_url: str = "http://localhost:8080", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = self.base_url + path
        if params:
            url = f"{url}?{urlencode(params)}"
        req = Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310 — fixed scheme, internal tunnel
                return json.loads(r.read().decode())
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}") from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e

    def latest_simulator(self, repo_url: str, branch: str) -> dict:
        return self._get("/core/v1/simulator/latest", {"git_branch": branch, "git_repo_url": repo_url})

    def register_simulator(self, repo_url: str, branch: str, commit: str) -> dict:
        """POST /core/v1/simulator/upload — register a repo@commit build (async image build)."""
        return self._post("/core/v1/simulator/upload", json_body={
            "git_repo_url": repo_url, "git_branch": branch, "git_commit_hash": commit,
        })

    def simulator_status(self, simulator_id: int) -> dict:
        return self._get("/core/v1/simulator/status", {"simulator_id": simulator_id})

    def list_simulators(self) -> dict:
        """GET /core/v1/simulator/versions — all registered simulator builds."""
        return self._get("/core/v1/simulator/versions")

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
        req = Request(url, method="GET", headers={"Accept": "application/gzip"})
        to = timeout if timeout is not None else self.timeout
        try:
            with urlopen(req, timeout=to) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}") from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        return out_path

    def simulation_status(self, simulation_id: int) -> dict:
        return self._get(f"/api/v1/simulations/{simulation_id}/status")

    def observables_index(self, simulation_id: int, seed: int = 0) -> dict:
        return self._get(f"/api/v1/simulations/{simulation_id}/observables/index", {"seed": seed})

    def observables(self, simulation_id: int, names: list[str], seed: int = 0) -> dict:
        params = {"seed": seed}
        if names:
            params["names"] = ",".join(names)
        return self._get(f"/api/v1/simulations/{simulation_id}/observables", params)

    def _post(self, path: str, params: dict | None = None, json_body: dict | None = None) -> dict:
        # doseq=True so list-valued params become repeated keys (?observables=a&observables=b)
        url = self.base_url + path
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        data = json.dumps(json_body).encode() if json_body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, method="POST", headers=headers)
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                return json.loads(r.read().decode())
        except HTTPError as e:
            raise SmsApiError(f"POST {url} -> {e.code}") from e
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
        return self._post("/api/v1/simulations", params=params)

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

        Returns
        -------
        int
            ``simulation_database_id`` from the response.
        """
        boundary = "----vivdash00boundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="uploaded_file"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n"
            "\r\n"
        ).encode() + pbg_bytes + f"\r\n--{boundary}--\r\n".encode()
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
            headers={"Accept": "application/json", "Content-Type": content_type},
        )
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                data = json.loads(r.read().decode())
        except HTTPError as e:
            raise SmsApiError(f"POST {url} -> {e.code}") from e
        except (URLError, OSError) as e:
            raise SmsApiError(
                f"POST {url} failed (sms-api unreachable — is the tunnel up?): {e}"
            ) from e
        return int(data["simulation_database_id"])

    def compose_status(self, task_id: int) -> dict:
        """GET /compose/v1/simulation/{id}/status — poll run status."""
        return self._get(f"/compose/v1/simulation/{task_id}/status")

    def download_compose_results(self, sim_id: int, dest: Path, timeout: float | None = None) -> Path:
        """GET /compose/v1/simulation/{id}/results — stream results.zip to dest.

        Returns
        -------
        Path
            ``dest / "results.zip"``
        """
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        out_path = dest / "results.zip"
        url = f"{self.base_url}/compose/v1/simulation/{sim_id}/results"
        req = Request(url, method="GET", headers={"Accept": "application/zip"})
        to = timeout if timeout is not None else self.timeout
        try:
            with urlopen(req, timeout=to) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}") from e
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
        req = Request(url, data=b"", method="POST", headers={"Accept": "application/gzip"})
        to = timeout if timeout is not None else self.timeout
        try:
            with urlopen(req, timeout=to) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"POST {url} -> {e.code}") from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"POST {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        return out_path
