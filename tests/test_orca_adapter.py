"""Tests for tide.adapters.orca_worktree — Orca-native arc execution adapter.

All subprocess calls (orca, gh, git) are mocked — no real Orca app, GitHub,
or git operations are performed.

Coverage:
  (a) orca_available() — True/False routing
  (b) Routing: _cmd_work routes to orca_work vs headless create
  (c) create_orca_worktree assembles the correct CLI command
  (d) orca_work records issue + workspace in passport
  (e) abandon_gate: refuses while issue open; allows when closed; no-op for headless
  (f) orca_land: assembles push + pr + orca-set; raises on no commits ahead
  (g) PR body contains "Closes #N"
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from tests.conftest import build_tide_skeleton
from tide import fields
from tide.arc import stream
from tide.arc.stream import passport_path
from tide.adapters import orca_worktree
from tide.adapters.orca_worktree import (
    ISSUE_FIELD,
    WORKSPACE_FIELD,
    BASE_BRANCH_FIELD,
    AbandonGateError,
    OrcaLandError,
    OrcaWorkError,
    abandon_gate,
    create_orca_worktree,
    orca_land,
    orca_work,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cp(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """Build a fake CompletedProcess."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _orca_create_json(path: str = "/orca/workspaces/proj/arc-test") -> str:
    return json.dumps({"path": path})


# ---------------------------------------------------------------------------
# (a) orca_available
# ---------------------------------------------------------------------------

class TestOrcaAvailable:
    def test_returns_true_when_running(self, monkeypatch):
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_orca",
            lambda args, **kw: _cp(json.dumps({"app": {"running": True}, "runtime": {"reachable": True}})),
        )
        assert orca_worktree.orca_available() is True

    def test_returns_false_when_app_not_running(self, monkeypatch):
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_orca",
            lambda args, **kw: _cp(json.dumps({"app": {"running": False}, "runtime": {"reachable": False}})),
        )
        assert orca_worktree.orca_available() is False

    def test_returns_false_when_binary_missing(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert orca_worktree.orca_available() is False

    def test_returns_false_on_json_error(self, monkeypatch):
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_orca",
            lambda args, **kw: _cp("not-json"),
        )
        assert orca_worktree.orca_available() is False

    def test_returns_false_when_runtime_not_reachable(self, monkeypatch):
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_orca",
            lambda args, **kw: _cp(json.dumps({"app": {"running": True}, "runtime": {"reachable": False}})),
        )
        assert orca_worktree.orca_available() is False


# ---------------------------------------------------------------------------
# (b) routing: _cmd_work → orca vs headless
# ---------------------------------------------------------------------------

class TestCmdWorkRouting:
    def test_routes_to_orca_when_available(self, monkeypatch, tmp_project):
        """When orca_available() is True, _cmd_work calls orca_work, not create."""
        from tide.arc.worktree import _cmd_work

        arc = stream.new_arc(tmp_project, "orca-routed")
        orca_calls: List = []

        monkeypatch.setattr("tide.adapters.orca_worktree.orca_available", lambda: True)
        monkeypatch.setattr(
            "tide.adapters.orca_worktree.orca_work",
            lambda root, arc_dir: orca_calls.append((root, arc_dir)) or "/workspace/arc-orca-routed",
        )
        monkeypatch.setattr("tide.arc.worktree.paths.require_tide_root", lambda: tmp_project)

        args = argparse.Namespace(slug="orca-routed", goal=None)
        result = _cmd_work(args)

        assert result == 0
        assert len(orca_calls) == 1, "orca_work must be called exactly once"
        assert orca_calls[0][0] == tmp_project

    def test_falls_back_headless_when_orca_unavailable(self, monkeypatch, tmp_project):
        """When orca_available() is False, _cmd_work falls back to raw-git create.

        For a non-git project create() returns None → 'worktree isolation skipped'.
        The arc must NOT have an orca-issue field (orca_work was not called).
        """
        from tide.arc.worktree import _cmd_work

        arc = stream.new_arc(tmp_project, "headless-route")

        monkeypatch.setattr("tide.adapters.orca_worktree.orca_available", lambda: False)
        monkeypatch.setattr("tide.arc.worktree.paths.require_tide_root", lambda: tmp_project)

        args = argparse.Namespace(slug="headless-route", goal=None)
        result = _cmd_work(args)

        assert result == 0
        # No orca-issue field must be set in the passport.
        issue = fields.read_field(passport_path(arc), ISSUE_FIELD)
        assert not issue, "orca_work must NOT have been called on the headless path"

    def test_orca_error_returns_nonzero(self, monkeypatch, tmp_project):
        """If orca_work raises OrcaWorkError, _cmd_work returns 1."""
        from tide.arc.worktree import _cmd_work

        stream.new_arc(tmp_project, "fail-arc")

        monkeypatch.setattr("tide.adapters.orca_worktree.orca_available", lambda: True)
        monkeypatch.setattr(
            "tide.adapters.orca_worktree.orca_work",
            lambda root, arc_dir: (_ for _ in ()).throw(OrcaWorkError("gh unavailable")),
        )
        monkeypatch.setattr("tide.arc.worktree.paths.require_tide_root", lambda: tmp_project)

        args = argparse.Namespace(slug="fail-arc", goal=None)
        result = _cmd_work(args)
        assert result == 1


