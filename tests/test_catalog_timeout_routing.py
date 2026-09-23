"""Catalog-class methods get the pool's long socket timeout (perf: RENCI 504).

The heavy interactive queries (registry_catalog / composites_full /
discover_composites) trigger the full process-module import walk on a cold worker
— minutes on a large venv. The 60s interactive default would kill+respawn the
worker mid-import on every call; these tests pin that catalog-class methods
instead receive the pool's ``catalog_timeout`` while ordinary interactive methods
keep the default.
"""
from vivarium_workbench.lib.env_worker_pool import WorkerPool
from vivarium_workbench.lib.env_worker_routing import is_catalog_class, is_job_class


def test_catalog_class_membership():
    for m in ("registry_catalog", "composites_full", "discover_composites"):
        assert is_catalog_class(m) is True
        assert is_job_class(m) is False          # answers a question, not a run
    assert is_catalog_class("run_study") is False
    assert is_catalog_class("ping") is False
    assert is_catalog_class("unknown_method") is False


class _FakeWorker:
    def __init__(self, calls):
        self._calls = calls

    def call(self, method, params=None, *, timeout=None):
        self._calls.append((method, timeout))
        return {"ok": True, "method": method}

    def close(self):
        pass

    def alive(self):
        return True


class _FakeLauncher:
    kind = "local"

    def __init__(self, calls):
        self._calls = calls

    def env_key(self, ws):
        return "env"

    def launch(self, ws, *, interpreter=None, timeout=None):
        return _FakeWorker(self._calls)


def test_catalog_method_gets_catalog_timeout_others_get_default():
    calls: list = []
    pool = WorkerPool(launcher=_FakeLauncher(calls), call_timeout=60)
    pool.catalog_timeout = 1234
    try:
        pool.call("/ws", "registry_catalog")
        pool.call("/ws", "composites_full")
        pool.call("/ws", "ping")
    finally:
        pool.close_all()

    by_method = dict(calls)
    # Heavy catalog builds get the long timeout so the cold import walk completes.
    assert by_method["registry_catalog"] == 1234
    assert by_method["composites_full"] == 1234
    # An ordinary interactive method is left on the worker's default (None here).
    assert by_method["ping"] is None


def test_explicit_timeout_overrides_catalog_default():
    calls: list = []
    pool = WorkerPool(launcher=_FakeLauncher(calls), call_timeout=60)
    pool.catalog_timeout = 1234
    try:
        pool.call("/ws", "registry_catalog", timeout=7)
    finally:
        pool.close_all()
    assert dict(calls)["registry_catalog"] == 7
