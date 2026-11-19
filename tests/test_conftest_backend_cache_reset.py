"""conftest's autouse ``_forget_what_the_process_learned_about_backends`` must forget every process-level cache keyed
by a backend URL, and the cached GitHub session, between tests.

The cached session is the one that bit: ``test_remote_health``'s ping tests reach ``caller_identity()``, which caches
the developer's real ``gh auth`` login in ``github_auth._CACHED_SESSION``; ``test_backend_interop``'s "not
authenticated" tests then ran signed in (401 became 502/400) -- only on a machine with a ``gh`` login, never in CI.

One test poisons every cache and runs the reset the autouse fixture runs; the other keeps the list honest: a cache
that is renamed or moved would otherwise silently stop being reset.
"""
from __future__ import annotations

import importlib

from conftest import BACKEND_CACHES, forget_backend_caches, forget_the_github_session

from vivarium_workbench.lib import github_auth

#: Listed in BACKEND_CACHES for code that still has it; current code does not.
_GONE = {("vivarium_workbench_sms.sms_api_client", "COMPOSE_API_BASES")}


def _present():
    for name, attr in BACKEND_CACHES:
        if (name, attr) in _GONE:
            continue
        yield name, attr, getattr(importlib.import_module(name), attr)


def test_every_listed_cache_still_exists():
    for name, attr in BACKEND_CACHES:
        if (name, attr) in _GONE:
            continue
        module = importlib.import_module(name)
        assert hasattr(module, attr), f"{name}.{attr} no longer exists: update conftest.BACKEND_CACHES"
        assert hasattr(getattr(module, attr), "clear"), f"{name}.{attr} cannot be cleared"


def test_the_reset_forgets_every_cache_and_the_github_session():
    """One test, not a poison/check pair: under xdist two tests of a file can land on different workers, and the
    check would then pass in a fresh process whatever the reset does."""
    from vivarium_workbench.lib import backend_config

    for _name, attr, cache in _present():
        if attr != "_links":  # a RemoteLink registry: made through link() below, not by hand
            cache["http://127.0.0.1:9"] = object()
    backend_config.link("http://127.0.0.1:9")
    github_auth._set_cached_session(github_auth.Session(login="someone", token="t", source="gh_cli"))
    github_auth._PENDING_FLOWS["flow"] = {}

    forget_backend_caches()
    forget_the_github_session()

    for name, attr, cache in _present():
        assert not cache, f"{name}.{attr} survived the reset: {list(cache)[:3]}"
    assert github_auth._get_cached_session() is None
    assert not github_auth._PENDING_FLOWS
