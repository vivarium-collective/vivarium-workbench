"""Static guards on the Dockerfile's slim-image invariants (issue #932).

This file replaces ``test_process_bigraph_pin.py``, whose invariant no longer
exists. That test asserted the Dockerfile's hand-pinned process-bigraph /
bigraph-schema commits stayed in sync with this repo's ``uv.lock`` — pins that
were only ever needed because the image installed the workbench INTO the
v2ecoli venv copied from sms-ecoli's lock, where a re-resolve risked upgrading
substrate packages sms-ecoli was not tested against (backlog item 44 was a live
production crash from exactly that skew).

The image now builds from this repo's own ``uv.lock``, so there is no foreign
lock to skew against and nothing to hand-pin: the floors in ``pyproject.toml``
are simply what gets installed. The version-floor class of bug is gone
structurally rather than guarded.

What needs guarding instead is that the image stays slim. Re-introducing a
workspace package would silently bring back all four consequences #932 removed:
~4.5 GB of GPU/ML stack, a duplicated environment, a GovCloud ECR dependency
with no CI credential, and a build that cannot run on Apple Silicon at all
(polars needs AVX; QEMU does not emulate it).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _instructions() -> list[str]:
    """Dockerfile lines with comments and blanks stripped."""
    return [
        ln for ln in _dockerfile().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def test_image_does_not_pull_a_workspace_image() -> None:
    """No WORKSPACE_IMAGE build-arg or stage.

    This is the dependency that forced a GovCloud ECR pull, for which no GitHub
    Actions credential exists — so its return would re-break CI builds.
    """
    body = "\n".join(_instructions())
    assert "WORKSPACE_IMAGE" not in body, (
        "Dockerfile references WORKSPACE_IMAGE again. The server image must not "
        "pull a workspace/science image (#932): it needs a GovCloud ECR "
        "credential that no workflow has."
    )


def test_build_does_not_import_a_workspace_package() -> None:
    """The build-time sanity check imports only what the SERVER needs.

    ``import v2ecoli`` pulls polars, which requires AVX/AVX2/FMA/BMI. QEMU does
    not emulate those, so this single import is what made the image impossible
    to cross-build from an ARM Mac.
    """
    body = "\n".join(_instructions())
    assert "import v2ecoli" not in body, (
        "Dockerfile imports v2ecoli at build time again (#932). The workspace "
        "package is not in this image and the import needs AVX, which QEMU "
        "cannot emulate — this breaks every Apple Silicon build."
    )


def test_installs_from_this_repos_own_lock() -> None:
    """``uv sync --frozen`` — the lock is authoritative, not a foreign one.

    ``--frozen`` matters: without it a drifted lock silently re-resolves at
    build time, which is how version skew got in before.
    """
    body = "\n".join(_instructions())
    assert re.search(r"uv\s+sync\b", body), (
        "Dockerfile no longer builds the venv with `uv sync` from this repo's lock"
    )
    sync_line = next(ln for ln in _instructions() if re.search(r"uv\s+sync\b", ln))
    assert "--frozen" in sync_line, (
        "`uv sync` must be --frozen so a drifted uv.lock fails the build rather "
        "than silently re-resolving"
    )


def test_requires_each_workspace_to_bring_its_own_venv() -> None:
    """The slim image must not silently host workspace code.

    EnvironmentResolver falls back to ``sys.executable`` when a workspace has no
    ``.venv``. That was harmless when the image happened to contain the full
    science stack; in the slim image it would run analysis code in an
    interpreter that cannot import the workspace package, failing deep inside a
    worker instead of at the seam.
    """
    body = "\n".join(_instructions())
    assert "VIVARIUM_WORKBENCH_REQUIRE_WORKSPACE_VENV=1" in body, (
        "the slim image must set VIVARIUM_WORKBENCH_REQUIRE_WORKSPACE_VENV=1 so "
        "a workspace without its own .venv fails loudly (#932)"
    )


def test_sanity_check_still_imports_api_app() -> None:
    """Preserved from the old pin tests, and still load-bearing.

    A bare ``import vivarium_workbench`` does not eagerly pull in ``api.app``,
    so the item-44 regression shipped straight past a check that omitted it and
    surfaced only as a live CrashLoopBackOff.
    """
    body = _dockerfile()
    assert "import vivarium_workbench.api.app" in body, (
        "the build-time sanity check must import vivarium_workbench.api.app "
        "explicitly — importing the package alone does not exercise it"
    )


def test_hosted_image_does_not_install_the_chat_extra() -> None:
    """The AI chat stays out of hosted workbenches until the chat hardening ships.

    This image is what hosted deployments run. Chat is offered there only once
    the hardening work (#1238, #1247) is merged, released and deployed; that
    release is where the ``[chat]`` extra comes back. Local installs keep chat
    via ``vivarium-workbench[chat]``.
    """
    # Whole instructions, not physical lines: a flag on a `\` continuation line still counts.
    joined = re.sub(r"\\\n", " ", "\n".join(_instructions()))
    installs = [ln for ln in joined.splitlines()
                if re.search(r"\b(uv\s+sync|uv\s+pip\s+install|pip\s+install)\b", ln)]
    assert installs, "Dockerfile has no install step to check"
    for ln in installs:
        assert not re.search(r"--extra[\s=]+chat\b|--all-extras|\[chat\]|pydantic[-_]ai", ln), (
            f"Dockerfile installs the AI chat into the hosted image: {ln.strip()!r}. "
            "Chat stays out of hosted images until the chat hardening (#1238, #1247) "
            "is merged, released and deployed."
        )