# ---------------------------------------------------------------------------
# (c) create_orca_worktree — command assembly
# ---------------------------------------------------------------------------

class TestCreateOrcaWorktree:
    def test_assembles_correct_command(self, monkeypatch, tmp_project):
        arc = stream.new_arc(tmp_project, "my-feat")
        captured: List[list] = []

        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_orca",
            lambda args, **kw: captured.append(list(args)) or _cp(_orca_create_json("/ws/arc-my-feat")),
        )

        path = create_orca_worktree(tmp_project, arc, "42", "main")

        assert captured, "orca must be invoked"
        cmd = captured[0]
        assert cmd[:2] == ["worktree", "create"], "command prefix must be 'worktree create'"
        assert "--repo" in cmd
        assert "path:{0}".format(tmp_project) in cmd, "must pass the project root as repo path:"
        assert "--name" in cmd
        assert cmd[cmd.index("--name") + 1] == "arc-my-feat", "name must be arc-<slug>"
        assert "--base-branch" in cmd
        assert cmd[cmd.index("--base-branch") + 1] == "main"
        assert "--issue" in cmd
        assert cmd[cmd.index("--issue") + 1] == "42"
        assert "--agent" in cmd
        assert cmd[cmd.index("--agent") + 1] == "claude"
        assert "--activate" in cmd
        assert "--json" in cmd
        assert path == "/ws/arc-my-feat"

    def test_workspace_path_from_json(self, monkeypatch, tmp_project):
        arc = stream.new_arc(tmp_project, "path-test")
        expected = "/orca/workspaces/proj/arc-path-test"
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_orca",
            lambda args, **kw: _cp(_orca_create_json(expected)),
        )
        assert create_orca_worktree(tmp_project, arc, "1", "main") == expected

    def test_name_uses_arc_slug_hyphen(self, monkeypatch, tmp_project):
        """The worktree name must use 'arc-<slug>' with a hyphen (not 'arc/<slug>')."""
        arc = stream.new_arc(tmp_project, "no-slash-please")
        captured: List[list] = []
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_orca",
            lambda args, **kw: captured.append(list(args)) or _cp(_orca_create_json()),
        )
        create_orca_worktree(tmp_project, arc, "9", "main")
        cmd = captured[0]
        name = cmd[cmd.index("--name") + 1]
        assert "/" not in name, "orca worktree name must not contain slashes"
        assert name == "arc-no-slash-please"


# ---------------------------------------------------------------------------
# (d) orca_work — passport recording
# ---------------------------------------------------------------------------

