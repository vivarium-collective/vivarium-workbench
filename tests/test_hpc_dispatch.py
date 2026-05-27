"""Unit tests for vivarium_dashboard.lib.hpc_dispatch.

All tests monkeypatch subprocess.run so no real SSH connection is opened.
No real hostnames, usernames, key paths, or fingerprints appear here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vivarium_dashboard.lib.hpc_dispatch import (
    _base_ssh_opts,
    _mask,
    _socket_path,
    _ssh,
    build_image_script,
    build_run_script,
    cancel_job,
    check_connectivity,
    check_slurm,
    get_job_status,
    open_socket,
    rsync_workspace,
    submit_build_job,
    submit_run_job,
)
from vivarium_dashboard.lib.hpc_settings import HpcNotConfiguredError, HpcSettings


# ---------------------------------------------------------------------------
# Test fixtures — no real values
# ---------------------------------------------------------------------------


def _settings(**overrides) -> HpcSettings:
    base = dict(
        slurm_submit_host="hpc.test.invalid",
        slurm_submit_user="testuser",
        slurm_submit_key_path="/home/testuser/.ssh/id_hpc",
        slurm_submit_known_hosts="/home/testuser/.ssh/hpc_known_hosts",
        slurm_partition="test-partition",
        slurm_qos="test-qos",
        hpc_repo_base_path="/remote/repos",
        hpc_log_base_path="/remote/logs",
        hpc_image_base_path="/remote/images",
        singularity_cmd="apptainer",
        timeout_connect=5,
    )
    base.update(overrides)
    return HpcSettings(**base)


def _ok(stdout: str = "", returncode: int = 0) -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


def _fail(stderr: str = "error", returncode: int = 1) -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stdout = ""
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# _mask
# ---------------------------------------------------------------------------


class TestMask:
    def test_redacts_key_path(self) -> None:
        s = _settings()
        result = _mask(f"error: bad key {s.slurm_submit_key_path}", s)
        assert s.slurm_submit_key_path not in result
        assert "<key_path>" in result

    def test_redacts_username(self) -> None:
        s = _settings()
        result = _mask(f"connection refused for {s.slurm_submit_user}@host", s)
        assert s.slurm_submit_user not in result
        assert "<user>" in result

    def test_does_not_redact_short_username(self) -> None:
        s = _settings(slurm_submit_user="ab")
        result = _mask("connection for ab@host", s)
        assert "ab" in result

    def test_empty_key_path_no_crash(self) -> None:
        s = _settings(slurm_submit_key_path="")
        result = _mask("some text", s)
        assert result == "some text"


# ---------------------------------------------------------------------------
# _base_ssh_opts
# ---------------------------------------------------------------------------


class TestBaseSshOpts:
    def test_includes_connect_timeout(self) -> None:
        s = _settings(timeout_connect=7)
        opts = _base_ssh_opts(s)
        assert "ConnectTimeout=7" in " ".join(opts)

    def test_includes_key_when_set(self) -> None:
        s = _settings(slurm_submit_key_path="/tmp/key")
        opts = _base_ssh_opts(s)
        assert "-i" in opts
        assert "/tmp/key" in opts

    def test_includes_known_hosts_when_set(self) -> None:
        s = _settings(slurm_submit_known_hosts="/tmp/kh")
        opts = _base_ssh_opts(s)
        joined = " ".join(opts)
        assert "UserKnownHostsFile=/tmp/kh" in joined

    def test_omits_key_when_empty(self) -> None:
        s = _settings(slurm_submit_key_path="")
        opts = _base_ssh_opts(s)
        assert "-i" not in opts

    def test_batch_mode_always_present(self) -> None:
        s = _settings()
        opts = _base_ssh_opts(s)
        assert "BatchMode=yes" in " ".join(opts)


# ---------------------------------------------------------------------------
# _socket_path
# ---------------------------------------------------------------------------


class TestSocketPath:
    def test_contains_user_and_host(self) -> None:
        s = _settings()
        p = _socket_path(s)
        assert "testuser" in p.name
        assert "hpc.test.invalid" in p.name

    def test_ends_with_sock(self) -> None:
        s = _settings()
        assert _socket_path(s).suffix == ".sock"

    def test_special_chars_sanitised(self) -> None:
        s = _settings(slurm_submit_host="host:port/path")
        p = _socket_path(s)
        assert ":" not in p.name
        assert "/" not in p.name


# ---------------------------------------------------------------------------
# _ssh — direct connection (socket absent)
# ---------------------------------------------------------------------------


class TestSsh:
    def test_direct_connection_args(self) -> None:
        s = _settings()
        with patch("subprocess.run", return_value=_ok("output")) as mock_run, \
             patch.object(Path, "exists", return_value=False):
            r = _ssh(s, "echo hello")

        assert r.stdout == "output"
        args = mock_run.call_args[0][0]
        assert args[0] == "ssh"
        assert s.slurm_submit_user + "@" + s.slurm_submit_host in args
        assert "echo hello" in args

    def test_uses_control_master_when_socket_alive(self) -> None:
        s = _settings()
        with patch("subprocess.run", return_value=_ok("via-socket")) as mock_run, \
             patch.object(Path, "exists", return_value=True):
            r = _ssh(s, "hostname")

        args = mock_run.call_args[0][0]
        assert "-S" in args

    def test_falls_back_on_stale_socket(self) -> None:
        s = _settings()
        call_count = {"n": 0}

        def _run(args, **kwargs):
            call_count["n"] += 1
            if "-S" in args:
                return _fail(returncode=255)  # stale socket
            return _ok("direct")

        with patch("subprocess.run", side_effect=_run), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "unlink"):
            r = _ssh(s, "whoami")

        assert r.stdout == "direct"
        assert call_count["n"] == 2  # socket attempt + fallback

    def test_raises_on_timeout(self) -> None:
        s = _settings()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 5)), \
             patch.object(Path, "exists", return_value=False):
            with pytest.raises(subprocess.TimeoutExpired):
                _ssh(s, "sleep 10")


# ---------------------------------------------------------------------------
# check_connectivity
# ---------------------------------------------------------------------------


class TestCheckConnectivity:
    def test_reachable_with_apptainer(self) -> None:
        s = _settings()
        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_ok("/usr/bin/apptainer")):
            result = check_connectivity(s)
        assert result["reachable"] is True
        assert result["singularity_available"] is True
        assert result["singularity_cmd"] == "apptainer"

    def test_reachable_singularity_missing(self) -> None:
        s = _settings()
        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_ok("")):
            result = check_connectivity(s)
        assert result["reachable"] is True
        assert result["singularity_available"] is False
        assert result["singularity_cmd"] is None
        assert "not found" in result["message"]

    def test_unreachable(self) -> None:
        s = _settings()
        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   side_effect=Exception("Connection refused")):
            result = check_connectivity(s)
        assert result["reachable"] is False
        assert result["singularity_available"] is False

    def test_ssh_nonzero_exit(self) -> None:
        s = _settings()
        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_fail("Permission denied", returncode=1)):
            result = check_connectivity(s)
        assert result["reachable"] is False

    def test_raises_when_not_configured(self) -> None:
        s = HpcSettings()
        with pytest.raises(HpcNotConfiguredError):
            check_connectivity(s)

    def test_masks_sensitive_values_in_error(self) -> None:
        s = _settings()
        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   side_effect=Exception(
                       f"key {s.slurm_submit_key_path} rejected for {s.slurm_submit_user}"
                   )):
            result = check_connectivity(s)
        assert s.slurm_submit_key_path not in result["message"]
        assert s.slurm_submit_user not in result["message"]


# ---------------------------------------------------------------------------
# check_slurm
# ---------------------------------------------------------------------------


class TestCheckSlurm:
    def test_parses_jobs_and_partitions(self) -> None:
        s = _settings()
        jobs_stdout = "12345 myjob RUNNING None 00:01:23"
        parts_stdout = "general* up 1-00:00:00 8 idle"

        def _mock_ssh(settings, cmd, **kwargs):
            if "squeue" in cmd:
                return _ok(jobs_stdout)
            return _ok(parts_stdout)

        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh", side_effect=_mock_ssh):
            result = check_slurm(s)

        assert len(result["jobs"]) == 1
        assert result["jobs"][0]["job_id"] == "12345"
        assert result["jobs"][0]["state"] == "RUNNING"
        assert "general" in result["partitions"]
        assert result["error"] is None

    def test_returns_error_on_exception(self) -> None:
        s = _settings()
        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   side_effect=Exception("network error")):
            result = check_slurm(s)
        assert result["error"] is not None
        assert result["jobs"] == []
        assert result["partitions"] == []


# ---------------------------------------------------------------------------
# rsync_workspace
# ---------------------------------------------------------------------------


class TestRsyncWorkspace:
    def test_rsync_args_include_excludes(self, tmp_path: Path) -> None:
        s = _settings()
        ws = tmp_path / "my-workspace"
        ws.mkdir()
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            rsync_workspace(s, ws)
        args = mock_run.call_args[0][0]
        assert "rsync" in args
        assert "--exclude=.venv/" in args
        assert "--exclude=.git/" in args
        assert "--exclude=.pbg/runs/" in args
        assert "--exclude=.pbg/state.json" in args

    def test_rsync_destination_contains_user_and_host(self, tmp_path: Path) -> None:
        s = _settings()
        ws = tmp_path / "my-workspace"
        ws.mkdir()
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            rsync_workspace(s, ws)
        args = mock_run.call_args[0][0]
        dest = args[-1]
        assert s.slurm_submit_user in dest
        assert s.slurm_submit_host in dest
        assert s.hpc_repo_base_path in dest
        assert "my-workspace" in dest

    def test_rsync_uses_key_in_ssh_e_opt(self, tmp_path: Path) -> None:
        s = _settings()
        ws = tmp_path / "ws"
        ws.mkdir()
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            rsync_workspace(s, ws)
        args = mock_run.call_args[0][0]
        e_idx = args.index("-e")
        ssh_opt = args[e_idx + 1]
        assert s.slurm_submit_key_path in ssh_opt

    def test_raises_on_rsync_failure(self, tmp_path: Path) -> None:
        s = _settings()
        ws = tmp_path / "ws"
        ws.mkdir()
        with patch("subprocess.run", return_value=_fail("rsync: connection failed", returncode=23)):
            with pytest.raises(RuntimeError, match="rsync failed"):
                rsync_workspace(s, ws)


# ---------------------------------------------------------------------------
# sbatch script content
# ---------------------------------------------------------------------------


class TestBuildRunScript:
    def test_contains_partition(self) -> None:
        s = _settings()
        script = build_run_script(s, "myws", "abc123", "python run.py")
        assert f"--partition={s.slurm_partition}" in script

    def test_contains_qos_when_set(self) -> None:
        s = _settings()
        script = build_run_script(s, "myws", "abc123", "python run.py")
        assert f"--qos={s.slurm_qos}" in script

    def test_omits_qos_when_empty(self) -> None:
        s = _settings(slurm_qos="")
        script = build_run_script(s, "myws", "abc123", "python run.py")
        assert "--qos=" not in script

    def test_apptainer_fallback_line(self) -> None:
        s = _settings(singularity_cmd="apptainer")
        script = build_run_script(s, "myws", "abc123", "python run.py")
        assert "apptainer" in script
        assert "singularity" in script  # fallback also present

    def test_singularity_primary_with_apptainer_fallback(self) -> None:
        s = _settings(singularity_cmd="singularity")
        script = build_run_script(s, "myws", "abc123", "python run.py")
        assert "singularity" in script
        assert "apptainer" in script  # fallback line

    def test_bind_mount_in_script(self) -> None:
        s = _settings()
        script = build_run_script(s, "myws", "abc123", "python run.py")
        assert "-B" in script  # sms-api uses short flag
        assert "/app/results" in script

    def test_resource_defaults_reflected(self) -> None:
        s = _settings()
        script = build_run_script(s, "myws", "abc123", "cmd",
                                  cpus=2, mem_gb=8, time_min=120)
        assert "--cpus-per-task=2" in script
        assert "--mem=8G" in script
        assert "--time=120" in script

    def test_shebang_and_set_euo(self) -> None:
        s = _settings()
        script = build_run_script(s, "myws", "abc123", "cmd")
        assert script.startswith("#!/bin/bash")
        assert "set -e" in script  # mirrors sms-api parca script (set -e, not pipefail)


class TestBuildImageScript:
    def test_contains_partition(self) -> None:
        s = _settings()
        script = build_image_script(s, "myws", "build01")
        assert f"--partition={s.slurm_partition}" in script

    def test_references_singularity_def(self) -> None:
        s = _settings()
        script = build_image_script(s, "myws", "build01")
        assert "Singularity.def" in script

    def test_build_fakeroot_flag(self) -> None:
        s = _settings()
        script = build_image_script(s, "myws", "build01")
        assert "--fakeroot" in script

    def test_sif_output_path_contains_ws_name(self) -> None:
        s = _settings()
        script = build_image_script(s, "myws", "build01")
        assert "myws.sif" in script


# ---------------------------------------------------------------------------
# get_job_status — squeue → scontrol fallback
# ---------------------------------------------------------------------------


class TestGetJobStatus:
    def test_squeue_success(self) -> None:
        s = _settings()
        call_count = {"n": 0}

        def _mock(settings, cmd, **kwargs):
            call_count["n"] += 1
            if "squeue" in cmd:
                # squeue format: "%i %j %T %R %M" → job_id name state reason elapsed
                return _ok("12345 myjob RUNNING None 01:23")
            return _ok()

        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh", side_effect=_mock):
            result = get_job_status(s, 12345)

        assert result["job_id"] == 12345
        assert result["state"] == "RUNNING"
        assert call_count["n"] == 1  # only squeue needed

    def test_scontrol_fallback_when_squeue_empty(self) -> None:
        s = _settings()

        def _mock(settings, cmd, **kwargs):
            if "squeue" in cmd:
                return _ok("")  # not in queue
            # scontrol response
            return _ok(
                "JobId=12345 JobName=test "
                "JobState=COMPLETED Reason=None ExitCode=0:0"
            )

        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh", side_effect=_mock):
            result = get_job_status(s, 12345)

        assert result["state"] == "COMPLETED"

    def test_unknown_when_both_miss(self) -> None:
        s = _settings()
        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_ok("")):
            result = get_job_status(s, 99999)
        assert result["state"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# cancel_job
# ---------------------------------------------------------------------------


class TestCancelJob:
    def test_calls_scancel(self) -> None:
        s = _settings()
        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_ok()) as mock_ssh:
            cancel_job(s, 42)
        cmd = mock_ssh.call_args[0][1]
        assert "scancel" in cmd
        assert "42" in cmd

    def test_raises_on_scancel_failure(self) -> None:
        s = _settings()
        with patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_fail("Invalid job id", returncode=1)):
            with pytest.raises(RuntimeError, match="scancel failed"):
                cancel_job(s, 99999)


# ---------------------------------------------------------------------------
# submit_build_job
# ---------------------------------------------------------------------------


class TestSubmitBuildJob:
    def test_calls_rsync_and_sbatch(self, tmp_path: Path) -> None:
        s = _settings()
        ws = tmp_path / "myws"
        ws.mkdir()
        (ws / "Singularity.def").write_text("Bootstrap: docker\nFrom: python:3.12-slim\n")

        # sbatch --parsable returns a bare integer (mirrors sms-api SlurmService.submit_job)
        with patch("vivarium_dashboard.lib.hpc_dispatch.rsync_workspace") as mock_rsync, \
             patch("vivarium_dashboard.lib.hpc_dispatch._scp_file"), \
             patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_ok("777")):
            result = submit_build_job(s, ws)

        assert mock_rsync.called
        assert result["slurm_job_id"] == 777
        assert "build_id" in result
        assert "log_path" in result

    def test_script_written_to_pbg_hpc(self, tmp_path: Path) -> None:
        s = _settings()
        ws = tmp_path / "myws"
        ws.mkdir()
        (ws / "Singularity.def").write_text("Bootstrap: docker\nFrom: python:3.12-slim\n")

        with patch("vivarium_dashboard.lib.hpc_dispatch.rsync_workspace"), \
             patch("vivarium_dashboard.lib.hpc_dispatch._scp_file"), \
             patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_ok("99")):
            submit_build_job(s, ws)

        hpc_dir = ws / ".pbg" / "hpc"
        scripts = list(hpc_dir.glob("build-*.sbatch"))
        assert len(scripts) == 1

    def test_raises_on_missing_singularity_def(self, tmp_path: Path) -> None:
        s = _settings()
        ws = tmp_path / "myws"
        ws.mkdir()
        # No Singularity.def — should raise before any network call.
        with pytest.raises(RuntimeError, match="Singularity.def"):
            submit_build_job(s, ws)

    def test_raises_on_sbatch_failure(self, tmp_path: Path) -> None:
        s = _settings()
        ws = tmp_path / "myws"
        ws.mkdir()
        (ws / "Singularity.def").write_text("Bootstrap: docker\nFrom: python:3.12-slim\n")

        with patch("vivarium_dashboard.lib.hpc_dispatch.rsync_workspace"), \
             patch("vivarium_dashboard.lib.hpc_dispatch._scp_file"), \
             patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_fail("sbatch: error: invalid partition", returncode=1)):
            with pytest.raises(RuntimeError, match="sbatch failed"):
                submit_build_job(s, ws)


# ---------------------------------------------------------------------------
# submit_run_job
# ---------------------------------------------------------------------------


class TestSubmitRunJob:
    def test_returns_job_id_and_log_path(self, tmp_path: Path) -> None:
        s = _settings()
        ws = tmp_path / "myws"
        ws.mkdir()

        # sbatch --parsable returns a bare integer (mirrors sms-api SlurmService.submit_job)
        with patch("vivarium_dashboard.lib.hpc_dispatch.rsync_workspace"), \
             patch("vivarium_dashboard.lib.hpc_dispatch._scp_file"), \
             patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_ok("555")):
            result = submit_run_job(s, ws, "python run.py", {"cpus": 2, "mem_gb": 8})

        assert result["slurm_job_id"] == 555
        assert "log_path" in result

    def test_script_written_to_pbg_hpc(self, tmp_path: Path) -> None:
        s = _settings()
        ws = tmp_path / "myws"
        ws.mkdir()

        with patch("vivarium_dashboard.lib.hpc_dispatch.rsync_workspace"), \
             patch("vivarium_dashboard.lib.hpc_dispatch._scp_file"), \
             patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_ok("1")):
            submit_run_job(s, ws, "cmd", {})

        scripts = list((ws / ".pbg" / "hpc").glob("run-*.sbatch"))
        assert len(scripts) == 1

    def test_script_content_uses_resources(self, tmp_path: Path) -> None:
        s = _settings()
        ws = tmp_path / "myws"
        ws.mkdir()

        with patch("vivarium_dashboard.lib.hpc_dispatch.rsync_workspace"), \
             patch("vivarium_dashboard.lib.hpc_dispatch._scp_file"), \
             patch("vivarium_dashboard.lib.hpc_dispatch._ssh",
                   return_value=_ok("2")):
            submit_run_job(s, ws, "python run.py",
                           {"cpus": 4, "mem_gb": 16, "time_min": 30})

        script_text = next((ws / ".pbg" / "hpc").glob("run-*.sbatch")).read_text()
        assert "--cpus-per-task=4" in script_text
        assert "--mem=16G" in script_text
        assert "--time=30" in script_text


# ---------------------------------------------------------------------------
# open_socket
# ---------------------------------------------------------------------------


class TestOpenSocket:
    def test_reuses_live_socket(self, tmp_path: Path) -> None:
        s = _settings()
        sock = tmp_path / "test.sock"
        sock.touch()

        check_ok = _ok()

        with patch("vivarium_dashboard.lib.hpc_dispatch._SOCKET_DIR", tmp_path), \
             patch("vivarium_dashboard.lib.hpc_dispatch._socket_path",
                   return_value=sock), \
             patch("subprocess.run", return_value=check_ok) as mock_run:
            result = open_socket(s)

        # Only the "ssh -O check" call should have been made (no new ControlMaster).
        assert result == sock
        args = mock_run.call_args[0][0]
        assert "-O" in args
        assert "check" in args

    def test_opens_new_socket_when_absent(self, tmp_path: Path) -> None:
        s = _settings()

        with patch("vivarium_dashboard.lib.hpc_dispatch._SOCKET_DIR", tmp_path), \
             patch("subprocess.run", return_value=_ok()) as mock_run:
            # Socket file does not exist — should open a new ControlMaster.
            open_socket(s)

        args = mock_run.call_args[0][0]
        assert "-M" in args
        assert "-N" in args
