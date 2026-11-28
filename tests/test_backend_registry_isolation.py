"""Regression: the provider registry (lib/backends._BY_KIND + the lazily-resolved
backend attr-globals) must not leak across tests. A test that runs with providers
off caches the inert _SENTINELS backend; without an autouse reset a later
run_jobs/SMS test resolves it and fails (_Inert has no attribute 'status_many').
See the reset_registry() call in conftest.forget_backend_caches()."""
import vivarium_workbench.lib.backends as backends


def test_a_providers_off_caches_inert_backend(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_PROVIDERS", "none")
    backends.reset_registry()            # make the 'none' switch take effect
    _ = backends.SMS_LEGACY              # resolve with providers off -> inert sentinel, cached in globals
    assert "SMS_LEGACY" in vars(backends)
    assert backends._BY_KIND is not None


def test_b_next_test_sees_a_clean_registry():
    # The autouse forget-backend-caches fixture must have reset the registry after
    # test_a, so its providers-off inert cache does NOT bleed into this test.
    assert "SMS_LEGACY" not in vars(backends), "inert SMS_LEGACY leaked from the previous test"
    assert backends._BY_KIND is None, "_BY_KIND leaked from the previous test"
