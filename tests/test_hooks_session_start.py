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


def test_orchestrator_reminder_states_the_gate_limits_up_front(tmp_project):
    """The head must open knowing its cage, not learn it one denied command at a time.

    Cand 155: every trip into the role-gate costs a round. The opening line now
    carries the SAME allowed-surface sentence the gate prints when it refuses —
    quoted from ``role_gate.ALLOWED_SURFACE``, so the two can never disagree.
    """
    from tide.hooks import role_gate

    text = session_start.render(tmp_project, "orchestrator")
    assert role_gate.ALLOWED_SURFACE in text
    assert role_gate.ALLOWED_SURFACE in role_gate.DENY_MESSAGE

    # The worker is ungated — spelling out an allowlist there would be a lie.
    assert role_gate.ALLOWED_SURFACE not in session_start.ROLE_REMINDERS["worker"]


def test_cold_entry_stream_hides_closed_threads(tmp_project):
    """Sealed threads are finished history — off the opening breath, on `tide status`.

    Live they were 18 of 23 STREAM rows: every closed thread plus each of its
    sub-arcs, all of them long done.
    """
    from tide.arc import board

    stream.new_arc(tmp_project, "alive")
    buried = stream.new_arc(tmp_project, "buried")
    (buried / "output" / "r.md").write_text("done\n", encoding="utf-8")
    strip_placeholders(buried / "arc.md")
    stream.close(tmp_project, "buried")

    text = session_start.render(tmp_project, "orchestrator")
    assert "01-alive" in text
    assert "__02-buried__" not in text
    # `tide status` still renders the whole stream, closed history included
    assert "__02-buried__" in board.render_board(tmp_project)


def test_render_worker_role_reminder(tmp_project):
    text = session_start.render(tmp_project, "worker")
    assert "WORKER" in text
    assert "Never merge canon" in text


def test_render_unknown_role_falls_back_to_worker(tmp_project):
    text = session_start.render(tmp_project, "bogus")
    assert "WORKER" in text


def test_orchestrator_seed_role_and_hook_oneliner_tell_one_truth():
    """Decision 11 (cold-start): the hook one-liner and the seed ## Role agree.

    A cold orchestrator hears its role twice — once from the SessionStart hook's
    one-liner, once from the shipped ``prompts/orchestrator.md`` embedded as the
    seed's ``## Role``. Those two used to disagree: the hook said "merge canon, sign
    contracts" (06-25 full model) while the prompt said "do NOT dispatch worker
    subagents" (06-29 minimal-mode) — two epochs, incompatible, in one entry. This
    pins the single truth the owner signed:

    * BOTH carry the dispatch mechanism (the role-gate physically forces it).
    * NEITHER carries the stale minimal-mode ban on dispatch.
    * NEITHER tells the head it stamps canon/contracts itself — those are the
      human's signature at the gate.
    """
    from tide.launcher import seed

    one_liner = session_start.ROLE_REMINDERS["orchestrator"]
    role_prompt = seed.read_role_prompt("orchestrator") or ""
    assert role_prompt, "prompts/orchestrator.md must ship"

    # One truth: both say the head dispatches build-work to workers.
    assert "dispatch" in one_liner.lower()
    assert "dispatch" in role_prompt.lower()

    # The stale minimal-mode ban is gone from both.
    for text in (one_liner.lower(), role_prompt.lower()):
        assert "do not dispatch" not in text
        assert "don't dispatch" not in text

    # The head does not stamp canon/contracts on its own (decision 11: the human
    # signs the gate). The old one-liner literally said "merge canon, sign contracts".
    assert "merge canon" not in one_liner.lower()
    assert "sign contracts" not in one_liner.lower()
    assert "merge canon" not in role_prompt.lower()


def test_render_reports_drift_once_in_the_health_footer(tmp_project):
    """Drift is named in ONE place — the HEALTH footer, which aggregates.

    It used to be reported twice: the footer's ``drift: <names>`` line AND a
    per-arc ``⚠ drift: …`` block under WARNINGS right below it (live: 4 of the 5
    WARNINGS lines were that echo). The footer now carries the re-stamp command
    the WARNINGS block used to carry, so nothing actionable was lost.
    """
    # Open an arc (stamps current canon-rev), then move CANON.md so it drifts.
    stream.new_arc(tmp_project, "do-thing")
    canon = tmp_project / ".tide" / "canon" / "CANON.md"
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    text = session_start.render(tmp_project, "orchestrator")
    assert "drift: 01-do-thing" in text          # named, once, in the footer…
    assert "tide arc resume" in text             # …with the fix alongside it
    assert "⚠ drift:" not in text                # and never echoed as a WARNING


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

