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


def test_resolve_open_entry_matches_prefixed_name(tmp_project):
    # the entry name `tide status` PRINTS (NN-[@]slug) must resolve the same as
    # the bare slug — regression for the slugify/entry_slug asymmetry that sent
    # agents chasing 'tide arc new' and duplicating arcs.
    entry = stream.new_arc(tmp_project, "ship-it")
    assert handoff.resolve_open_entry(tmp_project, entry.name) == entry


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


# --- run_handoff orchestration ---------------------------------------------
# ONE path into the loop (cand 05): handoff distils, then hangs an offer in the
# control-home queue. It NEVER spawns a terminal — pickup is pull (tide menu).

def _queue_home(monkeypatch, tmp_path):
    """A control-home for the offer queue, wired via $TIDE_HOME."""
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "control-home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    monkeypatch.setenv("TIDE_HOME", str(home))
    return home


def test_run_handoff_continue_hangs_offer_in_queue(tmp_project, monkeypatch, tmp_path):
    from tide import handoff_queue

    home = _queue_home(monkeypatch, tmp_path)
    stream.new_arc(tmp_project, "ship-it")
    res = handoff.run_handoff(
        tmp_project, arc_ref="ship-it", mode="continue", from_session="origin-123"
    )
    assert res.summary_path.exists()
    assert res.summary_path.parent.name == handoff.WORKSPACE_DIRNAME
    assert res.offer_path is not None and res.offer_path.exists()
    (rec,) = handoff_queue.list_offers(home)
    assert rec["status"] == "offered"
    assert rec["mode"] == "continue"
    assert rec["arc"] == "ship-it"
    # the distil doubles as the seed pointer + the origin is recorded for the
    # multiples detector
    assert rec["seed"] == str(res.summary_path)
    assert rec["from_session"] == "origin-123"


def test_run_handoff_new_mode_hangs_offer(tmp_project, monkeypatch, tmp_path):
    from tide import handoff_queue

    home = _queue_home(monkeypatch, tmp_path)
    stream.new_arc(tmp_project, "ship-it")
    res = handoff.run_handoff(tmp_project, arc_ref="ship-it", mode="new")
    assert res.offer_path is not None
    (rec,) = handoff_queue.list_offers(home)
    assert rec["mode"] == "new"


def test_run_handoff_close_hangs_no_offer(tmp_project, monkeypatch, tmp_path):
    from tide import handoff_queue

    home = _queue_home(monkeypatch, tmp_path)
    stream.new_arc(tmp_project, "ship-it")
    res = handoff.run_handoff(tmp_project, arc_ref="ship-it", mode="close")
    assert res.summary_path.exists()
    assert res.offer_path is None
    assert handoff_queue.list_offers(home) == []
    assert any("no offer" in n for n in res.notes)


def test_run_handoff_dry_run_leaves_queue_untouched(tmp_project, monkeypatch, tmp_path):
    from tide import handoff_queue

    home = _queue_home(monkeypatch, tmp_path)
    stream.new_arc(tmp_project, "ship-it")
    res = handoff.run_handoff(
        tmp_project, arc_ref="ship-it", mode="continue", dry_run=True
    )
    assert res.summary_path.exists()   # the distil is still written
    assert res.offer_path is None
    assert handoff_queue.list_offers(home) == []


def test_run_handoff_no_control_home_fails_loud(tmp_project):
    stream.new_arc(tmp_project, "ship-it")
    with pytest.raises(handoff.HandoffError, match="control-home"):
        handoff.run_handoff(tmp_project, arc_ref="ship-it", mode="continue")


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
    assert "queue untouched" in out
    # the distil landed in the arc workspace
    ws = handoff.resolve_open_entry(tmp_project, "ship-it") / "workspace"
    assert any(p.name.startswith("handoff-") for p in ws.iterdir())


