"""``artifacts.fetch_store`` (docs/backend-adapters.md §2.1, step P2 Remote tab R2).

``simulations_index.resolve_or_fetch_store``'s remote half: the core keeps the routing (``backends.for_existing``)
and owns the temporary directory; the backend's ``artifacts.fetch_store`` downloads into it. These tests pin the
contract from both sides: the core removes the directory when the facet returns ``None`` or raises, and asks no
backend without ``artifacts``; SMS's facet (``sms_legacy.fetch_store_tail``) extracts the tar into
``dest/"extracted"``, answers ``None`` for an ``SmsApiError``, and reads its seams through ``sms_legacy``.
"""
from __future__ import annotations

import ast
import io
import tarfile
from pathlib import Path

import pytest

from vivarium_workbench.lib import backends
from vivarium_workbench.lib import simulations_index as si

ROOT = Path(__file__).resolve().parents[1]

ROW = {"run_id": "r", "db_path": None, "store_path": "s3://bucket/r", "study_slug": None,
       "remote_origin": {"simulation_id": 113, "deployment": "d"}}


class _Artifacts:
    """A fake ``artifacts`` facet: records ``(run_id, dest)`` and answers with ``outcome(dest)``."""

    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []

    def fetch_store(self, run_id, dest):
        self.calls.append((run_id, dest))
        assert dest.is_dir()  # the core made it
        return self.outcome(dest)


def _route_to(monkeypatch, backend):
    asked: list = []
    monkeypatch.setattr(backends, "for_existing",
                        lambda ws, rid, deployment=None, study=None: asked.append((rid, deployment)) or backend)
    return asked


def _backend(artifacts):
    return backends.Backend("fake", runs=object(), artifacts=artifacts)  # type: ignore[arg-type]


# --- the core's half -------------------------------------------------------------------------------------------------

def test_a_fetched_store_is_returned_with_its_directory(tmp_path, monkeypatch):
    def fetched(dest):
        (dest / "extracted").mkdir()
        return dest / "extracted"

    art = _Artifacts(fetched)
    asked = _route_to(monkeypatch, _backend(art))
    store, tmp = si.resolve_or_fetch_store(tmp_path, ROW)
    try:
        assert asked == [(113, "d")]
        assert art.calls == [("113", Path(tmp.name))]  # the run id as a string; the core's directory
        assert store == Path(tmp.name) / "extracted" and store.is_dir()
    finally:
        tmp.cleanup()


def test_none_is_none_none_and_the_directory_is_removed(tmp_path, monkeypatch):
    art = _Artifacts(lambda dest: None)
    _route_to(monkeypatch, _backend(art))
    assert si.resolve_or_fetch_store(tmp_path, ROW) == (None, None)
    assert len(art.calls) == 1 and not art.calls[0][1].exists()


def test_an_error_propagates_and_the_directory_is_removed(tmp_path, monkeypatch):
    def boom(dest):
        (dest / "partial").write_text("x")
        raise tarfile.ReadError("corrupt tar")

    art = _Artifacts(boom)
    _route_to(monkeypatch, _backend(art))
    with pytest.raises(tarfile.ReadError):
        si.resolve_or_fetch_store(tmp_path, ROW)
    assert len(art.calls) == 1 and not art.calls[0][1].exists()


@pytest.mark.parametrize("chosen", [
    _backend(None),                                    # a backend without artifacts (compose-api, viva-core)
    ({"error": "other backend"}, 409),                 # a refusal (another deployment's run)
    None,                                              # no backend answers for the id
], ids=["no-artifacts", "refusal", "none"])
def test_no_artifacts_facet_means_nothing_is_fetched(tmp_path, monkeypatch, chosen):
    _route_to(monkeypatch, chosen)
    assert si.resolve_or_fetch_store(tmp_path, ROW) == (None, None)


