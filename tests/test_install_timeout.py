"""Unit tests for the configurable catalog-install timeout.

The old hardcoded caps (120s on ``git submodule add``, 180s on ``pip install``)
were the #1 catalog-install failure on slow/proxied networks (the k8s/HeLx
deployment). Installs are now overridable via ``VIVARIUM_WORKBENCH_INSTALL_TIMEOUT``
with a generous default.
"""
import pytest

from vivarium_workbench.lib.catalog_install_views import (
    _INSTALL_TIMEOUT_DEFAULT,
    _install_timeout,
)

ENV = "VIVARIUM_WORKBENCH_INSTALL_TIMEOUT"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    yield


def test_default_when_unset():
    assert _install_timeout() == _INSTALL_TIMEOUT_DEFAULT
    assert _install_timeout(300) == 300


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv(ENV, "1200")
    assert _install_timeout() == 1200
    # the override beats the caller-supplied default too
    assert _install_timeout(300) == 1200


def test_invalid_value_falls_back(monkeypatch):
    monkeypatch.setenv(ENV, "not-a-number")
    assert _install_timeout(300) == 300


def test_nonpositive_value_falls_back(monkeypatch):
    monkeypatch.setenv(ENV, "0")
    assert _install_timeout(300) == 300
    monkeypatch.setenv(ENV, "-5")
    assert _install_timeout(300) == 300


def test_default_is_generous_enough():
    # comfortably above the old 120s/180s caps that were failing
    assert _INSTALL_TIMEOUT_DEFAULT >= 600
