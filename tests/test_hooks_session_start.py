"""U10 — SessionStart hook: board + role reminder + drift / unmerged warnings."""

from __future__ import annotations

import pytest

from tide import cli, readme
from tide.arc import stream
from tide.canon import store
from tide.hooks import session_start

from tests.conftest import strip_placeholders


def _seed_board(tmp_project, slug, **extra):
    """Write a minimal board.json into an arc's workspace (for board-announce tests)."""
    import json

    arc = stream.new_arc(tmp_project, slug)
    ws = arc / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    board = {"focus": {"limit": 7, "cards": [{"id": "c1", "text": "x"}], "backlog": []}}
    board.update(extra)
    (ws / "board.json").write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
    return arc


def test_render_announces_open_board_with_url(tmp_project):
    _seed_board(tmp_project, "make-board", artifact_url="https://claude.ai/code/artifact/abc")
    text = session_start.render(tmp_project, "orchestrator")
    assert "BOARD" in text
    assert "фокус 1/7" in text
    assert "https://claude.ai/code/artifact/abc" in text


def test_render_announces_board_without_url(tmp_project):
    _seed_board(tmp_project, "make-board")  # no artifact_url yet
    text = session_start.render(tmp_project, "orchestrator")
    assert "BOARD" in text
    assert "01-make-board" in text


def test_render_no_board_section_when_none(tmp_project):
    stream.new_arc(tmp_project, "do-thing")
    text = session_start.render(tmp_project, "orchestrator")
    assert "\nBOARD\n" not in text


def test_render_includes_board_and_role_reminder(tmp_project):
    stream.new_arc(tmp_project, "do-thing")
    text = session_start.render(tmp_project, "orchestrator")
    assert "STREAM" in text
    assert "ORCHESTRATOR" in text


def test_render_worker_role_reminder(tmp_project):
    text = session_start.render(tmp_project, "worker")
    assert "WORKER" in text
    assert "Never merge canon" in text


def test_render_unknown_role_falls_back_to_worker(tmp_project):
    text = session_start.render(tmp_project, "bogus")
    assert "WORKER" in text


def test_render_flags_drift_on_open_arc(tmp_project):
    # Open an arc (stamps current canon-rev), then move CANON.md so it drifts.
    stream.new_arc(tmp_project, "do-thing")
    canon = tmp_project / ".tide" / "canon" / "CANON.md"
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    text = session_start.render(tmp_project, "orchestrator")
    assert "WARNINGS" in text
    assert "drift" in text
    assert "do-thing" in text


def test_render_flags_unmerged_delta(tmp_project):
    arc = stream.new_arc(tmp_project, "alpha")
    (arc / "output" / "r.md").write_text("ok\n", encoding="utf-8")
    strip_placeholders(arc / "arc.md")
    closed = stream.close(tmp_project, "alpha")
    (closed / "delta.md").write_text(
        "# delta — alpha\n\nadded a thing.\n", encoding="utf-8"
    )
    text = session_start.render(tmp_project, "orchestrator")
    assert "WARNINGS" in text
    assert "unmerged delta" in text
    assert "tide canon merge alpha" in text


def test_render_clean_project_has_no_warnings(tmp_project):
    # A truly clean project: open arc (suppresses arc-first) + current README
    # (suppresses readme-drift).  Only these two warnings could fire on a plain
    # new project; both are cleared here so the session opens warning-free.
    stream.new_arc(tmp_project, "do-thing")
    readme.generate(tmp_project)
    text = session_start.render(tmp_project, "orchestrator")
    assert "WARNINGS" not in text


# --- CLI handler -----------------------------------------------------------

