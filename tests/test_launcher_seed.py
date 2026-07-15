"""U11 unit — launcher.seed: build a seed from canon + arc + roster + prompt."""

from __future__ import annotations

from tide import roster
from tide.arc import stream
from tide.launcher import seed


# --- pure build_seed -------------------------------------------------------

def test_build_seed_carries_project_role_and_canon():
    out = seed.build_seed(
        project_name="focus",
        role="orchestrator",
        canon_text="# CANON.md — focus\n## What it is\nthe genius",
    )
    assert seed.SEED_TITLE in out
    assert "ORCHESTRATOR" in out
    assert "focus" in out
    assert "the genius" in out
    # roster + arc sections are omitted when not supplied
    assert "## Active arc" not in out
    assert "## Roster" not in out


def test_build_seed_falls_back_to_role_reminder_when_no_prompt():
    out = seed.build_seed(project_name="demo", role="worker", canon_text="x")
    # worker reminder text leaks through the fallback
    assert "WORKER" in out
    assert "ONE open arc" in out


def test_build_seed_includes_arc_and_roster_when_given():
    out = seed.build_seed(
        project_name="demo",
        canon_text="c",
        arc_ref="ship-it",
        arc_text="goal: ship it\nstatus: active",
        roster_text="focus | /p/focus\npulse | /p/pulse",
    )
    assert "## Active arc — ship-it" in out
    assert "ship it" in out
    assert "## Roster" in out
    assert "/p/pulse" in out


def test_build_seed_notes_empty_canon():
    out = seed.build_seed(project_name="demo", canon_text="   ")
    assert "no canon yet" in out


def test_launch_command_shapes():
    assert seed.launch_command("focus") == "tide focus"
    assert seed.launch_command("focus", "ship-it") == "tide focus ship-it"


def test_session_framed_seed_carries_the_start_gate():
    # a fresh session must be told to fill goal+title + first offload BEFORE work,
    # or the board reads blind for the whole session (cand 81/87).
    out = seed.build_seed(
        project_name="demo",
        canon_text="c",
        arc_ref="01-pickup",
        arc_text="goal: <one line>\nstatus: active",
        thread_name="handoff",
    )
    assert "Старт-гейт" in out
    assert "tide offload" in out
    assert "goal:" in out


def test_plain_arc_seed_has_no_start_gate():
    # the gate is for sessions inside a thread; a bare arc seed stays lean.
    out = seed.build_seed(
        project_name="demo", canon_text="c", arc_ref="ship-it", arc_text="status: active",
    )
    assert "Старт-гейт" not in out


# --- disk wrapper seed_for_project -----------------------------------------

def test_seed_for_project_reads_canon(tmp_project):
    out = seed.seed_for_project(tmp_project)
    # conftest seeds CANON.md '# CANON.md — demo'
    assert "CANON.md — demo" in out
    assert "ORCHESTRATOR" in out


def test_seed_for_project_includes_open_arc_passport(tmp_project):
    stream.new_arc(tmp_project, "ship-it")
    out = seed.seed_for_project(tmp_project, arc_ref="ship-it")
    assert "## Active arc — ship-it" in out
    # the arc.md passport text is embedded (it carries a status: field)
    assert "status:" in out


def test_seed_for_project_arc_missing_is_a_note(tmp_project):
    out = seed.seed_for_project(tmp_project, arc_ref="ghost")
    assert "## Active arc — ghost" in out
    assert "no open arc passport found" in out


def test_read_arc_passport_matches_displayed_entry_name(tmp_project):
    # Одна из четырёх копий резолвера (seed) матчила только bare-слаг: имя,
    # которое печатает tide status ('01-ship-it'), молча промахивалось — та же
    # cand-43 ловушка. Единый tide.resolve матчит обе формы на всех поверхностях.
    entry = stream.new_arc(tmp_project, "ship-it")
    assert seed.read_arc_passport(tmp_project, entry.name) is not None
    assert seed.read_arc_passport(tmp_project, "ship-it") is not None


def test_seed_for_project_includes_roster_from_control_home(tmp_control_home):
    roster.add(tmp_control_home, "focus", "/p/focus")
    out = seed.seed_for_project(
        tmp_control_home, control_home=tmp_control_home
    )
    assert "## Roster" in out
    assert "focus | /p/focus" in out


def test_seed_for_project_no_roster_when_not_control_home(tmp_project):
    # a plain project (no roster.md) → no roster section even if control_home passed
    out = seed.seed_for_project(tmp_project, control_home=tmp_project)
    assert "## Roster" not in out
