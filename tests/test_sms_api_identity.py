"""The workbench forwards a signed-in GitHub login as caller identity.

Until now every workbench call to viva-api sent exactly `{"Accept": ...}`, so
every task it submitted landed with `created_by = NULL` -- unowned, and therefore
cancellable by anyone, including a CLI user with no connection to it. Meanwhile
the CLI, TUI and marimo GUI all offer an identity field. The asymmetry was
invisible from the workbench, which is the part worth fixing.

The subtle half, and the reason this is not a one-liner: `current_session()`
resolves a MACHINE credential (`VIVARIUM_WORKBENCH_GH_TOKEN`, a k8s Secret) when
no person is signed in. On a deployed workbench every user resolves to that one
account. Forwarding it would give them all the SAME identity -- so they could
each cancel the others' tasks while the record named a specific owner. That is
worse than anonymous: it looks like attribution and provides none.
"""

from __future__ import annotations

from typing import Any

import pytest

from vivarium_workbench_sms import sms_api_client as sac


class _Session:
    def __init__(self, login: str, source: str) -> None:
        self.login = login
        self.source = source


def _with_session(monkeypatch: pytest.MonkeyPatch, session: Any) -> None:
    from vivarium_workbench.lib import github_auth

    monkeypatch.setattr(github_auth, "current_session", lambda: session)


# --- who counts as a person --------------------------------------------------


@pytest.mark.parametrize("source", ["device_flow", "gh_cli"])
def test_an_interactively_signed_in_user_is_forwarded(monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    _with_session(monkeypatch, _Session("octocat", source))
    assert sac.caller_identity() == "octocat@github"


def test_the_shared_machine_credential_is_NOT_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point. `source="token"` is VIVARIUM_WORKBENCH_GH_TOKEN, which on
    a deployment is one account shared by every user. Anonymous is more honest
    than an owner nobody actually is."""
    _with_session(monkeypatch, _Session("viva-machine-bot", "token"))
    assert sac.caller_identity() is None


def test_nobody_signed_in_is_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_session(monkeypatch, None)
    assert sac.caller_identity() is None


def test_a_blank_login_is_not_an_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """`"@github"` would be a plausible-looking owner belonging to no one."""
    _with_session(monkeypatch, _Session("   ", "gh_cli"))
    assert sac.caller_identity() is None


def test_the_login_is_qualified_so_it_is_not_mistaken_for_an_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare `octocat` sitting next to `you@example.com` in an Owner column
    reads as an email that lost its domain."""
    _with_session(monkeypatch, _Session("octocat", "gh_cli"))
    assert sac.caller_identity() == "octocat@github"


def test_a_broken_auth_lookup_never_fails_the_request_it_decorates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity is a nicety on every path that calls this. An unreachable
    keyring or a slow `gh` must not take down the call it was meant to label."""

    def _boom() -> Any:
        raise RuntimeError("keyring locked")

    from vivarium_workbench.lib import github_auth

    monkeypatch.setattr(github_auth, "current_session", _boom)
    assert sac.caller_identity() is None


# --- what actually goes on the wire ------------------------------------------


def test_the_header_is_present_when_a_person_is_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_session(monkeypatch, _Session("octocat", "device_flow"))
    headers = sac.SmsApiClient()._headers()
    assert headers[sac.IDENTITY_HEADER] == "octocat@github"
    assert headers["Accept"] == "application/json"


def test_no_header_at_all_when_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not an empty value. `X-Auth-Request-Email: ` would make the caller look
    identified-as-nobody rather than unidentified."""
    _with_session(monkeypatch, None)
    assert sac.IDENTITY_HEADER not in sac.SmsApiClient()._headers()


def test_the_accept_type_is_still_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Downloads ask for gzip/zip; identity must not flatten that."""
    _with_session(monkeypatch, _Session("octocat", "gh_cli"))
    assert sac.SmsApiClient()._headers("application/gzip")["Accept"] == "application/gzip"


def test_every_request_in_the_client_goes_through_the_helper() -> None:
    """A hand-built header dict is a call that silently drops identity. There
    were five before this change; none should be left."""
    import inspect

    source = inspect.getsource(sac)
    body = source.split("class SmsApiClient", 1)[1]
    assert '"Accept": "application/json"' not in body, "a request bypasses _headers()"
    assert '"Accept": "application/gzip"' not in body, "a request bypasses _headers()"
    assert '"Accept": "application/zip"' not in body, "a request bypasses _headers()"
