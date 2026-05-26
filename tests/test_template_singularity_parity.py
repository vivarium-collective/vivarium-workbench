"""Verify that pbg-template's Singularity.def.j2 stays in lockstep with Dockerfile.

All tests read the real sibling checkout at ../pbg-template/template/ and are
skipped when that checkout is absent (CI without the sibling still passes).
No real credentials, hostnames, or key material appear anywhere in this file.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture: locate real pbg-template or skip
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_SIBLING_TEMPLATE = _HERE.parents[2] / "pbg-template" / "template"


@pytest.fixture(scope="module")
def pbg_template() -> Path:
    if not (_SIBLING_TEMPLATE / "template-init.sh").is_file():
        pytest.skip("pbg-template sibling not present")
    return _SIBLING_TEMPLATE


@pytest.fixture(scope="module")
def singularity_j2(pbg_template: Path) -> str:
    p = pbg_template / "Singularity.def.j2"
    if not p.is_file():
        pytest.skip("Singularity.def.j2 not present in pbg-template")
    return p.read_text()


@pytest.fixture(scope="module")
def dockerfile(pbg_template: Path) -> str:
    p = pbg_template / "Dockerfile"
    if not p.is_file():
        pytest.skip("Dockerfile not present in pbg-template")
    return p.read_text()


# ---------------------------------------------------------------------------
# Singularity.def.j2 structure
# ---------------------------------------------------------------------------

def test_singularity_has_required_sections(singularity_j2: str) -> None:
    for section in ("%files", "%post", "%environment", "%runscript"):
        assert section in singularity_j2, f"Singularity.def.j2 missing {section}"


def test_singularity_bootstrap_is_docker(singularity_j2: str) -> None:
    assert singularity_j2.lstrip().startswith("Bootstrap: docker"), (
        "Singularity.def.j2 must begin with 'Bootstrap: docker'"
    )


# ---------------------------------------------------------------------------
# Base-image parity
# ---------------------------------------------------------------------------

def _extract_docker_base_image(text: str) -> str:
    """Return the image name from the first FROM line (strips AS alias)."""
    for line in text.splitlines():
        m = re.match(r"^\s*FROM\s+(\S+)(?:\s+AS\s+\S+)?", line, re.IGNORECASE)
        if m:
            return m.group(1)
    raise AssertionError("no FROM line found in Dockerfile")


def _extract_singularity_from_image(text: str) -> str:
    """Return the image name from the 'From:' line."""
    for line in text.splitlines():
        m = re.match(r"^\s*From:\s+(\S+)", line)
        if m:
            return m.group(1)
    raise AssertionError("no 'From:' line found in Singularity.def.j2")


def test_base_image_parity(singularity_j2: str, dockerfile: str) -> None:
    docker_img = _extract_docker_base_image(dockerfile)
    sing_img = _extract_singularity_from_image(singularity_j2)
    assert docker_img == sing_img, (
        f"Base image mismatch: Dockerfile uses '{docker_img}', "
        f"Singularity.def.j2 uses '{sing_img}'. Keep them in lockstep."
    )


# ---------------------------------------------------------------------------
# Runscript / CMD parity
# ---------------------------------------------------------------------------

_PORT = "9863"


def _dockerfile_cmd_tokens(text: str) -> list[str]:
    """Extract CMD tokens from a Dockerfile — handles both JSON-array and
    shell-string form (e.g. CMD ["uv", "run", "serve"] or CMD uv run serve)."""
    import json as _json
    for line in text.splitlines():
        m = re.match(r"^\s*CMD\s+(.*)", line, re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            if raw.startswith("["):
                try:
                    return _json.loads(raw)
                except ValueError:
                    pass
            return raw.split()
    return []


def test_dockerfile_has_serve_cmd(dockerfile: str) -> None:
    tokens = _dockerfile_cmd_tokens(dockerfile)
    assert "vivarium-dashboard" in tokens, (
        "Dockerfile CMD should include 'vivarium-dashboard'"
    )
    assert "serve" in tokens, "Dockerfile CMD should include 'serve'"


def test_singularity_runscript_has_serve_cmd(singularity_j2: str) -> None:
    assert "vivarium-dashboard" in singularity_j2, (
        "Singularity.def.j2 %runscript should include 'vivarium-dashboard'"
    )
    assert "serve" in singularity_j2, (
        "Singularity.def.j2 %runscript should include 'serve'"
    )


def test_port_parity(singularity_j2: str, dockerfile: str) -> None:
    assert _PORT in dockerfile, f"Dockerfile should reference port {_PORT}"
    assert _PORT in singularity_j2, f"Singularity.def.j2 should reference port {_PORT}"


def test_workspace_flag_parity(singularity_j2: str, dockerfile: str) -> None:
    tokens = _dockerfile_cmd_tokens(dockerfile)
    assert "--workspace" in tokens, "Dockerfile CMD should include '--workspace'"
    assert "/app" in tokens, "Dockerfile CMD should include '/app' as workspace path"
    assert "--workspace /app" in singularity_j2, (
        "Singularity.def.j2 %runscript should include '--workspace /app'"
    )


# ---------------------------------------------------------------------------
# hpc.env.example presence
# ---------------------------------------------------------------------------

def test_hpc_env_example_exists(pbg_template: Path) -> None:
    p = pbg_template / ".pbg" / "hpc.env.example"
    assert p.is_file(), (
        ".pbg/hpc.env.example must exist in pbg-template so scaffolded HPC "
        "workspaces provide operators a fill-in-the-blanks config file"
    )


def test_hpc_env_example_has_required_keys(pbg_template: Path) -> None:
    p = pbg_template / ".pbg" / "hpc.env.example"
    if not p.is_file():
        pytest.skip(".pbg/hpc.env.example not present")
    text = p.read_text()
    for key in (
        "SLURM_SUBMIT_HOST",
        "SLURM_SUBMIT_USER",
        "SLURM_SUBMIT_KEY_PATH",
        "SLURM_PARTITION",
        "HPC_REPO_BASE_PATH",
    ):
        assert key in text, f".pbg/hpc.env.example missing required key {key}"


def test_hpc_env_example_has_no_real_values(pbg_template: Path) -> None:
    """All value slots in hpc.env.example must be empty (KEY= with nothing after)."""
    p = pbg_template / ".pbg" / "hpc.env.example"
    if not p.is_file():
        pytest.skip(".pbg/hpc.env.example not present")
    for line in p.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            # Non-sensitive tunables (SINGULARITY_CMD) may have a default.
            sensitive_prefixes = ("SLURM_SUBMIT_HOST", "SLURM_SUBMIT_USER",
                                  "SLURM_SUBMIT_KEY_PATH", "SLURM_SUBMIT_KNOWN_HOSTS",
                                  "SLURM_PARTITION", "SLURM_QOS",
                                  "HPC_IMAGE_BASE_PATH", "HPC_SIM_BASE_PATH",
                                  "HPC_LOG_BASE_PATH", "HPC_REPO_BASE_PATH")
            if any(key.strip() == p for p in sensitive_prefixes):
                assert val.strip() == "", (
                    f"hpc.env.example must not ship a real value for {key.strip()!r}"
                )


# ---------------------------------------------------------------------------
# template-init.sh renders Singularity.def.j2
# ---------------------------------------------------------------------------

def test_singularity_j2_processed_by_template_init(pbg_template: Path) -> None:
    """template-init.sh must include a sed rule for .j2 → rendered output."""
    init = pbg_template / "template-init.sh"
    if not init.is_file():
        pytest.skip("template-init.sh not present")
    text = init.read_text()
    assert "*.j2" in text or ".j2" in text, (
        "template-init.sh must process .j2 files (Singularity.def.j2 → Singularity.def)"
    )
