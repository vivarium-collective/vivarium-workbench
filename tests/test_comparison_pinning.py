"""Tests for lib/comparison_pinning.py — per-comparison two-repo build pinning
(dual-engine W4, workbench side; spec §5.6 Q4).

A fake SmsApiClient stands in for viva-api. Covers: ref-as-commit-prefix vs
ref-as-branch resolution, the no-match error naming repo@ref, the
resolve-BOTH-then-verify-BOTH discipline (a not-ready reference blocks the pair
BEFORE anything could be submitted, and the error names the role), and the
manifest passthrough (a resolved declared_environment lands commit+simulator_id
in the `declared` entry; unresolved stays null).
"""
from __future__ import annotations

import pytest

from vivarium_workbench.lib import comparison_pinning as cp
from vivarium_workbench.lib.remote_pinned import NoPinnedBuildError


class FakeClient:
    def __init__(self, versions, statuses=None):
        self._versions = versions
        self._statuses = statuses or {}
        self.status_calls: list[int] = []

    def list_simulators(self, branch_lookup=False):
        return {"versions": self._versions}

    def list_branch_builds(self, repo_url, branch):
        return {"versions": self._versions}

    def simulator_status(self, simulator_id):
        self.status_calls.append(int(simulator_id))
        return {"status": self._statuses.get(int(simulator_id), "completed")}


V2 = {"git_repo_url": "https://github.com/CovertLabEcoli/sms-ecoli",
      "git_branch": "main", "git_commit_hash": "aaa1111222233334444",
      "database_id": 10, "created_at": "2026-08-18T00:00:00"}
V2_NEWER = {**V2, "git_commit_hash": "bbb5555666677778888",
            "database_id": 11, "created_at": "2026-08-19T00:00:00"}
VE = {"git_repo_url": "https://github.com/CovertLabEcoli/vEcoli-private.git",
      "git_branch": "master", "git_commit_hash": "1d80baa99990000aaaa",
      "database_id": 67, "created_at": "2026-08-12T00:00:00"}


class TestResolveEnvironmentBuild:
    def test_ref_as_commit_prefix(self):
        client = FakeClient([V2, V2_NEWER, VE])
        r = cp.resolve_environment_build(
            client, {"repo": "CovertLabEcoli/vEcoli-private", "ref": "1d80baa"})
        # .git-suffix + case normalization handled; commit prefix matched
        assert r["simulator_id"] == 67
        assert r["commit"] == "1d80baa99990000aaaa"

    def test_ref_as_branch_newest_wins(self):
        client = FakeClient([V2, V2_NEWER, VE])
        r = cp.resolve_environment_build(
            client, {"repo": "https://github.com/CovertLabEcoli/sms-ecoli", "ref": "main"})
        assert r["simulator_id"] == 11  # newest created_at

    def test_branchlike_ref_never_matches_commits(self):
        # "main" is not sha-shaped; a repo with no such branch -> no match,
        # even though commit hashes exist.
        client = FakeClient([VE])
        with pytest.raises(NoPinnedBuildError, match="vEcoli-private@main"):
            cp.resolve_environment_build(
                client, {"repo": "CovertLabEcoli/vEcoli-private", "ref": "main"})

    def test_no_match_names_repo_and_ref(self):
        client = FakeClient([V2])
        with pytest.raises(NoPinnedBuildError, match="sms-ecoli@deadbeef"):
            cp.resolve_environment_build(
                client, {"repo": "CovertLabEcoli/sms-ecoli", "ref": "deadbeef"})

    def test_missing_fields_raise(self):
        with pytest.raises(ValueError):
            cp.resolve_environment_build(FakeClient([]), {"repo": "x"})


class TestResolveComparisonPair:
    CAND = {"repo": "CovertLabEcoli/sms-ecoli", "ref": "bbb5555"}
    REF = {"repo": "CovertLabEcoli/vEcoli-private", "ref": "1d80baa"}

    def test_happy_pair_role_tagged(self):
        client = FakeClient([V2, V2_NEWER, VE])
        pair = cp.resolve_comparison_pair(client, self.CAND, self.REF)
        assert pair["candidate"]["role"] == "candidate"
        assert pair["candidate"]["simulator_id"] == 11
        assert pair["reference"]["role"] == "reference"
        assert pair["reference"]["simulator_id"] == 67
        # both were verified
        assert sorted(client.status_calls) == [11, 67]

    def test_not_ready_reference_blocks_pair_and_names_role(self):
        client = FakeClient([V2, V2_NEWER, VE], statuses={67: "running"})
        with pytest.raises(cp.BuildNotReadyError) as ei:
            cp.resolve_comparison_pair(client, self.CAND, self.REF)
        assert ei.value.role == "reference"
        assert ei.value.simulator_id == 67
        assert "before submitting" in str(ei.value)

    def test_resolution_error_surfaces_before_any_verify(self):
        client = FakeClient([V2, V2_NEWER])  # no vEcoli builds at all
        with pytest.raises(NoPinnedBuildError):
            cp.resolve_comparison_pair(client, self.CAND, self.REF)
        assert client.status_calls == []  # never got to verification

    def test_failed_candidate_blocks(self):
        client = FakeClient([V2, V2_NEWER, VE], statuses={11: "failed"})
        with pytest.raises(cp.BuildNotReadyError) as ei:
            cp.resolve_comparison_pair(client, self.CAND, self.REF)
        assert ei.value.role == "candidate"


class TestManifestPassthrough:
    def test_resolved_declaration_lands_in_manifest(self):
        from vivarium_workbench.lib import composite_runs as cr
        m = cr.build_run_manifest(
            spec_id="s", params={}, n_steps=1, emitter="sqlite", emit_paths=[],
            runtime={}, origin="study",
            declared_environment={"repo": "CovertLabEcoli/vEcoli-private",
                                  "ref": "1d80baa",
                                  "commit": "1d80baa99990000aaaa",
                                  "simulator_id": 67})
        d = m["environments"][1]
        assert d["role"] == "declared"
        assert d["commit"] == "1d80baa99990000aaaa"
        assert d["simulator_id"] == 67

    def test_unresolved_declaration_stays_null(self):
        from vivarium_workbench.lib import composite_runs as cr
        m = cr.build_run_manifest(
            spec_id="s", params={}, n_steps=1, emitter="sqlite", emit_paths=[],
            runtime={}, origin="study",
            declared_environment={"repo": "r", "ref": "abc1234"})
        d = m["environments"][1]
        assert d["commit"] is None and d["simulator_id"] is None
