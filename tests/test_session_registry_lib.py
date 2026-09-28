"""Slice 1 of the workspace-context refactor: SessionRegistry + WorkspaceContext.

Verifies the per-session routing seam is behavior-preserving — an unbound
session (and every cookie-less client) resolves to the process default
workspace, while an explicit bind routes that session elsewhere.
"""
import errno
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from vivarium_workbench.lib import (
    active_workspace,
    session_registry,
    workspace_context,
)
from vivarium_workbench.lib.env_compat import BUILD_CACHE_ENV


@pytest.fixture(autouse=True)
def _clean_registry():
    session_registry.clear()
    saved = active_workspace.get_workspace_root()
    yield
    session_registry.clear()
    if saved is not None:
        active_workspace.set_workspace_root(saved)


# ---------------------------------------------------------------------------
# SessionRegistry
# ---------------------------------------------------------------------------
def test_mint_key_is_unguessable_and_unique():
    keys = {session_registry.mint_key() for _ in range(100)}
    assert len(keys) == 100
    assert all(len(k) >= 32 for k in keys)


def test_unbound_session_has_no_entry():
    assert session_registry.get("never-bound") is None
    assert session_registry.get(None) is None


def test_rebind_then_get(tmp_path):
    session_registry.rebind("s1", tmp_path)
    entry = session_registry.get("s1")
    assert entry is not None and entry.source_path == tmp_path


def test_drop_forgets_binding(tmp_path):
    session_registry.rebind("s1", tmp_path)
    session_registry.drop("s1")
    assert session_registry.get("s1") is None


# ---------------------------------------------------------------------------
# WorkspaceContext resolution — the behavior-preservation contract
# ---------------------------------------------------------------------------
def test_unbound_resolves_to_process_default(tmp_path):
    """A cookie-less / unbound session resolves to the registered global root —
    exactly the pre-seam behavior."""
    active_workspace.set_workspace_root(tmp_path)
    ctx = workspace_context.resolve(None)
    assert ctx.ws_root == tmp_path.resolve()
    assert ctx.session_key is None


def test_unknown_key_resolves_to_process_default(tmp_path):
    active_workspace.set_workspace_root(tmp_path)
    ctx = workspace_context.resolve("stale-key-not-in-registry")
    assert ctx.ws_root == tmp_path.resolve()


def test_bound_session_resolves_to_its_path(tmp_path):
    """A bound session routes to its own workspace, not the global default."""
    other = tmp_path / "other-ws"
    other.mkdir()
    active_workspace.set_workspace_root(tmp_path)          # global default
    session_registry.rebind("s1", other)                    # this session bound elsewhere

    assert workspace_context.resolve("s1").ws_root == other  # bound → its own
    assert workspace_context.resolve(None).ws_root == tmp_path.resolve()  # unbound → default


# ---------------------------------------------------------------------------
# Slice 4: per-session switch semantics (endpoint-level, in-process)
# ---------------------------------------------------------------------------
def test_switch_is_isolated_across_sessions(tmp_path, monkeypatch):
    """The load-bearing slice-4 property: session A switching does not move
    session B, nor the process-global default."""
    from fastapi.testclient import TestClient
    from viva_superpowers import workspace_catalog
    from vivarium_workbench.api.app import create_app
    from vivarium_workbench.lib import _root

    session_registry.clear()
    default = tmp_path / "default"; default.mkdir(); (default / "workspace.yaml").write_text("name: d\n")
    other = tmp_path / "other"; other.mkdir(); (other / "workspace.yaml").write_text("name: o\n")
    _root.set_workspace_root(default)
    monkeypatch.setattr(workspace_catalog, "list_workspaces",
                        lambda: [{"path": str(other), "name": "o"}])

    app = create_app()
    a = TestClient(app)   # each TestClient keeps its own cookie jar = its own session
    b = TestClient(app)

    # Establish both sessions (first GET mints each cookie).
    assert a.get("/health").status_code == 200
    assert b.get("/health").status_code == 200

    # Session A switches to `other`.
    r = a.post("/api/source/switch", json={"path": str(other)})
    assert r.status_code == 200

    key_a = a.cookies.get(session_registry.SESSION_COOKIE)
    key_b = b.cookies.get(session_registry.SESSION_COOKIE)
    assert key_a and key_b and key_a != key_b

    # A is bound to `other`; B is untouched (unbound → default); global unmoved.
    assert session_registry.get(key_a).source_path == Path(str(other))
    assert session_registry.get(key_b) is None
    assert _root.get_workspace_root() == default.resolve()


# ---------------------------------------------------------------------------
# Durable bindings (item 3, Phase 1; docs/session-registry.md §6, "Durable" tier).
# A "restart" is a fresh import of the module: every piece of in-memory state is gone
# and the store on disk is untouched — what a new process sees. Every test here talks to
# a real file; only a fault (a full disk, an I/O error) is ever injected, and only where
# the fault itself is the proposition under test.
# ---------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path, monkeypatch):
    """A private build cache, and so a private durable session-bindings store."""
    root = tmp_path / "bc"
    monkeypatch.setenv(BUILD_CACHE_ENV, str(root))
    session_registry.clear()
    return root / session_registry.STORE_FILENAME


