"""#6 hardening: a failed remote run must surface a clean, actionable reason
(mapped from the known failure modes) rather than only a raw Python traceback.
"""
from vivarium_workbench.lib.run_runner import _remote_failure_reason


def test_reason_timeout_hints_run_may_continue():
    r = _remote_failure_reason(TimeoutError("Remote run 7 did not reach a terminal state within 7200s"))
    assert "executing on the deployment" in r


def test_reason_unreachable_points_at_the_backend_url():
    r = _remote_failure_reason(
        RuntimeError("Remote run 7: sms-api status polling failed 6 times in a row … "
                     "Is the sms-api endpoint (http://x:8080) still reachable?")
    )
    # Neutral since step P3 C2 (was "... is the tunnel/endpoint up? (SMS_API_BASE)"): no provider's variable.
    assert r.endswith("  [remote backend not reachable (check the configured backend URL)]")
    assert "SMS_API_BASE" not in r


def test_reason_core_unreachable_text_is_classified():
    r = _remote_failure_reason(
        RuntimeError("GET http://x/version failed (backend unreachable — is the endpoint up?): refused"))
    assert r.endswith("  [remote backend not reachable (check the configured backend URL)]")


def test_reason_unpushed_workspace_tells_user_to_push():
    r = _remote_failure_reason(RuntimeError("HEAD commit abc1234 is not pushed to any remote branch."))
    assert "commit and push" in r


def test_reason_generic_falls_back_to_type_and_message():
    assert _remote_failure_reason(ValueError("boom")) == "ValueError: boom"