def test_cli_handoff_offers_and_notes_retired_flags(tmp_project, monkeypatch, tmp_path, capsys):
    from tide import cli, handoff_queue

    home = _queue_home(monkeypatch, tmp_path)
    stream.new_arc(tmp_project, "ship-it")
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["handoff", "ship-it", "--no-spawn"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "offer hung" in out
    assert "--no-spawn is retired" in out
    (rec,) = handoff_queue.list_offers(home)
    assert rec["status"] == "offered"


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


# --- cross-project resolution ----------------------------------------------

def test_run_handoff_cross_project_writes_into_owning_project(tmp_control_home, tmp_path, monkeypatch):
    from tide import handoff_queue, roster
    from tests.conftest import build_tide_skeleton

    proj = tmp_path / "owner-proj"
    proj.mkdir()
    build_tide_skeleton(proj, name="owner")
    stream.new_arc(proj, "remote-thread")
    roster.add(tmp_control_home, "owner", str(proj))
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))

    # Fired from the control-home; the distil must land in the OWNING project's arc.
    res = handoff.run_handoff(
        tmp_control_home, arc_ref="remote-thread", mode="continue"
    )
    assert str(proj) in str(res.summary_path)
    assert res.summary_path.parent.name == handoff.WORKSPACE_DIRNAME
    assert res.summary_path.exists()
    # ...and the offer names the OWNING project by its ROSTER name — the dir
    # name 'owner-proj' is a dev alias pickup would die on (cand 17).
    (rec,) = handoff_queue.list_offers(tmp_control_home)
    assert rec["project"] == "owner"
    assert rec["seed"] == str(res.summary_path)


# --- Fix B: continue/new into a THREAD anchors on a fresh session ------------
# (cand 38 + agent report 2026-07-07: thread-anchored offers are invisible in
# the menu — pickup resolves only through <thread>/<session> + session seed.)

def test_run_handoff_thread_births_session_and_anchors_offer(tmp_project, monkeypatch, tmp_path):
    from tide import handoff_queue

    home = _queue_home(monkeypatch, tmp_path)
    entry = stream.new_thread(tmp_project, "hygiene", goal="keep the seam clean")
    res = handoff.run_handoff(
        tmp_project, arc_ref="hygiene", mode="continue", from_session="o1"
    )
    (rec,) = handoff_queue.list_offers(home)
    assert rec["arc"].startswith(entry.name + "/")        # <thread>/<session>
    assert "pickup" in rec["arc"]
    assert res.summary_path.name == "handoff-seed.md"
    assert res.summary_path.parent.name == "input"        # seed in SESSION input
    # menu maps seed.parent.parent → the session dir
    assert res.summary_path.parent.parent.name.endswith("pickup")
    assert rec["seed"] == str(res.summary_path)
    assert any("session born" in n for n in res.notes)


def test_run_handoff_second_pickup_gets_unique_slug(tmp_project, monkeypatch, tmp_path):
    # cand 66/78: two handoffs in one thread must NOT both be slug 'pickup' — a shared
    # slug breaks offload resolution and lineage. The dir stays NN-, the slug varies.
    from tide import offload, slug as _slug

    _queue_home(monkeypatch, tmp_path)
    entry = stream.new_thread(tmp_project, "hygiene", goal="keep the seam clean")
    handoff.run_handoff(tmp_project, arc_ref="hygiene", mode="continue", from_session="o1")
    handoff.run_handoff(tmp_project, arc_ref="hygiene", mode="continue", from_session="o2")

    names = sorted(d.name for d in (entry / "arcs").iterdir() if d.is_dir())
    slugs = [_slug.entry_slug(n) for n in names]
    assert len(set(slugs)) == len(slugs), "pickups share a slug: {0}".format(slugs)
    assert "pickup" in slugs and any(s.startswith("pickup-") for s in slugs)
    # each session now resolves unambiguously by its exact (unique) dir name
    for name in names:
        assert offload.find_session(tmp_project, name).name == name


def test_run_handoff_auto_fills_origin_from_active_session(tmp_project, monkeypatch, tmp_path):
    # cand 78: 'one holder per thread' must not go blind when --from-session is omitted.
    from tide import handoff_queue, fields

    home = _queue_home(monkeypatch, tmp_path)
    stream.new_thread(tmp_project, "hygiene", goal="keep the seam clean")
    sess = stream.new_session(tmp_project, "hygiene", "work")
    fields.set_field(sess / "arc.md", "claude-session", "sid-live")

    handoff.run_handoff(tmp_project, arc_ref="hygiene", mode="continue")  # no from_session
    (rec,) = handoff_queue.list_offers(home)
    assert rec["from_session"] == "sid-live"  # auto-derived from the thread's holder


