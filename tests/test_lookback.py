"""отлив watermark (cand 10) — refs/lookback/<reader>/<scope>: status/mark/log."""

from __future__ import annotations

import subprocess

import pytest

from tide import cli, lookback


def _git(root, *argv):
    subprocess.run(["git", *argv], cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def git_project(tmp_project):
    """The tmp .tide/ skeleton committed into a fresh git repo (the trace log)."""
    _git(tmp_project, "init", "-q")
    _git(tmp_project, "config", "user.email", "t@example.com")
    _git(tmp_project, "config", "user.name", "t")
    _git(tmp_project, "add", ".")
    _git(tmp_project, "commit", "-qm", "birth")
    return tmp_project


def _trace_commit(root, name="note"):
    p = root / ".tide" / "arcs" / "{0}.md".format(name)
    p.write_text("trace\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "feat: {0}".format(name))


# --- ref naming --------------------------------------------------------------

def test_ref_name_slugifies_parts():
    assert lookback.ref_name("Agent", "My Project") == "refs/lookback/agent/my-project"


def test_ref_name_rejects_empty_part():
    with pytest.raises(lookback.LookbackError, match="empty reader/scope"):
        lookback.ref_name("agent", "###")


# --- watermark ops -----------------------------------------------------------

def test_no_watermark_yet(git_project):
    ref = lookback.ref_name("agent", "demo")
    assert lookback.current(git_project, ref) is None
    assert lookback.gap(git_project, ref) is None
    assert "no watermark yet" in lookback.render_status(git_project, ref)


def test_mark_then_gap_counts_only_trace_commits(git_project):
    ref = lookback.ref_name("agent", "demo")
    old, new = lookback.mark(git_project, ref)
    assert old is None and len(new) == 40
    assert lookback.gap(git_project, ref) == 0

    _trace_commit(git_project)                       # a .tide/ trace commit
    (git_project / "README.md").write_text("x\n", encoding="utf-8")
    _git(git_project, "add", ".")
    _git(git_project, "commit", "-qm", "chore: outside traces")

    assert lookback.gap(git_project, ref) == 1       # only the .tide/ commit counts
    assert "1 trace commit(s) behind" in lookback.render_status(git_project, ref)


def test_mark_moves_with_cas_and_logs(git_project):
    ref = lookback.ref_name("agent", "demo")
    lookback.mark(git_project, ref)
    _trace_commit(git_project, "second")
    old, new = lookback.mark(git_project, ref)       # CAS against the first mark
    assert old is not None and old != new
    assert lookback.gap(git_project, ref) == 0
    log = lookback.reflog(git_project, ref)
    assert "lookback mark" in log                    # every move is auditable


def test_mark_cas_refuses_when_ref_moved_under_you(git_project, monkeypatch):
    ref = lookback.ref_name("agent", "demo")
    lookback.mark(git_project, ref)
    # Simulate a concurrent mover: mark() reads a STALE current value.
    monkeypatch.setattr(lookback, "current",
                        lambda root, r: "0" * 40)
    with pytest.raises(lookback.LookbackError, match="mark refused"):
        lookback.mark(git_project, ref)


def test_mark_rejects_unresolvable_at(git_project):
    ref = lookback.ref_name("agent", "demo")
    with pytest.raises(lookback.LookbackError, match="cannot resolve"):
        lookback.mark(git_project, ref, at="no-such-rev")


def test_not_a_repo_is_a_clear_error(tmp_project):
    ref = lookback.ref_name("agent", "demo")
    with pytest.raises(lookback.LookbackError, match="not a git repository"):
        lookback.current(tmp_project, ref)


# --- CLI ---------------------------------------------------------------------

def test_cli_status_mark_log_roundtrip(git_project, monkeypatch, capsys):
    monkeypatch.chdir(git_project)

    assert cli.main(["lookback"]) == 0               # bare = status
    assert "no watermark yet" in capsys.readouterr().out

    assert cli.main(["lookback", "mark"]) == 0
    assert "(unset) →" in capsys.readouterr().out

    assert cli.main(["lookback", "status"]) == 0
    assert "up to date" in capsys.readouterr().out

    assert cli.main(["lookback", "log"]) == 0
    assert "lookback mark" in capsys.readouterr().out


def test_cli_scope_defaults_to_project_dirname(git_project, monkeypatch, capsys):
    monkeypatch.chdir(git_project)
    cli.main(["lookback", "mark"])
    out = capsys.readouterr().out
    assert "refs/lookback/agent/" in out
