"""U12 unit — launcher.handoff: distil → workspace, remind, fork, (toggle) spawn."""

from __future__ import annotations

import json

import pytest

from tide.arc import candidate, stream
from tide.launcher import handoff


# --- build_summary (pure) --------------------------------------------------

def test_build_summary_header_and_fixed_sections():
    out = handoff.build_summary(
        mode="continue",
        arc_ref="ship-it",
        state="we wired the gate",
        decisions=["use the merge gate", "drop autonomy"],
        artifacts=["src/tide/launcher/handoff.py"],
        next_step="spawn the worker",
        open_questions=["which adapter?"],
        date="2026-06-25",
    )
    assert "# tide handoff — ship-it" in out
    assert "mode: continue" in out
    assert "arc: ship-it" in out
    assert "date: 2026-06-25" in out
    assert "## Where we are" in out and "we wired the gate" in out
    assert "## Decisions" in out and "- use the merge gate" in out
    assert "## Artifacts" in out and "handoff.py" in out
    assert "## Next step" in out and "spawn the worker" in out
    assert "## Open questions" in out and "- which adapter?" in out


def test_build_summary_omits_empty_sections_but_keeps_state_placeholder():
    out = handoff.build_summary(mode="close", arc_ref="x", date="2026-06-25")
    assert "## Where we are" in out
    assert "not distilled" in out
    # all the optional sections are gone when nothing was supplied
    assert "## Decisions" not in out
    assert "## Artifacts" not in out
    assert "## Next step" not in out
    assert "## Open questions" not in out


def test_summary_filename_uses_date():
    assert handoff.summary_filename("2026-06-25") == "handoff-2026-06-25.md"


# --- resolve + write -------------------------------------------------------

def test_resolve_open_entry_finds_open_arc(tmp_project):
    entry = stream.new_arc(tmp_project, "ship-it")
    found = handoff.resolve_open_entry(tmp_project, "ship-it")
    assert found == entry


def test_resolve_open_entry_none_for_missing(tmp_project):
    assert handoff.resolve_open_entry(tmp_project, "ghost") is None


def test_write_summary_lands_in_workspace(tmp_project):
    stream.new_arc(tmp_project, "ship-it")
    path = handoff.write_summary(
        tmp_project, "ship-it", "# distil\n", date="2026-06-25"
    )
    assert path.parent.name == handoff.WORKSPACE_DIRNAME
    assert path.name == "handoff-2026-06-25.md"
    assert path.read_text(encoding="utf-8") == "# distil\n"


def test_write_summary_refuses_unknown_arc(tmp_project):
    with pytest.raises(handoff.HandoffError):
        handoff.write_summary(tmp_project, "ghost", "x")


# --- reminders / fork (pure-ish) -------------------------------------------

def test_candidate_reminder_lists_backlog(tmp_project):
    candidate.new_candidate(tmp_project, "shiny-idea", body="a thought")
    text = handoff.candidate_reminder(tmp_project)
    assert "tide candidate add" in text
    assert "shiny-idea" in text


def test_candidate_reminder_empty(tmp_project):
    assert "(no candidates)" in handoff.candidate_reminder(tmp_project)


def test_fork_offer_names_all_three():
    text = handoff.fork_offer("ship-it")
    assert "continue" in text and "new" in text and "close" in text
    assert "ship-it" in text


# --- autospawn toggle ------------------------------------------------------

def test_autospawn_default_on():
    assert handoff.autospawn_enabled(None) is True
    assert handoff.autospawn_enabled({}) is True


def test_autospawn_explicit_false_disables():
    assert handoff.autospawn_enabled({"handoff_autospawn": False}) is False
    assert handoff.autospawn_enabled({"handoff_autospawn": True}) is True


def test_read_autospawn_reads_settings(tmp_project):
    claude = tmp_project / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(
        json.dumps({"handoff_autospawn": False}), encoding="utf-8"
    )
    assert handoff.read_autospawn(tmp_project) is False
    assert handoff.read_autospawn(tmp_project) is False


# --- run_handoff orchestration ---------------------------------------------

def test_run_handoff_continue_dry_run_writes_and_builds_spawn(tmp_project):
    stream.new_arc(tmp_project, "ship-it")
    res = handoff.run_handoff(
        tmp_project, arc_ref="ship-it", mode="continue", dry_run=True
    )
    assert res.summary_path.exists()
    assert res.summary_path.parent.name == handoff.WORKSPACE_DIRNAME
    assert res.autospawn is True
    assert res.spawn is not None and res.spawn.ok
    # the adapter command was built (dry-run) without executing
    assert res.spawn.commands
    # the seed it would carry resumes THIS arc
    assert any(c[0] == "orca" for c in res.spawn.commands)


def test_run_handoff_close_does_not_spawn(tmp_project):
    stream.new_arc(tmp_project, "ship-it")
    res = handoff.run_handoff(tmp_project, arc_ref="ship-it", mode="close")
    assert res.summary_path.exists()
    assert res.spawn is None
    assert any("no spawn" in n for n in res.notes)


def test_run_handoff_toggle_off_skips_spawn(tmp_project):
    stream.new_arc(tmp_project, "ship-it")
    res = handoff.run_handoff(
        tmp_project, arc_ref="ship-it", mode="continue", autospawn=False
    )
    assert res.summary_path.exists()
    assert res.spawn is None
    assert res.autospawn is False