def _write_canon_content(root) -> None:
    """Fill CANON.md so the project is no longer a NEWBORN (empty skeleton).

    A just-adopted project has blank sections and no README; the readme-drift
    reproach holds its tongue over that (see the newborn tests below). Tests that
    want the reproach must therefore give the project a canon to lag behind.
    """
    canon = root / ".tide" / "canon" / "CANON.md"
    canon.write_text(
        canon.read_text(encoding="utf-8").replace(
            "## What it is\n", "## What it is\n\nA real project with real canon.\n"
        ),
        encoding="utf-8",
    )


def test_render_warns_readme_drift_when_stale(tmp_project):
    """SessionStart includes a readme drift warning when the README is stale/missing."""
    # Open an arc to suppress the arc-first advisory.
    stream.new_arc(tmp_project, "do-thing")
    _write_canon_content(tmp_project)  # not a newborn — the reproach applies
    # README never generated → code 1 → warning expected.
    text = session_start.render(tmp_project, "orchestrator")
    assert "readme: drift" in text
    assert "WARNINGS" in text


# --- newborn silence: no README yet, canon still a blank skeleton -----------

def test_no_readme_reproach_for_a_newborn_project(tmp_project):
    """A freshly-adopted project (blank canon, no README) is NOT scolded.

    ``tide adopt`` writes only the four-heading canon skeleton and no README, so
    "readme: drift — run 'tide readme'" blamed the agent for the absence of
    something that should not exist yet.
    """
    stream.new_arc(tmp_project, "do-thing")  # suppress arc-first
    assert session_start._is_newborn(tmp_project)
    assert session_start._readme_drift_warnings(tmp_project) == []


def test_readme_reproach_returns_once_the_canon_says_something(tmp_project):
    """A MATURE project with real canon and no README is still flagged."""
    _write_canon_content(tmp_project)
    assert not session_start._is_newborn(tmp_project)
    warnings = session_start._readme_drift_warnings(tmp_project)
    assert warnings and "readme: drift" in warnings[0]


def test_an_existing_readme_means_not_newborn(tmp_project):
    """Even on a blank canon, a README that EXISTS puts the project past birth."""
    readme.generate(tmp_project)
    assert not session_start._is_newborn(tmp_project)


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

    _write_canon_content(tmp_project)  # past newborn, so check() is actually reached

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


def test_notes_index_lists_titles_not_bodies(tmp_project):
    # Гриша 17.07: агент видит ИНДЕКС заметок (заголовок+теги), тела не грузятся
    d = tmp_project / ".tide" / "notes"
    d.mkdir(parents=True)
    (d / "01-zaglushka.md").write_text(
        "# Снять заглушку\n\ntags: деплой, прод\n\n"
        "    curl -s https://mite.bot/api/maintenance-status\n",
        encoding="utf-8")
    out = session_start.render(tmp_project, "worker")
    assert "NOTES" in out
    assert "01-zaglushka — Снять заглушку [деплой, прод]" in out
    assert "curl -s" not in out  # тело в индекс не течёт
    # без заметок секции нет
    (d / "01-zaglushka.md").unlink()
    assert "NOTES" not in session_start.render(tmp_project, "worker")


# --- decision injection (cand 128-A) ---------------------------------------

def test_session_decisions_surfaces_the_nits_open_decisions(tmp_project):
    from tide import fields
    from tide.arc import decision

    stream.new_thread(tmp_project, "prz")
    sess = stream.new_session(tmp_project, "prz", "work")
    fields.set_field(sess / "arc.md", "claude-session", "sid-123")
    decision.add_decision(tmp_project, "решили Z", thread_ref="prz", dslug="zed")
    lines = session_start._session_decisions(tmp_project, "sid-123")
    assert any("Решения этой нити" in ln for ln in lines)
    assert any("решили Z" in ln for ln in lines)


def test_session_decisions_silent_without_sid_or_match(tmp_project):
    assert session_start._session_decisions(tmp_project, None) == []
    assert session_start._session_decisions(tmp_project, "ghost-sid") == []


def test_render_injects_open_decisions_below_role(tmp_project):
    from tide import fields
    from tide.arc import decision

    stream.new_thread(tmp_project, "prz")
    sess = stream.new_session(tmp_project, "prz", "work")
    fields.set_field(sess / "arc.md", "claude-session", "sid-xyz")
    decision.add_decision(tmp_project, "решение для рендера", thread_ref="prz", dslug="r")
    out = session_start.render(tmp_project, "orchestrator", session="sid-xyz")
    assert "Решения этой нити" in out and "решение для рендера" in out
