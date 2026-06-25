"""U10 — PreToolUse edit-gate: block edits with no open arc; allow .tide/; skip __…__."""

from __future__ import annotations

import io

import pytest

from tide import cli
from tide.arc import stream
from tide.hooks import edit_gate

from tests.conftest import strip_placeholders


# --- has_open_arc scan -----------------------------------------------------

def test_no_open_arc_when_stream_empty(tmp_project):
    assert edit_gate.has_open_arc(tmp_project) is False


def test_open_arc_detected_at_stream_level(tmp_project):
    stream.new_arc(tmp_project, "do-thing")
    assert edit_gate.has_open_arc(tmp_project) is True


def test_open_subarc_detected_one_level_down(tmp_project):
    stream.new_goal(tmp_project, "big-goal")
    stream.new_arc(tmp_project, "sub-step", goal_slug="big-goal")
    assert edit_gate.has_open_arc(tmp_project) is True


def test_closed_arc_does_not_count_as_open(tmp_project):
    arc = stream.new_arc(tmp_project, "do-thing")
    (arc / "output" / "result.md").write_text("done\n", encoding="utf-8")
    strip_placeholders(arc / "arc.md")
    stream.close(tmp_project, "do-thing")
    assert edit_gate.has_open_arc(tmp_project) is False


def test_closed_arc_with_stale_active_text_never_reopens_gate(tmp_project):
    # The anti-grep-r footgun: a closed workspace file literally says
    # 'status: active' but the gate scans canonical passports + skips __…__.
    arc = stream.new_arc(tmp_project, "do-thing")
    (arc / "output" / "r.md").write_text("ok\n", encoding="utf-8")
    strip_placeholders(arc / "arc.md")
    closed = stream.close(tmp_project, "do-thing")
    (closed / "workspace" / "scratch.md").write_text(
        "old notes\nstatus: active\n", encoding="utf-8"
    )
    assert edit_gate.has_open_arc(tmp_project) is False


# --- decide ----------------------------------------------------------------

def test_decide_blocks_project_edit_with_no_open_arc(tmp_project):
    target = tmp_project / "src" / "app.py"
    code, reason = edit_gate.decide(str(target), tmp_project)
    assert code == edit_gate.BLOCK
    assert "no open arc" in reason


def test_decide_allows_project_edit_with_open_arc(tmp_project):
    stream.new_arc(tmp_project, "do-thing")
    target = tmp_project / "src" / "app.py"
    code, _ = edit_gate.decide(str(target), tmp_project)
    assert code == edit_gate.ALLOW


def test_decide_always_allows_edits_inside_tide(tmp_project):
    # No open arc, yet an edit inside .tide/ is allowed (deltas/reports/cannon).
    target = tmp_project / ".tide" / "arcs" / "01-foo" / "delta.md"
    code, reason = edit_gate.decide(str(target), tmp_project)
    assert code == edit_gate.ALLOW
    assert ".tide/" in reason


def test_decide_allows_outside_any_tide_project(tmp_path):
    # Not opted in (no .tide/ anywhere) → the gate stays out of the way.
    target = tmp_path / "random.py"
    code, _ = edit_gate.decide(str(target), tmp_path)
    assert code == edit_gate.ALLOW


def test_decide_allows_empty_file_path(tmp_project):
    code, _ = edit_gate.decide(None, tmp_project)
    assert code == edit_gate.ALLOW


# --- unmerged-delta barrier ------------------------------------------------

def _make_closed_arc_with_unmerged_delta(root, slug_name="alpha"):
    arc = stream.new_arc(root, slug_name)
    (arc / "output" / "r.md").write_text("ok\n", encoding="utf-8")
    strip_placeholders(arc / "arc.md")
    closed = stream.close(root, slug_name)
    (closed / "delta.md").write_text(
        "# delta — {0}\n\nadded a thing to cannon.\n".format(slug_name),
        encoding="utf-8",
    )
    return closed


def test_decide_blocks_project_edit_while_delta_unmerged(tmp_project):
    # An unmerged delta from a closed arc is the between-arcs barrier: project
    # edits are blocked (and a new arc can't even be opened) until it merges.
    _make_closed_arc_with_unmerged_delta(tmp_project)
    code, reason = edit_gate.decide(str(tmp_project / "x.py"), tmp_project)
    assert code == edit_gate.BLOCK
    assert "unmerged cannon-delta" in reason


def test_decide_still_allows_tide_edits_while_delta_unmerged(tmp_project):
    _make_closed_arc_with_unmerged_delta(tmp_project)
    target = tmp_project / ".tide" / "cannon" / "CANON.md"
    code, _ = edit_gate.decide(str(target), tmp_project)
    assert code == edit_gate.ALLOW


# --- CLI handler (stdin payload) -------------------------------------------

def _run_edit_gate(monkeypatch, payload_json):
    monkeypatch.setattr("sys.stdin", io.StringIO(payload_json))
    return cli.main(["hook", "edit-gate"])


def test_cli_edit_gate_blocks_via_stdin(tmp_project, monkeypatch, capsys):
    monkeypatch.chdir(tmp_project)
    payload = '{"tool_name": "Edit", "tool_input": {"file_path": "%s"}}' % (
        tmp_project / "app.py"
    )
    rc = _run_edit_gate(monkeypatch, payload)
    assert rc == edit_gate.BLOCK
    assert "no open arc" in capsys.readouterr().err


def test_cli_edit_gate_allows_with_open_arc(tmp_project, monkeypatch, capsys):
    monkeypatch.chdir(tmp_project)
    stream.new_arc(tmp_project, "do-thing")
    payload = '{"tool_name": "Write", "tool_input": {"file_path": "%s"}}' % (
        tmp_project / "app.py"
    )
    rc = _run_edit_gate(monkeypatch, payload)
    assert rc == edit_gate.ALLOW


def test_cli_edit_gate_allows_on_garbled_payload(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project)
    rc = _run_edit_gate(monkeypatch, "not json at all")
    assert rc == edit_gate.ALLOW