def test_run_handoff_new_mode_spawns_orchestrator(tmp_project):
    stream.new_arc(tmp_project, "ship-it")
    res = handoff.run_handoff(
        tmp_project, arc_ref="ship-it", mode="new", dry_run=True
    )
    assert res.spawn is not None and res.spawn.ok


def test_run_handoff_unknown_mode_raises(tmp_project):
    stream.new_arc(tmp_project, "ship-it")
    with pytest.raises(handoff.HandoffError):
        handoff.run_handoff(tmp_project, arc_ref="ship-it", mode="sideways")


def test_run_handoff_uses_supplied_summary(tmp_project):
    stream.new_arc(tmp_project, "ship-it")
    res = handoff.run_handoff(
        tmp_project,
        arc_ref="ship-it",
        mode="close",
        summary="# my own distil\n\nthe thread\n",
    )
    assert res.summary_path.read_text(encoding="utf-8") == "# my own distil\n\nthe thread\n"


# --- CLI handler (e2e smoke) -----------------------------------------------

def test_cli_handoff_dry_run_smoke(tmp_project, monkeypatch, capsys):
    from tide import cli

    stream.new_arc(tmp_project, "ship-it")
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["handoff", "ship-it", "--mode", "continue", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "handoff [continue]" in out
    assert "Candidates backlog" in out
    assert "Fork —" in out
    # the distil landed in the arc workspace
    ws = handoff.resolve_open_entry(tmp_project, "ship-it") / "workspace"
    assert any(p.name.startswith("handoff-") for p in ws.iterdir())


def test_cli_handoff_summary_file(tmp_project, monkeypatch, tmp_path):
    from tide import cli

    stream.new_arc(tmp_project, "ship-it")
    sf = tmp_path / "distil.md"
    sf.write_text("# prepared distil\n", encoding="utf-8")
    monkeypatch.chdir(tmp_project)
    rc = cli.main(
        ["handoff", "ship-it", "--mode", "close", "--summary-file", str(sf)]
    )
    assert rc == 0
    ws = handoff.resolve_open_entry(tmp_project, "ship-it") / "workspace"
    written = next(p for p in ws.iterdir() if p.name.startswith("handoff-"))
    assert written.read_text(encoding="utf-8") == "# prepared distil\n"


# --- arc-worktree cwd + cross-project resolution ---------------------------

def _spawn_blob(res) -> str:
    """Flatten a dry-run SpawnResult's built commands into one searchable string."""
    return " ".join(" ".join(c) for c in (res.spawn.commands or []))


def test_run_handoff_continue_lands_in_arc_worktree_cwd(tmp_project):
    from tide.adapters.orca_worktree import WORKSPACE_FIELD
    from tide.arc import worktree

    arc = stream.new_arc(tmp_project, "ship-it")
    ws = tmp_project / "orca-ws"
    ws.mkdir()
    from tide import fields
    fields.set_field(worktree._passport(arc), WORKSPACE_FIELD, str(ws))

    res = handoff.run_handoff(tmp_project, arc_ref="ship-it", mode="continue", dry_run=True)
    # the spawn cwd reflects the arc's orca workspace, not the bare project root
    assert str(ws) in _spawn_blob(res)


def test_run_handoff_new_mode_uses_root_cwd(tmp_project):
    arc = stream.new_arc(tmp_project, "ship-it")
    from tide.adapters.orca_worktree import WORKSPACE_FIELD
    from tide.arc import worktree
    from tide import fields

    ws = tmp_project / "orca-ws"
    ws.mkdir()
    fields.set_field(worktree._passport(arc), WORKSPACE_FIELD, str(ws))

    res = handoff.run_handoff(tmp_project, arc_ref="ship-it", mode="new", dry_run=True)
    blob = _spawn_blob(res)
    # new mode seeds a project-level orchestrator at the root — never the arc worktree
    assert str(ws) not in blob
    assert str(tmp_project.resolve()) in blob


def test_run_handoff_cross_project_writes_into_owning_project(tmp_control_home, tmp_path):
    from tide import roster
    from tests.conftest import build_tide_skeleton

    from tide import fields
    from tide.adapters.orca_worktree import WORKSPACE_FIELD
    from tide.arc import worktree

    proj = tmp_path / "owner-proj"
    proj.mkdir()
    build_tide_skeleton(proj, name="owner")
    arc = stream.new_arc(proj, "remote-thread")
    # Record the arc's worktree so the cross-project spawn cwd resolves to it.
    ws = proj / "orca-ws"
    ws.mkdir()
    fields.set_field(worktree._passport(arc), WORKSPACE_FIELD, str(ws))
    roster.add(tmp_control_home, "owner", str(proj))

    # Fired from the control-home; the distil must land in the OWNING project's arc.
    res = handoff.run_handoff(
        tmp_control_home, arc_ref="remote-thread", mode="continue", dry_run=True
    )
    assert str(proj) in str(res.summary_path)
    assert res.summary_path.parent.name == handoff.WORKSPACE_DIRNAME
    assert res.summary_path.exists()
    # the cross-project spawn cwd is the OWNING project's arc worktree, not the
    # control-home root (mirrors the same-project worktree-cwd assertion)
    assert str(ws) in _spawn_blob(res)