def test_cli_session_start_prints_board(tmp_project, monkeypatch, capsys):
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("TIDE_ROLE", "worker")
    stream.new_arc(tmp_project, "do-thing")
    rc = cli.main(["hook", "session-start"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STREAM" in out
    assert "WORKER" in out


def test_cli_session_start_outside_project_is_silent_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no .tide/
    rc = cli.main(["hook", "session-start"])
    assert rc == 0
    assert capsys.readouterr().out == ""


# --- G1 arc-first WARN (advisory; orchestrator-only) ------------------------

def test_arc_first_warning_when_orchestrator_no_arc_no_contract(tmp_project):
    text = session_start.render(tmp_project, "orchestrator")
    assert "WARNINGS" in text
    assert "arc-first" in text


def test_no_arc_first_warning_for_worker(tmp_project):
    text = session_start.render(tmp_project, "worker")
    assert "arc-first" not in text


def test_no_arc_first_warning_when_open_arc(tmp_project):
    stream.new_arc(tmp_project, "do-thing")
    text = session_start.render(tmp_project, "orchestrator")
    assert "arc-first" not in text


def _write_sealed_contract(root, *, state: str) -> None:
    """A CLOSED arc (no open arc) carrying a contract in *state* — anchor probe."""
    arc = root / ".tide" / "arcs" / "__01-sealed__"
    arc.mkdir(parents=True, exist_ok=True)
    (arc / "arc.md").write_text("# 01-sealed\n\nstatus: done\n", encoding="utf-8")
    (arc / "contract.md").write_text(
        "# contract — x\n\nslug: x\nstate: {0}\n".format(state), encoding="utf-8"
    )


def test_no_arc_first_warning_when_signed_contract(tmp_project):
    # A running contract anchors work even with no OPEN arc → no warning.
    _write_sealed_contract(tmp_project, state="running")
    text = session_start.render(tmp_project, "orchestrator")
    assert "arc-first" not in text


def test_draft_contract_does_not_anchor(tmp_project):
    # A draft (unsigned) contract is NOT anchored → the warning still fires.
    _write_sealed_contract(tmp_project, state="draft")
    text = session_start.render(tmp_project, "orchestrator")
    assert "arc-first" in text


# --- readme drift warnings (criterion F) -----------------------------------

def test_render_warns_readme_drift_when_stale(tmp_project):
    """SessionStart includes a readme drift warning when the README is stale/missing."""
    # Open an arc to suppress the arc-first advisory.
    stream.new_arc(tmp_project, "do-thing")
    # README never generated → code 1 → warning expected.
    text = session_start.render(tmp_project, "orchestrator")
    assert "readme: drift" in text
    assert "WARNINGS" in text


def test_render_no_readme_warning_when_current(tmp_project):
    """SessionStart has no readme drift warning when README is up-to-date."""
    readme.generate(tmp_project)
    stream.new_arc(tmp_project, "do-thing")  # suppress arc-first
    text = session_start.render(tmp_project, "orchestrator")
    assert "readme: drift" not in text


def test_readme_drift_warning_silent_on_oracle_error(tmp_path):
    """_readme_drift_warnings returns [] when CANON.md is missing (oracle-error).

    The hook must never raise on infrastructure errors — code 2 stays silent.
    """
    # A path with .tide/ but no CANON.md → check() returns code 2 (oracle-error).
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / ".tide").mkdir()
    warnings = session_start._readme_drift_warnings(bad)
    assert warnings == []


def test_readme_drift_warning_silent_for_nonexistent_path(tmp_path):
    """_readme_drift_warnings returns [] for a totally non-existent path."""
    nonexistent = tmp_path / "no-such-project"
    warnings = session_start._readme_drift_warnings(nonexistent)
    assert warnings == []


# ---------------------------------------------------------------------------
# F4: readme-drift exception emits stderr advisory instead of silent pass
# ---------------------------------------------------------------------------

def test_readme_drift_warning_emits_stderr_advisory_on_exception(
    tmp_project, monkeypatch, capsys
):
    """F4: a failed readme-drift check must emit a stderr advisory, not silently swallow it.

    Before the fix ``except Exception: pass`` dropped real failures with no trace.
    After the fix a warning line is printed to stderr so the degradation is visible.
    The no-raise contract is still preserved (warnings list still returns []).
    """
    import tide.readme as _readme

    def boom(root):
        raise RuntimeError("simulated readme check explosion")

    monkeypatch.setattr(_readme, "check", boom)

    warnings = session_start._readme_drift_warnings(tmp_project)
    assert warnings == []  # no-raise contract preserved

    err = capsys.readouterr().err
    assert "session-start" in err
    assert "readme-drift" in err


# --- Mickey 17 multiple pinch (one orchestrator per thread) -----------------

def test_multiple_warnings_silent_for_none_or_unknown_session(monkeypatch, tmp_path):
    # no session id, and any unknown session, must never warn (fully defensive)
    monkeypatch.setattr(session_start.paths, "control_home", lambda: tmp_path)
    assert session_start._multiple_warnings(None) == []
    assert session_start._multiple_warnings("never-handed-off") == []


def test_multiple_warnings_pinches_a_dissolved_origin(monkeypatch, tmp_path):
    from tide import handoff_queue as hq
    monkeypatch.setattr(session_start.paths, "control_home", lambda: tmp_path)
    hq.offer(tmp_path, "pass-it", arc="t/02", project="p", seed="-", from_session="origin-A")
    hq.take(tmp_path, "pass-it", session="successor-B")
    warns = session_start._multiple_warnings("origin-A")
    assert warns and "MULTIPLE" in warns[0] and "successor-B" in warns[0]
    # the successor is NOT pinched
    assert session_start._multiple_warnings("successor-B") == []


# --- cand 93: link claude-session id at start (not only on first offload) ---

def test_link_claude_session_binds_a_fresh_unclaimed_head(tmp_project):
    from tide import fields

    stream.new_thread(tmp_project, "work", goal="do the work")
    s = stream.new_session(tmp_project, "work", "plan")   # fresh: blank id, offloaded-at 0
    pp = session_start._link_claude_session(tmp_project, "sid-live")
    assert pp == s / "arc.md"
    assert fields.read_field(pp, "claude-session") == "sid-live"


def test_link_claude_session_noop_when_already_linked(tmp_project):
    from tide import fields

    stream.new_thread(tmp_project, "work", goal="do the work")
    s = stream.new_session(tmp_project, "work", "plan")
    fields.set_field(s / "arc.md", "claude-session", "sid-live")
    assert session_start._link_claude_session(tmp_project, "sid-live") is None


def test_link_claude_session_never_overwrites_a_real_head(tmp_project):
    from tide import fields

    stream.new_thread(tmp_project, "work", goal="do the work")
    s = stream.new_session(tmp_project, "work", "plan")
    fields.set_field(s / "arc.md", "claude-session", "someone-else")
    # a different incoming id must NOT clobber an existing real link
    assert session_start._link_claude_session(tmp_project, "sid-live") is None
    assert fields.read_field(s / "arc.md", "claude-session") == "someone-else"


def test_link_claude_session_skips_when_ambiguous(tmp_project):
    from tide import fields

    stream.new_thread(tmp_project, "work", goal="do the work")
    a = stream.new_session(tmp_project, "work", "plan")
    b = stream.new_session(tmp_project, "work", "other")   # two fresh heads → ambiguous
    assert session_start._link_claude_session(tmp_project, "sid-live") is None
    assert not (fields.read_field(a / "arc.md", "claude-session") or "").strip()
    assert not (fields.read_field(b / "arc.md", "claude-session") or "").strip()


def test_link_claude_session_no_session_id_is_noop(tmp_project):
    stream.new_thread(tmp_project, "work", goal="do the work")
    stream.new_session(tmp_project, "work", "plan")
    assert session_start._link_claude_session(tmp_project, None) is None


def test_link_never_binds_a_pickup_target(tmp_project):
    # live 14.07: a passing SessionStart bound a random sid to a session that was
    # WAITING for its handoff launch — the pickup mints its own sid, hands off it
    from tide import fields
    from tide.hooks.session_start import _link_claude_session

    stream.new_thread(tmp_project, "work", goal="do the work")
    sess = stream.new_session(tmp_project, "work", "pickup")
    seed = sess / "input" / "handoff-seed.md"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text("# distil\n", encoding="utf-8")
    assert _link_claude_session(tmp_project, "random-passerby-sid") is None
    assert not (fields.read_field(sess / "arc.md", "claude-session") or "").strip()