def _stored(store_path):
    return json.loads(store_path.read_text(encoding="utf-8"))["sessions"]


def _restart():
    """A pod restart, as far as the module can tell: all its state is gone, the volume is
    not. Reloading also restores the real clock and the real writer, so a test that fakes
    either re-installs its fake afterwards."""
    importlib.reload(session_registry)


def _join_flushers():
    """Wait for the background writer threads ``get`` schedules."""
    for thread in threading.enumerate():
        if thread.name == "session-registry-flush":
            thread.join(5)


def _fake_clock(monkeypatch, start=1_000.0):
    clock = {"t": start}
    monkeypatch.setattr(session_registry, "_now", lambda: clock["t"])
    return clock


def _stall_writes(monkeypatch):
    """Make every store write block until released — a hung volume. Returns
    ``(write_started, release, writer)``; ``writer["thread"]`` is the thread that wrote."""
    write_started, release, writer = threading.Event(), threading.Event(), {}
    real_write = session_registry.atomic_write_text

    def stalled_write(path, text):
        writer["thread"] = threading.current_thread()
        write_started.set()
        assert release.wait(5), "the test never released the stalled write"
        real_write(path, text)

    monkeypatch.setattr(session_registry, "atomic_write_text", stalled_write)
    return write_started, release, writer


def test_binding_survives_simulated_restart(store, tmp_path):
    """The property Phase 1 exists for: a process with no memory of the binding — a
    pod restart — still routes the session to its workspace."""
    ws = tmp_path / "ws"; ws.mkdir()
    session_registry.rebind("session-aaaa", ws)
    assert store.exists()

    _restart()  # memory gone, volume intact

    entry = session_registry.get("session-aaaa")
    assert entry is not None and entry.source_path == ws