class TestOrcaWork:
    def _patch_externals(self, monkeypatch, tmp_project, arc, *, issue_url: str = "https://github.com/org/repo/issues/77"):
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_git",
            lambda root, args, **kw: _cp("main"),
        )
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_gh",
            lambda args, **kw: _cp(issue_url),
        )
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_orca",
            lambda args, **kw: _cp(_orca_create_json("/ws/arc-recorded")),
        )

    def test_records_issue_in_passport(self, monkeypatch, tmp_project):
        arc = stream.new_arc(tmp_project, "recorded")
        self._patch_externals(monkeypatch, tmp_project, arc, issue_url="https://github.com/o/r/issues/77")
        orca_work(tmp_project, arc)
        assert fields.read_field(passport_path(arc), ISSUE_FIELD) == "77"

    def test_records_workspace_in_passport(self, monkeypatch, tmp_project):
        arc = stream.new_arc(tmp_project, "ws-recorded")
        self._patch_externals(monkeypatch, tmp_project, arc, issue_url="https://github.com/o/r/issues/7")
        orca_work(tmp_project, arc)
        assert fields.read_field(passport_path(arc), WORKSPACE_FIELD) == "/ws/arc-recorded"

    def test_records_base_branch_in_passport(self, monkeypatch, tmp_project):
        arc = stream.new_arc(tmp_project, "base-recorded")
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_git",
            lambda root, args, **kw: _cp("feature/main"),
        )
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_gh",
            lambda args, **kw: _cp("https://github.com/o/r/issues/5"),
        )
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_orca",
            lambda args, **kw: _cp(_orca_create_json()),
        )
        orca_work(tmp_project, arc)
        assert fields.read_field(passport_path(arc), BASE_BRANCH_FIELD) == "feature/main"

    def test_issue_create_uses_label_tide_arc(self, monkeypatch, tmp_project):
        arc = stream.new_arc(tmp_project, "label-check")
        gh_calls: List[list] = []
        monkeypatch.setattr("tide.adapters.orca_worktree._run_git", lambda root, args, **kw: _cp("main"))
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_gh",
            lambda args, **kw: gh_calls.append(list(args)) or _cp("https://github.com/o/r/issues/1"),
        )
        monkeypatch.setattr("tide.adapters.orca_worktree._run_orca", lambda args, **kw: _cp(_orca_create_json()))
        orca_work(tmp_project, arc)
        issue_create_call = gh_calls[0]
        assert "--label" in issue_create_call
        assert "tide-arc" in issue_create_call


# ---------------------------------------------------------------------------
# (e) abandon_gate
# ---------------------------------------------------------------------------