def test_run_handoff_pickup_gets_real_goal_from_next_step(tmp_project, monkeypatch, tmp_path):
    # cand 84: a pickup born with no goal shows '<one line …>' on the board. The
    # handoff auto-fills it from the distil's stated next step.
    from tide import fields

    _queue_home(monkeypatch, tmp_path)
    entry = stream.new_thread(tmp_project, "hygiene", goal="keep the seam clean")
    distil = "# distil\n\nСледующий шаг: подчистить мёртвый код в live_projection\n"
    handoff.run_handoff(tmp_project, arc_ref="hygiene", mode="continue",
                        summary=distil, from_session="o1")
    born = sorted(d for d in (entry / "arcs").iterdir() if d.is_dir())[-1]
    goal = fields.read_field(born / "arc.md", "goal")
    assert goal and "<" not in goal                 # no template placeholder
    assert "подчистить мёртвый код" in goal          # took the next-step line


def test_run_handoff_pickup_goal_falls_back_to_thread_goal(tmp_project, monkeypatch, tmp_path):
    from tide import fields

    _queue_home(monkeypatch, tmp_path)
    entry = stream.new_thread(tmp_project, "hygiene", goal="keep the seam clean")
    distil = "# distil\n\nсделал то-то, без явного следующего шага\n"
    handoff.run_handoff(tmp_project, arc_ref="hygiene", mode="continue",
                        summary=distil, from_session="o1")
    born = sorted(d for d in (entry / "arcs").iterdir() if d.is_dir())[-1]
    assert fields.read_field(born / "arc.md", "goal") == "keep the seam clean"


def test_run_handoff_explicit_from_session_wins_over_auto(tmp_project, monkeypatch, tmp_path):
    from tide import handoff_queue, fields

    home = _queue_home(monkeypatch, tmp_path)
    stream.new_thread(tmp_project, "hygiene", goal="keep the seam clean")
    sess = stream.new_session(tmp_project, "hygiene", "work")
    fields.set_field(sess / "arc.md", "claude-session", "sid-live")

    handoff.run_handoff(tmp_project, arc_ref="hygiene", mode="continue", from_session="explicit")
    (rec,) = handoff_queue.list_offers(home)
    assert rec["from_session"] == "explicit"  # caller's value is not overridden


def test_run_handoff_thread_ref_matches_displayed_name(tmp_project, monkeypatch, tmp_path):
    _queue_home(monkeypatch, tmp_path)
    entry = stream.new_thread(tmp_project, "hygiene", goal="real goal")
    res = handoff.run_handoff(tmp_project, arc_ref=entry.name, mode="new")
    assert res.offer_path is not None                     # '01-@hygiene' resolves too


def test_run_handoff_thread_dry_run_creates_no_session(tmp_project, monkeypatch, tmp_path):
    from tide import handoff_queue

    home = _queue_home(monkeypatch, tmp_path)
    entry = stream.new_thread(tmp_project, "hygiene", goal="real goal")
    res = handoff.run_handoff(tmp_project, arc_ref="hygiene", mode="continue", dry_run=True)
    assert handoff_queue.list_offers(home) == []
    assert not any((entry / "arcs").iterdir())            # no side-effect session
    assert res.summary_path.parent.name == handoff.WORKSPACE_DIRNAME


def test_run_handoff_plain_arc_keeps_legacy_anchor(tmp_project, monkeypatch, tmp_path):
    from tide import handoff_queue

    home = _queue_home(monkeypatch, tmp_path)
    stream.new_arc(tmp_project, "ship-it")
    handoff.run_handoff(tmp_project, arc_ref="ship-it", mode="continue")
    (rec,) = handoff_queue.list_offers(home)
    assert rec["arc"] == "ship-it"                        # unchanged for non-threads