def test_rebind_replaces_the_durable_binding(store, tmp_path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    session_registry.rebind("session-aaaa", a)
    session_registry.rebind("session-aaaa", b)
    _restart()
    assert session_registry.get("session-aaaa").source_path == b


def test_drop_is_durable(store, tmp_path):
    session_registry.rebind("session-aaaa", tmp_path)
    session_registry.drop("session-aaaa")
    _restart()
    assert session_registry.get("session-aaaa") is None


def test_many_sessions_survive_a_restart_independently(store, tmp_path):
    """No test ever held two sessions: a store that kept only the latest binding, or a
    `drop` that cleared everyone, would have passed. Each session keeps its OWN path."""
    dirs = {name: tmp_path / name for name in ("a", "b", "c")}
    for name, path in dirs.items():
        path.mkdir()
        session_registry.rebind(f"session-{name}", path)
    session_registry.drop("session-b")

    _restart()

    assert session_registry.get("session-a").source_path == dirs["a"]
    assert session_registry.get("session-b") is None
    assert session_registry.get("session-c").source_path == dirs["c"]


def test_the_first_operation_after_a_restart_may_be_a_write(store, tmp_path):
    """After a restart nothing has been read yet. A bind or a drop that is the FIRST thing
    the new process does must load the store before writing, or it would replace every
    other session's binding with just its own."""
    a, b, c = (tmp_path / n for n in "abc")
    for path in (a, b, c):
        path.mkdir()
    session_registry.rebind("session-a", a)
    session_registry.rebind("session-b", b)

    _restart()
    session_registry.rebind("session-c", c)  # the new process's very first operation
    _restart()
    assert {k: session_registry.get(k).source_path for k in ("session-a", "session-b", "session-c")} \
        == {"session-a": a, "session-b": b, "session-c": c}

    _restart()
    session_registry.drop("session-a")  # again, a drop is the very first operation
    _restart()
    assert session_registry.get("session-a") is None
    assert session_registry.get("session-b").source_path == b
    assert session_registry.get("session-c").source_path == c


def test_only_bound_sessions_are_written(store, tmp_path):
    """Unbound sessions — every cookie-less client and every fresh tab — cost no I/O
    and leave no trace on the volume."""
    assert session_registry.get("never-bound") is None
    assert not store.exists()
    session_registry.rebind("session-aaaa", tmp_path)
    assert set(_stored(store)) == {"session-aaaa"}


def test_store_format(store, tmp_path):
    session_registry.rebind("session-aaaa", tmp_path)
    raw = json.loads(store.read_text(encoding="utf-8"))
    assert raw["version"] == session_registry.STORE_VERSION
    record = raw["sessions"]["session-aaaa"]
    assert record["source_path"] == str(tmp_path)
    assert isinstance(record["last_seen"], float)


def test_last_seen_advances_only_when_stale_and_reaches_the_store(store, tmp_path, monkeypatch):
    """`last_seen` is the durable record a future reaper derives "orphaned" from, so it
    must reach the disk — but once per flush interval, not once per request."""
    clock = _fake_clock(monkeypatch)
    session_registry.rebind("session-aaaa", tmp_path)
    assert _stored(store)["session-aaaa"]["last_seen"] == 1_000.0

    clock["t"] += session_registry.LAST_SEEN_FLUSH_INTERVAL_S - 1
    session_registry.get("session-aaaa")  # too fresh: not advanced, nothing scheduled
    _join_flushers()
    assert _stored(store)["session-aaaa"]["last_seen"] == 1_000.0
    assert session_registry.get("session-aaaa").last_seen == 1_000.0

    clock["t"] += 1
    session_registry.get("session-aaaa")  # stale enough: advanced, written by the flusher
    _join_flushers()
    assert _stored(store)["session-aaaa"]["last_seen"] == clock["t"]


def test_last_seen_is_read_back_from_the_store(store, tmp_path, monkeypatch):
    """A restarted process must carry the STORED `last_seen`, not invent "now" for every
    session — otherwise the durable liveness record a reaper reads is meaningless."""
    clock = _fake_clock(monkeypatch)
    session_registry.rebind("session-aaaa", tmp_path)
    clock["t"] += session_registry.LAST_SEEN_FLUSH_INTERVAL_S
    session_registry.get("session-aaaa")  # advanced to 1300 and persisted
    _join_flushers()
    stored = clock["t"]

    _restart()
    monkeypatch.setattr(session_registry, "_now", lambda: clock["t"])
    clock["t"] += 10  # time passes — but less than one interval, so nothing advances
    assert session_registry.get("session-aaaa").last_seen == stored  # the stored value, not "now"


def test_an_advance_touches_only_that_session(store, tmp_path, monkeypatch):
    clock = _fake_clock(monkeypatch)
    session_registry.rebind("session-aaaa", tmp_path)  # last_seen 1000
    clock["t"] = 1_250.0
    session_registry.rebind("session-bbbb", tmp_path)  # last_seen 1250
    clock["t"] = 1_300.0  # aaaa is now a full interval stale; bbbb is not
    session_registry.get("session-aaaa")
    _join_flushers()
    stored = _stored(store)
    assert stored["session-aaaa"]["last_seen"] == 1_300.0
    assert stored["session-bbbb"]["last_seen"] == 1_250.0


def test_bindings_are_never_expired_by_age(store, tmp_path, monkeypatch):
    """Phase 1 records `last_seen` and NOTHING acts on it: reaping a clone before
    `WorkspaceStore.persist()` exists destroys unpushed work (#944)."""
    clock = _fake_clock(monkeypatch)
    session_registry.rebind("session-aaaa", tmp_path)
    clock["t"] += 10 * 365 * 24 * 3600  # ten years idle
    assert session_registry.get("session-aaaa") is not None
    _join_flushers()

    _restart()
    monkeypatch.setattr(session_registry, "_now", lambda: clock["t"])
    assert session_registry.get("session-aaaa").source_path == tmp_path


def test_get_never_writes_on_the_calling_thread(store, tmp_path, monkeypatch):
    """`get` runs on the event-loop thread for every request; a slow volume must not be
    able to freeze it. The write it schedules happens on another thread."""
    clock = _fake_clock(monkeypatch)
    session_registry.rebind("session-aaaa", tmp_path)
    write_started, release, writer = _stall_writes(monkeypatch)

    clock["t"] += session_registry.LAST_SEEN_FLUSH_INTERVAL_S
    try:
        session_registry.get("session-aaaa")  # advances last_seen and schedules a write: returns at once
        assert write_started.wait(5)
        assert writer["thread"] is not threading.current_thread()
        assert session_registry.get("session-aaaa").last_seen == clock["t"]  # advanced in memory already
    finally:
        release.set()
    _join_flushers()
    assert _stored(store)["session-aaaa"]["last_seen"] == clock["t"]


def test_a_stalled_write_does_not_block_other_lookups(store, tmp_path, monkeypatch):
    clock = _fake_clock(monkeypatch)
    session_registry.rebind("session-aaaa", tmp_path)
    session_registry.rebind("session-bbbb", tmp_path)
    write_started, release, _ = _stall_writes(monkeypatch)

    clock["t"] += session_registry.LAST_SEEN_FLUSH_INTERVAL_S
    found = {}
    try:
        session_registry.get("session-aaaa")  # its write now hangs
        assert write_started.wait(5)
        other = threading.Thread(target=lambda: found.update(entry=session_registry.get("session-bbbb")))
        other.start()
        other.join(2)
        assert not other.is_alive(), "a lookup for another session blocked behind a stalled write"
    finally:
        release.set()
    _join_flushers()
    assert found["entry"].source_path == tmp_path


def test_concurrent_binds_lose_nothing(store, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    keys = [f"session-{i:03d}" for i in range(64)]
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda key: session_registry.rebind(key, tmp_path), keys))

    _restart()
    assert all(session_registry.get(key) is not None for key in keys)