class TestAbandonGate:
    def _set_issue(self, arc: Path, num: str) -> None:
        fields.set_field(passport_path(arc), ISSUE_FIELD, num)

    def test_refuses_open_issue(self, monkeypatch, tmp_project):
        """abandon_gate raises AbandonGateError when the issue is still OPEN."""
        arc = stream.new_arc(tmp_project, "gated-open")
        self._set_issue(arc, "99")
        monkeypatch.setattr("tide.adapters.orca_worktree.issue_state", lambda n: "OPEN")
        with pytest.raises(AbandonGateError, match="99"):
            abandon_gate(arc)

    def test_error_message_contains_issue_number(self, monkeypatch, tmp_project):
        arc = stream.new_arc(tmp_project, "gated-msg")
        self._set_issue(arc, "123")
        monkeypatch.setattr("tide.adapters.orca_worktree.issue_state", lambda n: "OPEN")
        with pytest.raises(AbandonGateError) as exc:
            abandon_gate(arc)
        assert "123" in str(exc.value)
        assert "OPEN" in str(exc.value)

    def test_allows_closed_issue(self, monkeypatch, tmp_project):
        """abandon_gate is silent when the issue is CLOSED."""
        arc = stream.new_arc(tmp_project, "gated-closed")
        self._set_issue(arc, "55")
        monkeypatch.setattr("tide.adapters.orca_worktree.issue_state", lambda n: "CLOSED")
        abandon_gate(arc)  # must not raise

    def test_no_issue_is_no_op(self, tmp_project):
        """abandon_gate is silent for arcs without an orca-issue (headless path)."""
        arc = stream.new_arc(tmp_project, "headless-no-gate")
        abandon_gate(arc)  # must not raise — no ISSUE_FIELD set

    def test_gate_no_op_on_gh_failure(self, monkeypatch, tmp_project):
        """If gh call raises (network unavailable), abandon_gate silently allows."""
        arc = stream.new_arc(tmp_project, "net-fail")
        self._set_issue(arc, "7")
        monkeypatch.setattr(
            "tide.adapters.orca_worktree.issue_state",
            lambda n: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "gh")),
        )
        abandon_gate(arc)  # must not raise

    def test_abandon_gate_blocks_cmd_close(self, monkeypatch, tmp_project):
        """_cmd_close returns 1 when abandon_gate blocks (issue still open)."""
        from tide.arc.stream import _cmd_close
        arc = stream.new_arc(tmp_project, "blocked-close")
        # Prepare passport so the output guard won't also block us.
        out = arc / "output"
        (out / "result.md").write_text("done\n")
        from tests.conftest import strip_placeholders
        strip_placeholders(passport_path(arc))
        # Set issue field.
        self._set_issue(arc, "42")
        monkeypatch.setattr("tide.adapters.orca_worktree.issue_state", lambda n: "OPEN")
        monkeypatch.setattr("tide.arc.stream.paths.require_tide_root", lambda: tmp_project)
        args = argparse.Namespace(slug="blocked-close", goal=None, force=False)
        result = _cmd_close(args)
        assert result == 1  # must be refused

    def test_abandon_gate_allows_cmd_close_when_issue_closed(self, monkeypatch, tmp_project):
        """_cmd_close succeeds when the issue is CLOSED."""
        from tide.arc.stream import _cmd_close
        arc = stream.new_arc(tmp_project, "allowed-close")
        out = arc / "output"
        (out / "result.md").write_text("done\n")
        from tests.conftest import strip_placeholders
        strip_placeholders(passport_path(arc))
        self._set_issue(arc, "42")
        monkeypatch.setattr("tide.adapters.orca_worktree.issue_state", lambda n: "CLOSED")
        monkeypatch.setattr("tide.arc.stream.paths.require_tide_root", lambda: tmp_project)
        args = argparse.Namespace(slug="allowed-close", goal=None, force=False)
        result = _cmd_close(args)
        assert result == 0


# ---------------------------------------------------------------------------
# (f) orca_land — gh-first command assembly
# ---------------------------------------------------------------------------

