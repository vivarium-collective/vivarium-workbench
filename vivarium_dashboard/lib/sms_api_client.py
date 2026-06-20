"""Thin HTTP client for the sms-api endpoints the remote-run pipeline calls.

Stdlib-only (urllib) to avoid adding a dependency, matching server.py's existing
outbound-HTTP approach. Pure HTTP — no DB, no orchestration. Parameterized by
base_url (the SSM tunnel, default http://localhost:8080).
"""

from __future__ import annotations

import json
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

    def simulator_status(self, simulator_id: int) -> dict:
        return self._get("/core/v1/simulator/status", {"simulator_id": simulator_id})

    def simulation_status(self, simulation_id: int) -> dict:
        return self._get(f"/api/v1/simulations/{simulation_id}/status")

    def observables_index(self, simulation_id: int, seed: int = 0) -> dict:
        return self._get(f"/api/v1/simulations/{simulation_id}/observables/index", {"seed": seed})

    def observables(self, simulation_id: int, names: list[str], seed: int = 0) -> dict:
        params = {"seed": seed}
        if names:
            params["names"] = ",".join(names)
        return self._get(f"/api/v1/simulations/{simulation_id}/observables", params)