def test_an_older_snapshot_never_overwrites_a_newer_one(store, tmp_path, monkeypatch):
    """Writers are serialised and each snapshots only once it holds the writer lock. If the
    first write hangs while a second binding is made, the hung write must not land AFTER
    the second and erase it."""
    real_write = session_registry.atomic_write_text
    first_write_started, release = threading.Event(), threading.Event()
    calls = []

    def write_hanging_the_first(path, text):
        calls.append(text)
        if len(calls) == 1:
            first_write_started.set()
            assert release.wait(5), "the test never released the first write"
        real_write(path, text)

    monkeypatch.setattr(session_registry, "atomic_write_text", write_hanging_the_first)
    first = threading.Thread(target=session_registry.rebind, args=("session-aaaa", tmp_path))
    second = threading.Thread(target=session_registry.rebind, args=("session-bbbb", tmp_path))
    first.start()
    try:
        assert first_write_started.wait(5)
        second.start()
        second.join(0.5)  # it queues behind the hung write (or, if unserialised, overtakes it)
    finally:
        release.set()
    first.join(5)
    second.join(5)
    assert set(_stored(store)) == {"session-aaaa", "session-bbbb"}


UNUSABLE_STORES = {
    "invalid-json": "{ not json",
    "top-level-list": "[]",
    "no-sessions-mapping": '{"version": 1}',
    "another-version": '{"version": 2, "sessions": {"session-zzzz": {"source_path": "/x", "last_seen": 1.0}}}',
}


@pytest.mark.parametrize("content", UNUSABLE_STORES.values(), ids=UNUSABLE_STORES.keys())
def test_a_store_that_cannot_be_understood_is_never_overwritten(store, tmp_path, caplog, content):
    """The store is the only copy of every session's binding. A file this build cannot read
    — corrupt, the wrong shape, or written by another version — is left exactly as it is:
    the registry runs memory-only (as before Phase 1) instead of replacing it."""
    store.parent.mkdir(parents=True)
    store.write_text(content, encoding="utf-8")
    with caplog.at_level("ERROR", logger=session_registry.logger.name):
        for _ in range(3):
            assert session_registry.get("session-aaaa") is None  # no crash on the request path
    logged = [r for r in caplog.records if r.name == session_registry.logger.name]
    assert len(logged) == 1, "the same failure is logged once, not once per request"

    entry = session_registry.rebind("session-aaaa", tmp_path)  # a switch still works...
    assert session_registry.get("session-aaaa") is entry  # ...in memory...
    assert store.read_text(encoding="utf-8") == content  # ...and the file is exactly as it was