def test_a_local_store_or_a_row_without_a_simulation_id_never_routes(tmp_path, monkeypatch):
    asked = _route_to(monkeypatch, _backend(_Artifacts(lambda d: pytest.fail("fetched"))))
    (tmp_path / "s.zarr").mkdir()
    assert si.resolve_or_fetch_store(tmp_path, {**ROW, "store_path": "s.zarr"}) == ((tmp_path / "s.zarr").resolve(),
                                                                                      None)
    assert si.resolve_or_fetch_store(tmp_path, {**ROW, "remote_origin": {"deployment": "d"}}) == (None, None)
    assert asked == []


def test_the_core_names_no_sms_download():
    """The download, its error type and the tar handling left the core: none of them is spelled in
    ``simulations_index`` (a ``sys.modules`` reach-in would not show in import-linter)."""
    src = (ROOT / "vivarium_workbench" / "lib" / "simulations_index.py").read_text()
    for name in ("download_data", "SmsApiError", "SmsApiClient", "sms_api_client", "tarfile", "SMS_LEGACY"):
        assert name not in src, name


# --- SMS's half (the plugin: these tests are marked ``plugin``) ------------------------------------------------------

def SmsArtifacts():  # noqa: N802 -- the plugin's facet class, imported when a plugin test builds one
    from vivarium_workbench_sms.analyses import SmsArtifacts as _SmsArtifacts
    return _SmsArtifacts()


def _tar_bytes(tree: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in tree.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def client(monkeypatch):
    """``sms_legacy.SmsApiClient`` / ``sms_legacy._sms_api_base``: the seams' one patch target each."""
    from vivarium_workbench_sms import sms_legacy

    seen: list = []

    class Client:
        outcome: object = {"exp/zarr.json": b"{}", "exp/a/0": b"\x00"}

        def __init__(self, base):
            seen.append(("base", base))

        def download_data(self, simulation_id, dest_dir, timeout=None):
            seen.append(("download", simulation_id, Path(dest_dir)))
            if isinstance(Client.outcome, Exception):
                raise Client.outcome
            out = Path(dest_dir) / f"sim_{simulation_id}.tar.gz"
            out.write_bytes(_tar_bytes(Client.outcome))  # type: ignore[arg-type]
            return out

    monkeypatch.setattr(sms_legacy, "SmsApiClient", Client)
    monkeypatch.setattr(sms_legacy, "_sms_api_base", lambda: "http://configured")
    Client.seen = seen  # type: ignore[attr-defined]
    return Client


@pytest.mark.plugin
def test_sms_fetch_store_extracts_the_tar_into_dest_extracted(tmp_path, client):
    store = SmsArtifacts().fetch_store("113", tmp_path)
    assert store == tmp_path / "extracted"
    assert sorted(str(p.relative_to(store)) for p in store.rglob("*") if p.is_file()) == ["exp/a/0", "exp/zarr.json"]
    assert client.seen == [("base", "http://configured"), ("download", 113, tmp_path)]


@pytest.mark.plugin
def test_sms_fetch_store_answers_none_for_an_sms_api_error(tmp_path, client):
    from vivarium_workbench_sms.sms_api_client import SmsApiError

    client.outcome = SmsApiError("sms-api unreachable")
    assert SmsArtifacts().fetch_store("113", tmp_path) is None
    assert not (tmp_path / "extracted").exists()


@pytest.mark.plugin
def test_sms_fetch_store_lets_another_error_propagate(tmp_path, client):
    client.outcome = OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        SmsArtifacts().fetch_store("113", tmp_path)


@pytest.mark.plugin
def test_sms_fetch_store_refuses_a_tar_member_that_escapes(tmp_path, client):
    client.outcome = {"../escape.txt": b"x"}
    with pytest.raises(tarfile.FilterError):
        SmsArtifacts().fetch_store("113", tmp_path)
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.plugin
def test_the_plugin_reads_fetch_store_tail_through_sms_legacy():
    """One patch target per seam: no plugin module binds ``fetch_store_tail`` by name. (The plugin's sources are found
    through its import, so without the plugin this fails instead of passing over no files.)"""
    import vivarium_workbench_sms

    for path in Path(vivarium_workbench_sms.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("sms_legacy"):
                assert "fetch_store_tail" not in {a.name for a in node.names}, path