class TestOrcaLand:
    def _setup_arc(self, tmp_project: Path, slug_str: str, issue: str = "55") -> Path:
        arc = stream.new_arc(tmp_project, slug_str)
        fields.set_field(passport_path(arc), ISSUE_FIELD, issue)
        fields.set_field(passport_path(arc), BASE_BRANCH_FIELD, "main")
        # Branch name in orca style (hyphen).
        branch = "arc-{0}".format(slug_str)
        fields.set_field(passport_path(arc), "worktree-branch", branch)
        return arc

    def _patch_land(self, monkeypatch, *, ahead: bool = True, pr_url: str = "https://github.com/o/r/pull/9"):
        git_calls: List[list] = []
        gh_calls: List[list] = []
        orca_calls: List[list] = []

        def fake_git(root, args, **kw):
            git_calls.append(list(args))
            if "rev-list" in args and "--count" in args:
                return _cp("3" if ahead else "0")
            return _cp("")

        monkeypatch.setattr("tide.adapters.orca_worktree._run_git", fake_git)
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_gh",
            lambda args, **kw: gh_calls.append(list(args)) or _cp(pr_url),
        )
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_orca",
            lambda args, **kw: orca_calls.append(list(args)) or _cp(""),
        )
        return git_calls, gh_calls, orca_calls

    def test_assembles_push_pr_status(self, monkeypatch, tmp_project):
        arc = self._setup_arc(tmp_project, "land-test", issue="55")
        git_calls, gh_calls, orca_calls = self._patch_land(monkeypatch)

        pr_url = orca_land(tmp_project, arc)

        # git push -u origin <branch>
        push = next((c for c in git_calls if "push" in c), None)
        assert push is not None, "git push must be called"
        assert "-u" in push
        assert "origin" in push
        assert "arc-land-test" in push

        # gh pr create
        pr_create = next((c for c in gh_calls if "pr" in c and "create" in c), None)
        assert pr_create is not None, "gh pr create must be called"

        # orca worktree set in-review
        orca_set = next((c for c in orca_calls if "set" in c), None)
        assert orca_set is not None, "orca worktree set must be called"
        assert "in-review" in orca_set

        assert pr_url == "https://github.com/o/r/pull/9"

    def test_pr_body_contains_closes_issue(self, monkeypatch, tmp_project):
        """The PR body must contain 'Closes #N' so GitHub auto-closes the issue."""
        arc = self._setup_arc(tmp_project, "closes-test", issue="55")
        gh_calls: List[list] = []
        monkeypatch.setattr("tide.adapters.orca_worktree._run_git", lambda r, a, **k: _cp("3"))
        monkeypatch.setattr(
            "tide.adapters.orca_worktree._run_gh",
            lambda args, **kw: gh_calls.append(list(args)) or _cp("https://github.com/o/r/pull/1"),
        )
        monkeypatch.setattr("tide.adapters.orca_worktree._run_orca", lambda args, **kw: _cp(""))

        orca_land(tmp_project, arc)

        pr_call = next(c for c in gh_calls if "pr" in c and "create" in c)
        body_idx = pr_call.index("--body") + 1
        assert "Closes #55" in pr_call[body_idx], "PR body must contain 'Closes #N'"

    def test_raises_on_no_commits_ahead(self, monkeypatch, tmp_project):
        """orca_land must raise OrcaLandError when branch has no commits ahead."""
        arc = self._setup_arc(tmp_project, "empty-branch")
        self._patch_land(monkeypatch, ahead=False)
        with pytest.raises(OrcaLandError, match="no commits ahead"):
            orca_land(tmp_project, arc)

    def test_orca_set_uses_branch_prefix(self, monkeypatch, tmp_project):
        """orca worktree set must use 'branch:arc-<slug>' as the worktree selector."""
        arc = self._setup_arc(tmp_project, "branch-selector", issue="7")
        _, _, orca_calls = self._patch_land(monkeypatch)
        orca_land(tmp_project, arc)
        orca_set = next(c for c in orca_calls if "set" in c)
        assert "--worktree" in orca_set
        wt_idx = orca_set.index("--worktree") + 1
        assert orca_set[wt_idx] == "branch:arc-branch-selector"

    def test_cmd_land_uses_orca_when_issue_present(self, monkeypatch, tmp_project):
        """_cmd_land routes to orca_land when ISSUE_FIELD is set in the passport."""
        from tide.arc.worktree import _cmd_land

        arc = self._setup_arc(tmp_project, "cmd-land-orca", issue="33")
        orca_land_calls: List = []

        monkeypatch.setattr(
            "tide.adapters.orca_worktree.orca_land",
            lambda root, arc_dir: orca_land_calls.append((root, arc_dir)) or "https://github.com/pr/1",
        )
        monkeypatch.setattr("tide.arc.worktree.paths.require_tide_root", lambda: tmp_project)

        args = argparse.Namespace(slug="cmd-land-orca", goal=None)
        result = _cmd_land(args)

        assert result == 0
        assert orca_land_calls, "orca_land must be called when issue is set"

    def test_cmd_land_headless_when_no_issue(self, monkeypatch, tmp_project):
        """_cmd_land falls back to raw-git land when no ISSUE_FIELD is set."""
        from tide.arc.worktree import _cmd_land, land

        arc = stream.new_arc(tmp_project, "cmd-land-headless")
        land_calls: List = []

        def fake_land(root, arc_dir, base=None):
            land_calls.append((root, arc_dir))
            from tide.arc.worktree import LandResult
            return LandResult(landed=False, conflict=False, branch="", detail="no worktree to land")

        monkeypatch.setattr("tide.arc.worktree.land", fake_land)
        monkeypatch.setattr("tide.adapters.orca_worktree.orca_available", lambda: False)
        monkeypatch.setattr("tide.arc.worktree.paths.require_tide_root", lambda: tmp_project)

        args = argparse.Namespace(slug="cmd-land-headless", goal=None)
        result = _cmd_land(args)

        assert result == 0
        assert land_calls, "raw-git land must be called when no orca issue is set"
