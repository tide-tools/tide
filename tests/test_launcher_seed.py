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


def test_build_seed_drops_the_role_section_when_no_prompt_is_shipped():
    # The seed no longer re-renders the one-line role reminder: the SessionStart
    # hook prints it in EVERY session and the launcher installs that hook before
    # every spawn, so a seeded session heard it once already. Absent a shipped
    # prompts/<role>.md the section is dropped — the header still names the role.
    out = seed.build_seed(project_name="demo", role="worker", canon_text="x")
    assert "WORKER" in out          # the header still says which role this is
    assert "## Role" not in out
    assert "ONE open arc" not in out  # the hook's reminder text, not the seed's


def test_build_seed_keeps_a_shipped_role_prompt():
    # a real prompts/<role>.md is substantive (not the hook's one-liner) — it rides.
    out = seed.build_seed(
        project_name="demo", role="worker", canon_text="x",
        prompt_text="# worker\nDo the one arc, write only its output.",
    )
    assert "## Role" in out
    assert "Do the one arc" in out


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


def test_session_framed_seed_carries_law_47_definition():
    # cand 127: the spark/handoff greet tells a fresh session to "build the plan by
    # law 47" — the DEFINITION must ride in the seed so a cold agent doesn't grep the
    # board source for it (the live report timed out doing exactly that). Carried in
    # the thread block, next to the start-gate.
    out = seed.build_seed(
        project_name="demo",
        canon_text="c",
        arc_ref="01-pickup",
        arc_text="goal: <one line>\nstatus: active",
        thread_name="handoff",
    )
    assert "Закон 47" in out
    assert "final:" in out          # the plan's shape is spelled out…
    assert "иммутабелен" in out     # …incl. immutability + the патчи section
    assert "## патчи" in out


def test_plain_arc_seed_has_no_law_47_definition():
    # like the start-gate, the law-47 block is for sessions inside a thread only.
    out = seed.build_seed(
        project_name="demo", canon_text="c", arc_ref="ship-it", arc_text="status: active",
    )
    assert "Закон 47" not in out


def test_seed_leaves_the_open_decisions_to_the_session_start_hook(tmp_project):
    # cand 128-A ships the nit's OPEN decisions into a fresh session — but the
    # SessionStart hook already injects that exact block from the same source, and
    # the launcher binds the sid into the session passport BEFORE spawn, so the hook
    # resolves the nit and prints it. Rendering it in the seed too printed it TWICE.
    from tide.arc import decision, stream

    stream.new_thread(tmp_project, "prz")
    stream.new_session(tmp_project, "prz", "work")
    decision.add_decision(tmp_project, "мы решили X", thread_ref="prz", dslug="keep-x")
    out = seed.seed_for_project(tmp_project, arc_ref="work", thread_name="prz")
    assert decision.CONTEXT_HEADER not in out


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


def test_seed_roster_hides_archived_projects(tmp_control_home):
    # An archived project is a dead path — handing it to a fresh session is 13 rows
    # of noise on the live home. The picker already filters; the seed now agrees.
    # `tide roster ls` (roster.render_list) still shows everything — that is its job.
    roster.add(tmp_control_home, "live", "/p/live")
    roster.add(tmp_control_home, "old", "/p/old", status=roster.STATUS_ARCHIVED)
    out = seed.seed_for_project(tmp_control_home, control_home=tmp_control_home)
    assert "live | /p/live" in out
    assert "/p/old" not in out
    assert "/p/old" in roster.render_list(tmp_control_home)  # ls is untouched


def test_seed_roster_note_is_one_line_carrying_both_commands(tmp_control_home):
    # The two evergreen paragraphs collapsed into one line; neither command is lost.
    roster.add(tmp_control_home, "live", "/p/live")
    out = seed.seed_for_project(tmp_control_home, control_home=tmp_control_home)
    note = out.split("live | /p/live", 1)[1].split("## Launch", 1)[0].strip()
    assert "tide candidate add" in note
    assert "tide adopt" in note
    assert len(note.splitlines()) == 1