def test_an_unreadable_store_is_retried_and_never_overwritten(store, tmp_path, monkeypatch):
    """A transient I/O error while reading must not be remembered as "no bindings": the
    intact file survives, the read is retried — once per retry interval, not once per
    request — and a binding made in the meantime is kept and written once the volume
    answers again."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    session_registry.rebind("session-aaaa", a)
    before = store.read_bytes()
    _restart()
    clock = _fake_clock(monkeypatch)

    real_read_text = Path.read_text
    volume = {"broken": True, "reads": 0}

    def flaky_read_text(self, *args, **kwargs):
        if self.name == session_registry.STORE_FILENAME:
            volume["reads"] += 1
            if volume["broken"]:
                raise OSError(errno.EIO, "Input/output error")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    assert session_registry.get("session-aaaa") is None  # unreadable: an ordinary unbound lookup
    session_registry.rebind("session-bbbb", b)  # a switch made during the outage...
    assert store.read_bytes() == before  # ...does not overwrite the intact file
    for _ in range(5):
        session_registry.get("session-aaaa")
    assert volume["reads"] == 1  # a broken volume is read once per interval, not once per request

    volume["broken"] = False  # the volume recovers...
    assert session_registry.get("session-aaaa") is None  # ...but the retry is not due yet
    clock["t"] += session_registry.STORE_RETRY_INTERVAL_S
    assert session_registry.get("session-aaaa").source_path == a  # due: read again, finds the binding
    _join_flushers()
    session_registry.flush()

    _restart()
    assert session_registry.get("session-aaaa").source_path == a
    assert session_registry.get("session-bbbb").source_path == b  # the outage-time binding was kept


def test_records_this_version_cannot_parse_are_preserved(store, tmp_path):
    """A rewrite must not delete what it did not understand: a hand-edited entry, or a
    field a newer build added. They are never served, but they stay in the file."""
    good = tmp_path / "good"; good.mkdir()
    other = tmp_path / "other"; other.mkdir()
    store.parent.mkdir(parents=True)
    store.write_text(
        '{"version": 1, "sessions": {'
        f'"good": {{"source_path": "{good}", "last_seen": 5.0}},'
        '"no-path": {"last_seen": 5.0},'
        '"no-time": {"source_path": "/abs"},'
        '"negative-time": {"source_path": "/abs", "last_seen": -1.0},'
        '"bad-time": {"source_path": "/abs", "last_seen": "yesterday"},'
        '"relative": {"source_path": "some/where", "last_seen": 5.0},'
        '"empty-path": {"source_path": "", "last_seen": 5.0},'
        '"not-finite": {"source_path": "/abs", "last_seen": NaN},'
        '"a-string": "surprise"}}', encoding="utf-8")

    assert session_registry.get("good").source_path == good
    for key in ("no-path", "no-time", "negative-time", "bad-time", "relative", "empty-path", "not-finite", "a-string"):
        assert session_registry.get(key) is None  # never served: this version does not trust them

    session_registry.rebind("other", other)  # a rewrite...
    stored = _stored(store)  # ...keeps them
    assert stored["no-path"] == {"last_seen": 5.0}
    assert stored["no-time"] == {"source_path": "/abs"}
    assert stored["negative-time"] == {"source_path": "/abs", "last_seen": -1.0}
    assert stored["bad-time"] == {"source_path": "/abs", "last_seen": "yesterday"}
    assert stored["relative"] == {"source_path": "some/where", "last_seen": 5.0}
    assert stored["empty-path"] == {"source_path": "", "last_seen": 5.0}
    assert stored["a-string"] == "surprise"
    assert "not-finite" in stored
    assert stored["good"]["source_path"] == str(good)
    assert stored["other"]["source_path"] == str(other)


def test_a_missing_store_is_an_empty_registry_without_noise(store, caplog):
    with caplog.at_level("DEBUG", logger=session_registry.logger.name):
        assert session_registry.get("session-aaaa") is None
    assert [r for r in caplog.records if r.name == session_registry.logger.name] == []
    assert not store.exists()


def test_a_failed_write_keeps_the_binding_and_is_retried(store, tmp_path, monkeypatch, caplog):
    """A full or read-only volume must not turn a workspace switch into a 500: the
    binding holds in memory, as it always did, the failure is logged, and the next flush
    writes what is still pending."""
    real_write = session_registry.atomic_write_text

    def full_disk(path, text):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(session_registry, "atomic_write_text", full_disk)
    with caplog.at_level("WARNING", logger=session_registry.logger.name):
        entry = session_registry.rebind("session-aaaa", tmp_path)
    assert session_registry.get("session-aaaa") is entry
    assert "could not persist session bindings" in caplog.text
    assert not store.exists()

    monkeypatch.setattr(session_registry, "atomic_write_text", real_write)  # space is freed
    session_registry.flush()
    assert set(_stored(store)) == {"session-aaaa"}


def test_a_directory_squatting_the_store_path_degrades_to_memory_only(store, tmp_path, caplog):
    store.mkdir(parents=True)  # a directory where the file belongs: it can be neither read nor replaced
    with caplog.at_level("ERROR", logger=session_registry.logger.name):
        entry = session_registry.rebind("session-aaaa", tmp_path)
    assert session_registry.get("session-aaaa") is entry
    assert "left untouched" in caplog.text
    assert store.is_dir()


def test_an_unresolvable_build_cache_root_degrades_to_memory_only(tmp_path, monkeypatch, caplog):
    """`Path.home()` raises when HOME is unset and the uid has no passwd entry. Before this
    guard that was a 500 on EVERY request — cookie-less clients included."""
    from vivarium_workbench.lib import remote_build_source

    _restart()  # a fresh module: nothing logged yet

    def no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(remote_build_source, "build_cache_root", no_home)
    with caplog.at_level("ERROR", logger=session_registry.logger.name):
        assert session_registry.get("session-aaaa") is None
        entry = session_registry.rebind("session-aaaa", tmp_path)
    assert session_registry.get("session-aaaa") is entry
    assert "cannot resolve the build-cache root" in caplog.text


def test_clear_also_removes_the_durable_store(store, tmp_path):
    session_registry.rebind("session-aaaa", tmp_path)
    session_registry.clear()
    assert not store.exists()
    _restart()
    assert session_registry.get("session-aaaa") is None


def test_clear_leaves_a_directory_squatting_the_store_path_alone(store):
    store.mkdir(parents=True)
    session_registry.clear()
    assert store.is_dir()


def test_stores_do_not_bleed_into_each_other(tmp_path, monkeypatch):
    """Two build caches are two registries — what keeps every test (and any second
    deployment sharing a process) isolated."""
    cache_a, cache_b = tmp_path / "cache-a", tmp_path / "cache-b"
    monkeypatch.setenv(BUILD_CACHE_ENV, str(cache_a))
    session_registry.clear()
    session_registry.rebind("session-aaaa", tmp_path)

    monkeypatch.setenv(BUILD_CACHE_ENV, str(cache_b))
    assert session_registry.get("session-aaaa") is None
    monkeypatch.setenv(BUILD_CACHE_ENV, str(cache_a))
    assert session_registry.get("session-aaaa") is not None


def test_a_hostile_session_key_is_only_ever_data(store, tmp_path):
    """The key comes straight from a request header; in the store it is a JSON key and
    nothing else, never a path."""
    key = "../../escape"
    session_registry.rebind(key, tmp_path)
    _restart()
    assert session_registry.get(key).source_path == tmp_path
    # Had the key ever been joined onto a path it would have landed outside the build
    # cache: `<cache>/../../escape` is `tmp_path.parent / "escape"`.
    assert not (store.parent / key).exists()
    assert not (tmp_path.parent / "escape").exists()


# ---------------------------------------------------------------------------
# A durable binding can outlive its folder: a session returns after the clone behind it
# was reaped by hand (#944) or its catalog path was removed. It must not get a blank page.
# ---------------------------------------------------------------------------
def test_a_binding_whose_folder_is_gone_serves_the_default_and_is_kept(store, tmp_path, caplog):
    default = tmp_path / "default"; default.mkdir()
    gone = tmp_path / "gone"; gone.mkdir()
    active_workspace.set_workspace_root(default)
    session_registry.rebind("session-aaaa", gone)
    assert workspace_context.resolve("session-aaaa").ws_root == gone  # folder present: routed to it

    shutil.rmtree(gone)  # someone reaps the clone by hand
    _restart()
    with caplog.at_level("WARNING", logger=workspace_context.logger.name):
        for _ in range(3):
            assert workspace_context.resolve("session-aaaa").ws_root == default.resolve()
    assert caplog.text.count("no longer exists") == 1  # once, not once per request
    assert session_registry.get("session-aaaa").source_path == gone  # the binding is kept...
    assert "session-aaaa" in _stored(store)  # ...on disk too: nothing is deleted

    gone.mkdir()  # the folder comes back
    assert workspace_context.resolve("session-aaaa").ws_root == gone  # the same binding routes to it again


def test_a_stale_binding_does_not_blank_the_page_over_http(tmp_path, monkeypatch):
    """End to end, cookie only — a plain page navigation. Before the guard the request was
    routed to the missing folder: a blank page, and a write could re-create a hollow folder
    at that path."""
    from fastapi.testclient import TestClient
    from viva_superpowers import workspace_catalog
    from vivarium_workbench.api.app import create_app
    from vivarium_workbench.lib import _root

    monkeypatch.setenv(BUILD_CACHE_ENV, str(tmp_path / "bc"))
    session_registry.clear()
    default = tmp_path / "default"; default.mkdir()
    (default / "workspace.yaml").write_text("name: the-default\n")
    bound = tmp_path / "bound"; bound.mkdir()
    (bound / "workspace.yaml").write_text("name: the-bound\n")
    _root.set_workspace_root(default)
    monkeypatch.setattr(workspace_catalog, "list_workspaces",
                        lambda: [{"path": str(bound), "name": "b"}])

    client = TestClient(create_app())  # keeps the vw_session cookie the switch aligns
    assert client.post("/api/source/switch", json={"path": str(bound)}).status_code == 200
    assert client.get("/api/workspace").json()["name"] == "the-bound"

    shutil.rmtree(bound)
    _restart()  # the pod restarts; the folder is gone

    response = client.get("/api/workspace")
    assert response.status_code == 200
    assert response.json()["name"] == "the-default"
    assert not bound.exists()  # nothing re-created the hollow folder


def test_switch_binding_survives_a_restart_over_http(tmp_path, monkeypatch):
    """End to end through the real middleware: a tab bound by /api/source/switch is
    still routed to that workspace after the process forgets everything in memory, and
    a session that never switched is untouched. Without the durable store the request
    falls back to the process default."""
    from fastapi.testclient import TestClient
    from viva_superpowers import workspace_catalog
    from vivarium_workbench.api.app import create_app
    from vivarium_workbench.lib import _root

    monkeypatch.setenv(BUILD_CACHE_ENV, str(tmp_path / "bc"))
    session_registry.clear()
    default = tmp_path / "default"; default.mkdir()
    (default / "workspace.yaml").write_text("name: the-default\n")
    bound = tmp_path / "bound"; bound.mkdir()
    (bound / "workspace.yaml").write_text("name: the-bound\n")
    _root.set_workspace_root(default)
    monkeypatch.setattr(workspace_catalog, "list_workspaces",
                        lambda: [{"path": str(bound), "name": "b"}])

    tab = {"X-VW-Session": "tab-that-switched"}
    client = TestClient(create_app())
    assert client.post("/api/source/switch", json={"path": str(bound)},
                       headers=tab).status_code == 200
    assert client.get("/api/workspace", headers=tab).json()["name"] == "the-bound"

    _restart()  # the pod restarts: memory gone, volume intact

    assert client.get("/api/workspace", headers=tab).json()["name"] == "the-bound"
    other = client.get("/api/workspace", headers={"X-VW-Session": "tab-that-never-switched"})
    assert other.json()["name"] == "the-default"


def test_cookie_only_navigation_survives_a_restart_over_http(tmp_path, monkeypatch):
    """The browser-wide `vw_session` cookie — what a reload or a typed URL carries, with no
    per-tab header — still routes to the bound workspace after a restart."""
    from fastapi.testclient import TestClient
    from viva_superpowers import workspace_catalog
    from vivarium_workbench.api.app import create_app
    from vivarium_workbench.lib import _root

    monkeypatch.setenv(BUILD_CACHE_ENV, str(tmp_path / "bc"))
    session_registry.clear()
    default = tmp_path / "default"; default.mkdir()
    (default / "workspace.yaml").write_text("name: the-default\n")
    bound = tmp_path / "bound"; bound.mkdir()
    (bound / "workspace.yaml").write_text("name: the-bound\n")
    _root.set_workspace_root(default)
    monkeypatch.setattr(workspace_catalog, "list_workspaces",
                        lambda: [{"path": str(bound), "name": "b"}])

    client = TestClient(create_app())  # its cookie jar is the session; no header is ever sent
    assert client.post("/api/source/switch", json={"path": str(bound)}).status_code == 200
    assert client.get("/api/workspace").json()["name"] == "the-bound"

    _restart()

    assert client.get("/api/workspace").json()["name"] == "the-bound"


def test_a_binding_survives_a_real_process_restart(store, tmp_path):
    """`importlib.reload` models a restart; this is the real thing — two separate
    interpreters that share nothing but the volume."""
    ws = tmp_path / "ws"; ws.mkdir()
    env = {**os.environ, BUILD_CACHE_ENV: str(store.parent)}
    run = lambda code: subprocess.run([sys.executable, "-c", code], env=env, check=True,
                                      capture_output=True, text=True, timeout=120).stdout.strip()
    run("from vivarium_workbench.lib import session_registry as r; "
        f"r.rebind('session-aaaa', {str(ws)!r}); r.rebind('session-bbbb', {str(ws)!r}); "
        "r.drop('session-bbbb')")
    out = run("from vivarium_workbench.lib import session_registry as r; "
              "print(r.get('session-aaaa').source_path, r.get('session-bbbb'))")
    assert out == f"{ws} None"


# ---------------------------------------------------------------------------
# The read side of the store, and the paths where memory and disk disagree.
# ---------------------------------------------------------------------------
def _unreadable_store(store, content="{ not json"):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(content, encoding="utf-8")


def test_the_request_path_lock_is_not_held_across_the_store_read(store, tmp_path, monkeypatch):
    """`_LOCK` is what every lookup takes on the event-loop thread. A slow read of the store
    (first request after a start, or a retry) must not hold it."""
    session_registry.rebind("session-aaaa", tmp_path)
    _restart()
    read_started, release = threading.Event(), threading.Event()
    real_read = session_registry._read_store

    def stalled_read(path):
        read_started.set()
        assert release.wait(5), "the test never released the stalled read"
        return real_read(path)

    monkeypatch.setattr(session_registry, "_read_store", stalled_read)
    loader = threading.Thread(target=session_registry.get, args=("session-aaaa",))
    loader.start()
    try:
        assert read_started.wait(5)
        assert session_registry._LOCK.acquire(timeout=2), "_LOCK is held while the store is being read"
        session_registry._LOCK.release()
    finally:
        release.set()
    loader.join(5)
    assert session_registry.get("session-aaaa").source_path == tmp_path


def test_bindings_made_while_the_store_is_unreadable_win_and_are_written_once_it_is_readable(
        store, tmp_path, monkeypatch):
    """Memory and disk disagree: this process bound K while the file was unreadable; the file
    is then repaired and holds an older K and another session Z. The newer binding wins,
    Z is picked up, and the merge is written back."""
    clock = _fake_clock(monkeypatch)
    fresh, stale, z = (tmp_path / n for n in ("fresh", "stale", "z"))
    for path in (fresh, stale, z):
        path.mkdir()
    _unreadable_store(store)
    session_registry.rebind("session-kkkk", fresh)  # nothing can be written: the file is left alone
    assert store.read_text(encoding="utf-8") == "{ not json"

    store.write_text(json.dumps({"version": 1, "sessions": {
        "session-kkkk": {"source_path": str(stale), "last_seen": clock["t"]},
        "session-zzzz": {"source_path": str(z), "last_seen": clock["t"]},  # fresh: no advance, so no write is scheduled by a lookup
    }}), encoding="utf-8")
    clock["t"] += session_registry.STORE_RETRY_INTERVAL_S + 1

    assert session_registry.get("session-zzzz").source_path == z
    assert session_registry.get("session-kkkk").source_path == fresh  # memory wins over the older record
    _join_flushers()
    assert _stored(store)["session-kkkk"]["source_path"] == str(fresh)  # and reaches the disk
    assert _stored(store)["session-zzzz"]["source_path"] == str(z)
    _restart()
    assert session_registry.get("session-kkkk").source_path == fresh


def test_an_unreadable_store_is_logged_once_per_outage_not_once_per_retry(store, tmp_path, monkeypatch, caplog):
    clock = _fake_clock(monkeypatch)
    _unreadable_store(store)
    session_registry._log_once.cache_clear()
    with caplog.at_level("ERROR", logger=session_registry.logger.name):
        for _ in range(3):
            session_registry.get("session-aaaa")
            clock["t"] += session_registry.STORE_RETRY_INTERVAL_S + 1  # every get retries the read
    assert caplog.text.count("left untouched") == 1

    store.write_text('{"version": 1, "sessions": {}}', encoding="utf-8")  # repaired...
    session_registry.get("session-aaaa")
    caplog.clear()
    _unreadable_store(store)  # ...and broken again: a new outage is logged again
    session_registry.clear()
    clock["t"] += session_registry.STORE_RETRY_INTERVAL_S + 1
    _unreadable_store(store)
    with caplog.at_level("ERROR", logger=session_registry.logger.name):
        session_registry.get("session-aaaa")
    assert caplog.text.count("left untouched") == 1


def test_rebinding_replaces_a_record_this_version_could_not_parse(store, tmp_path):
    """The unreadable record for a key is superseded by the new binding — it must not come
    back into the file after the binding is dropped."""
    store.parent.mkdir(parents=True)
    store.write_text('{"version": 1, "sessions": {"session-kkkk": "garbage"}}', encoding="utf-8")
    session_registry.rebind("session-kkkk", tmp_path)
    assert _stored(store)["session-kkkk"]["source_path"] == str(tmp_path)
    session_registry.drop("session-kkkk")
    assert "session-kkkk" not in _stored(store)


def test_a_write_that_fails_with_any_error_keeps_the_change_and_is_retried(store, tmp_path, monkeypatch, caplog):
    """Not only OSError: an unexpected exception must not silently drop a change (or kill the
    writer thread with `_DIRTY` already cleared)."""
    real_write = session_registry.atomic_write_text
    monkeypatch.setattr(session_registry, "atomic_write_text",
                        lambda path, text: (_ for _ in ()).throw(ValueError("embedded NUL")))
    with caplog.at_level("WARNING", logger=session_registry.logger.name):
        session_registry.rebind("session-aaaa", tmp_path)
    assert "could not persist" in caplog.text
    monkeypatch.setattr(session_registry, "atomic_write_text", real_write)
    session_registry.flush()  # the retry
    assert _stored(store)["session-aaaa"]["source_path"] == str(tmp_path)


def test_get_survives_a_writer_thread_that_cannot_be_started(store, tmp_path, monkeypatch, caplog):
    """Under thread exhaustion `Thread.start()` raises; a plain read must not become a 500,
    and the change stays marked unsaved for the next flush."""
    clock = _fake_clock(monkeypatch)
    session_registry.rebind("session-aaaa", tmp_path)

    class _NoThreads:
        class Thread:
            def __init__(self, *a, **kw): pass
            def start(self): raise RuntimeError("can't start new thread")

    monkeypatch.setattr(session_registry, "threading", _NoThreads)
    clock["t"] += session_registry.LAST_SEEN_FLUSH_INTERVAL_S
    with caplog.at_level("WARNING", logger=session_registry.logger.name):
        assert session_registry.get("session-aaaa") is not None  # no exception
    assert "could not start" in caplog.text
    monkeypatch.setattr(session_registry, "threading", threading)  # threads work again
    session_registry.flush()
    assert _stored(store)["session-aaaa"]["last_seen"] == 1_000.0 + session_registry.LAST_SEEN_FLUSH_INTERVAL_S


def test_a_bound_path_that_is_a_file_is_not_a_workspace(store, tmp_path):
    default = tmp_path / "default"; default.mkdir()
    active_workspace.set_workspace_root(default)
    a_file = tmp_path / "a-file"; a_file.write_text("not a folder")
    session_registry.rebind("session-aaaa", a_file)
    assert workspace_context.resolve("session-aaaa").ws_root == default.resolve()


def test_an_unstattable_bound_path_serves_the_default_never_a_500(store, tmp_path, monkeypatch):
    default = tmp_path / "default"; default.mkdir()
    active_workspace.set_workspace_root(default)
    bad = tmp_path / "no-access"; bad.mkdir()
    session_registry.rebind("session-aaaa", bad)
    real_is_dir = Path.is_dir

    def is_dir(self):
        if self == bad:
            raise PermissionError(errno.EACCES, "denied")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", is_dir)
    assert workspace_context.resolve("session-aaaa").ws_root == default.resolve()


def test_concurrent_first_lookups_read_the_store_once(store, tmp_path, monkeypatch):
    """Two requests arriving before the store is loaded must not both read it: the second
    waits for the first and finds it loaded."""
    session_registry.rebind("session-aaaa", tmp_path)
    _restart()
    release, reads = threading.Event(), []
    real_read = session_registry._read_store

    def counted_read(path):
        reads.append(path)
        assert release.wait(5), "the test never released the stalled read"
        return real_read(path)

    monkeypatch.setattr(session_registry, "_read_store", counted_read)
    found = []
    threads = [threading.Thread(target=lambda: found.append(session_registry.get("session-aaaa")))
               for _ in range(2)]
    for thread in threads:
        thread.start()
    threads[1].join(0.5)  # long enough for an unserialised second reader to have started its read
    release.set()
    for thread in threads:
        thread.join(5)
    assert len(reads) == 1
    assert all(entry is not None and entry.source_path == tmp_path for entry in found)
