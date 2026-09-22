"""Spec derivation from workspace.yaml + pool provisioning wiring."""
import yaml

from vivarium_workbench.lib.env_worker_provision import install_specs_from_workspace
from vivarium_workbench.lib.env_worker_pool import WorkerPool


def _ws(tmp_path, imports):
    (tmp_path / "workspace.yaml").write_text(
        yaml.safe_dump({"name": "host", "imports": imports}))
    return tmp_path


def test_derives_pypi_and_reference_specs(tmp_path):
    ws = _ws(tmp_path, {
        "viva-munk": {"mode": "pypi", "pypi_name": "viva-munk", "installed": True,
                      "source": "https://github.com/x/viva-munk.git", "ref": "main",
                      "package": "viva_munk"},
        "viva-tumor-tcell": {"mode": "reference", "installed": True,
                             "source": "https://github.com/vivarium-collective/viva-tumor-tcell.git",
                             "ref": "main", "path": "external/viva-tumor-tcell",
                             "package": "viva_tumor_tcell"},
    })
    specs = {s["name"]: s for s in install_specs_from_workspace(ws)}
    assert set(specs) == {"viva-munk", "viva-tumor-tcell"}
    assert specs["viva-munk"]["mode"] == "pypi"
    assert specs["viva-munk"]["pypi_name"] == "viva-munk"
    assert specs["viva-tumor-tcell"]["mode"] == "reference"
    assert specs["viva-tumor-tcell"]["source"].endswith("viva-tumor-tcell.git")
    assert specs["viva-tumor-tcell"]["ref"] == "main"


def test_skips_not_installed(tmp_path):
    ws = _ws(tmp_path, {"x": {"mode": "pypi", "pypi_name": "x", "installed": False}})
    assert install_specs_from_workspace(ws) == []


def test_skips_no_installable_form(tmp_path):
    # A reference entry with neither pypi_name nor source: nothing to install.
    ws = _ws(tmp_path, {"x": {"mode": "reference", "installed": True, "path": "external/x"}})
    assert install_specs_from_workspace(ws) == []


def test_missing_workspace_yaml_returns_empty(tmp_path):
    assert install_specs_from_workspace(tmp_path) == []


def test_malformed_imports_returns_empty(tmp_path):
    (tmp_path / "workspace.yaml").write_text("imports: [not, a, dict]\n")
    assert install_specs_from_workspace(tmp_path) == []


# ---- pool wiring ----------------------------------------------------------

class _FakeWorker:
    def __init__(self, result=None, raises=None):
        self.calls = []
        self._result = result or {"ok": True, "results": []}
        self._raises = raises

    def call(self, method, params=None):
        self.calls.append((method, params))
        if self._raises:
            raise self._raises
        return self._result


def test_provision_worker_pushes_derived_specs(tmp_path):
    ws = _ws(tmp_path, {"viva-munk": {"mode": "pypi", "pypi_name": "viva-munk", "installed": True}})
    pool = WorkerPool()
    worker = _FakeWorker(result={"ok": True, "results": [{"name": "viva-munk", "ok": True}]})
    pool._provision_worker(str(ws), worker)
    assert worker.calls, "install_modules should have been called"
    method, params = worker.calls[0]
    assert method == "install_modules"
    assert params["modules"][0]["name"] == "viva-munk"


def test_provision_worker_noop_without_specs(tmp_path):
    ws = _ws(tmp_path, {})  # no imports
    pool = WorkerPool()
    worker = _FakeWorker()
    pool._provision_worker(str(ws), worker)
    assert worker.calls == []


def test_provision_worker_best_effort_on_error(tmp_path):
    ws = _ws(tmp_path, {"x": {"mode": "pypi", "pypi_name": "x", "installed": True}})
    pool = WorkerPool()
    worker = _FakeWorker(raises=RuntimeError("worker down"))
    # must not raise — a provisioning failure leaves the worker usable
    pool._provision_worker(str(ws), worker)
    assert worker.calls  # attempted
