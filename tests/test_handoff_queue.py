"""Two-stage handoff queue — offer (stage 1) → confirmed pickup (stage 2)."""

from __future__ import annotations

import io

import pytest

from tide import cli, handoff_queue as hq, paths


# --- pure operations -------------------------------------------------------

def test_offer_writes_an_offered_record(tmp_control_home):
    path = hq.offer(tmp_control_home, "stabilize-tide", arc="01-x", project="tide",
                    seed="/s/seed.md", from_session="aaa")
    assert path.is_file()
    recs = hq.list_offers(tmp_control_home)
    assert len(recs) == 1
    r = recs[0]
    assert r["status"] == hq.STATUS_OFFERED
    assert r["project"] == "tide" and r["arc"] == "01-x"
    assert r["seed"] == "/s/seed.md"


def test_reserve_then_confirm_for_session(tmp_control_home):
    hq.offer(tmp_control_home, "a", arc="-", project="tide", seed="/s/a.md")
    hq.offer(tmp_control_home, "b", arc="-", project="tide", seed="/s/b.md")
    hq.reserve(tmp_control_home, "b", session="sess-123")  # reserve B for this session
    claimed = hq.confirm_for_session(tmp_control_home, "sess-123")
    assert claimed is not None
    assert claimed["slug"] == "b"                 # only the RESERVED one is claimed
    assert claimed["status"] == hq.STATUS_TAKEN
    assert claimed["taken_by"] == "sess-123"
    # A is untouched (not reserved for this session) — no project-wide vacuuming.
    assert [r["slug"] for r in hq.list_offers(tmp_control_home, status=hq.STATUS_OFFERED)] == ["a"]


def test_confirm_for_session_noop_without_reservation(tmp_control_home):
    # An ordinary session (not launched from a handoff) must NOT claim anything.
    hq.offer(tmp_control_home, "x", arc="-", project="tide", seed="-")
    assert hq.confirm_for_session(tmp_control_home, "random-session") is None
    assert hq.list_offers(tmp_control_home, status=hq.STATUS_OFFERED)  # still offered


def test_take_by_key_marks_taken(tmp_control_home):
    hq.offer(tmp_control_home, "ship-it", arc="-", project="p", seed="-")
    rec = hq.take(tmp_control_home, "ship-it", session="s")
    assert rec["status"] == hq.STATUS_TAKEN


def test_take_unknown_key_raises(tmp_control_home):
    with pytest.raises(hq.HandoffError, match="no offer matching"):
        hq.take(tmp_control_home, "ghost")


# --- drop (soft-archive an offer, prune its untouched session) -------------

def test_drop_soft_archives_offer(tmp_control_home):
    hq.offer(tmp_control_home, "skip-me", arc="-", project="p", seed="-")
    rec, pruned = hq.drop(tmp_control_home, "skip-me")
    assert rec["status"] == hq.STATUS_DROPPED
    assert pruned is False  # no seed → nothing to prune
    # the dropped offer no longer surfaces as pending
    assert hq.list_offers(tmp_control_home, status=hq.STATUS_OFFERED) == []
    # but the record is kept (soft archive, auditable)
    assert [r["slug"] for r in hq.list_offers(tmp_control_home)] == ["skip-me"]


def test_drop_refuses_taken_offer(tmp_control_home):
    hq.offer(tmp_control_home, "done", arc="-", project="p", seed="-")
    hq.take(tmp_control_home, "done", session="s")
    with pytest.raises(hq.HandoffError, match="already taken"):
        hq.drop(tmp_control_home, "done")


def _seeded_session(tmp_path, *, with_work=False):
    """Build a <session>/ dir with the handoff seed in input/; return the seed path."""
    sess = tmp_path / "07-@thread" / "arcs" / "03-session"
    (sess / "input").mkdir(parents=True)
    (sess / "workspace").mkdir()
    (sess / "output").mkdir()
    (sess / "arc.md").write_text("# 03-session\nstatus: active\n", encoding="utf-8")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# seed\n", encoding="utf-8")
    if with_work:
        (sess / "workspace" / "notes.md").write_text("real progress\n", encoding="utf-8")
    return sess, seed


def test_drop_prunes_untouched_session(tmp_control_home, tmp_path):
    sess, seed = _seeded_session(tmp_path)
    hq.offer(tmp_control_home, "abandon", arc="thread/session", project="p", seed=str(seed))
    rec, pruned = hq.drop(tmp_control_home, "abandon")
    assert rec["status"] == hq.STATUS_DROPPED
    assert pruned is True
    assert not sess.exists()  # the never-touched seeded session is gone


def test_drop_keeps_session_with_work(tmp_control_home, tmp_path):
    sess, seed = _seeded_session(tmp_path, with_work=True)
    hq.offer(tmp_control_home, "keep", arc="thread/session", project="p", seed=str(seed))
    rec, pruned = hq.drop(tmp_control_home, "keep")
    assert pruned is False  # real work in workspace/ → session kept (degrades to case A)
    assert sess.exists()


# --- CLI + hook ------------------------------------------------------------

def test_cli_offer_then_confirm_hook_flips_status(tmp_control_home, tmp_path, monkeypatch):
    from tide.init_home import scaffold_project

    proj = tmp_path / "tide"
    proj.mkdir()
    scaffold_project(proj, name="tide")
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))

    # stage 1: offer + reserve it for the picking-up session id (menu pickup does this)
    assert cli.main(["handoffs", "offer", "cont", "--project", "tide", "--seed", "/s.md"]) == 0
    hq.reserve(tmp_control_home, "cont", session="new-sess")
    assert hq.list_offers(tmp_control_home, status=hq.STATUS_OFFERED)

    # stage 2: the UserPromptSubmit hook fires in that session — its id (from stdin)
    # matches the reservation, so the offer flips to taken.
    monkeypatch.chdir(proj)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id": "new-sess"}'))
    assert cli.main(["hook", "handoff-confirm"]) == 0

    offered = hq.list_offers(tmp_control_home, status=hq.STATUS_OFFERED)
    taken = hq.list_offers(tmp_control_home, status=hq.STATUS_TAKEN)
    assert not offered and len(taken) == 1  # offered → taken on first message


def test_confirm_hook_is_silent_noop_with_nothing_pending(tmp_project, monkeypatch):
    # A hook must never break an ordinary session: no offers → exit 0, no claim.
    monkeypatch.setenv("TIDE_HOME", str(tmp_project))
    monkeypatch.chdir(tmp_project)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert cli.main(["hook", "handoff-confirm"]) == 0


def test_menu_banner_surfaces_pending_handoffs(tmp_control_home):
    from tide.launcher import menu

    hq.offer(tmp_control_home, "cont", arc="01-x", project="tide-stack", seed="/s/seed.md")
    entries = [{"name": "tide-stack", "path": "/p/tide-stack"}]
    banner = menu.render_pending_handoffs(tmp_control_home, entries)
    assert "pending handoffs" in banner
    assert "01-cont" in banner and "tide-stack" in banner
    assert "/p/tide-stack" in banner and "/s/seed.md" in banner  # actionable pickup cmd


def test_menu_banner_empty_when_nothing_offered(tmp_control_home):
    from tide.launcher import menu

    assert menu.render_pending_handoffs(tmp_control_home, []) == ""


def test_root_offers_fast_continue(monkeypatch):
    """Pending handoffs LEAD the root as ⇄ continue rows — resume in one click."""
    from tide.launcher import menu

    captured = {}

    def fake_select(title, options, **kwargs):
        captured["options"] = list(options)
        return 0  # pick the first row = the fast-continue handoff

    monkeypatch.setattr(menu.select, "select", fake_select)
    rec = {"slug": "stab", "project": "p", "mode": "continue", "seed": "-"}
    res = menu.navigate_interactive([{"name": "p", "path": "/p"}], handoffs=[rec])
    assert res[0] == menu.HANDOFF_PICK and res[1] is rec  # 1-click pickup
    assert captured["options"][0].startswith("⇄ continue")  # continue row leads
    assert captured["options"][-1] == "p → /p"  # project below it


def test_project_offers_maps_seed_to_thread_and_session(tmp_path):
    """An offer's thread/session are derived from its seed PATH, not the arc field."""
    from tide.launcher import menu
    from tide.arc import stream
    from tide.init_home import scaffold_project

    proj = tmp_path / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    stream.new_thread(proj, "kickoff")
    sess = stream.new_session(proj, "kickoff", "work")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# distil\n", encoding="utf-8")

    offers = menu.project_offers([{"slug": "h", "seed": str(seed)}], proj)
    assert len(offers) == 1
    assert offers[0]["thread"] == "kickoff" and offers[0]["session"] == "work"
    # an offer whose seed lives in another project is not mapped here
    assert menu.project_offers([{"slug": "x", "seed": "/elsewhere/in/s.md"}], proj) == []


def test_pickup_offered_session_inside_thread_returns_handoff_pick(tmp_path, monkeypatch):
    """A handoff is picked up from INSIDE its thread (project → Threads → thread → ⇄ → pick up)."""
    from tide.launcher import menu
    from tide.arc import stream
    from tide.init_home import scaffold_project

    proj = tmp_path / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    stream.new_thread(proj, "kickoff")
    sess = stream.new_session(proj, "kickoff", "work")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# distil\n", encoding="utf-8")
    rec = {"slug": "h", "project": "proj", "mode": "continue", "seed": str(seed)}

    # root leads with the ⇄ continue row (index 0); pick the PROJECT (index 1) to go
    # via the IN-THREAD path: project(1) → Threads(0) → thread(0) → session ⇄(0) → pick up(0)
    seq = iter([1, 0, 0, 0, 0])
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    res = menu.navigate_interactive([{"name": "proj", "path": str(proj)}], handoffs=[rec])
    assert res[0] == menu.HANDOFF_PICK and res[1] is rec


def test_dismiss_offered_session_from_menu_drops_it(tmp_control_home, tmp_path, monkeypatch):
    """Dismissing a ⇄ session in the menu drops the offer and prunes its dead session."""
    from tide.launcher import menu
    from tide.arc import stream
    from tide.init_home import scaffold_project

    proj = tmp_path / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    stream.new_thread(proj, "kickoff")
    sess = stream.new_session(proj, "kickoff", "work")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# distil\n", encoding="utf-8")
    hq.offer(tmp_control_home, "h", arc="kickoff/work", project="proj", seed=str(seed))
    rec = hq.list_offers(tmp_control_home)[0]

    # root now leads with the ⇄ continue row, so the project is index 1:
    # project(1) → Threads(0) → thread(0) → session ⇄(0) → dismiss(1); thread then
    # empties → the session step auto-creates a fresh first session (thread law).
    seq = iter([1, 0, 0, 0, 1])
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    menu.navigate_interactive([{"name": "proj", "path": str(proj)}], handoffs=[rec])

    assert hq.list_offers(tmp_control_home, status=hq.STATUS_OFFERED) == []  # no longer pending
    assert hq.list_offers(tmp_control_home)[0]["status"] == hq.STATUS_DROPPED  # soft-archived
    assert not sess.exists()  # untouched seeded session pruned


def test_launch_handoff_seeds_but_stays_offered_until_confirmed(tmp_control_home, tmp_path):
    from tide.launcher import menu
    from tide.adapters import SpawnResult
    from tide.init_home import scaffold_project

    proj = tmp_path / "tide-stack"
    proj.mkdir()
    scaffold_project(proj, name="tide-stack")
    seed = tmp_path / "seed.md"
    seed.write_text("# distil\n", encoding="utf-8")
    hq.offer(tmp_control_home, "stab", arc="01-x", project="tide-stack", seed=str(seed))
    rec = hq.list_offers(tmp_control_home)[0]
    entries = [{"name": "tide-stack", "path": str(proj)}]

    captured = {}

    class FakeAdapter:
        def spawn(self, *, command, cwd, title, dry_run):
            captured["command"] = command
            captured["cwd"] = cwd
            return SpawnResult(ok=True, detail="spawned", commands=[command])

    res = menu.launch_handoff(
        rec, entries, control_home=tmp_control_home, adapter=FakeAdapter(), dry_run=False
    )
    assert res.ok
    assert str(seed) in " ".join(captured["command"])   # session seeded from the distil
    assert captured["cwd"] == str(proj)
    # CRITICAL: launching does NOT consume the offer — it stays OFFERED until the
    # picked-up session's first message confirms it (the confirm hook).
    assert hq.list_offers(tmp_control_home, status=hq.STATUS_OFFERED)
    assert not hq.list_offers(tmp_control_home, status=hq.STATUS_TAKEN)


def test_launch_handoff_pins_session_id_for_menu_resume(tmp_control_home, tmp_path):
    # After pickup the session must be RESUMABLE from the menu: the new claude
    # session id is pinned onto the handoff's target session passport.
    from tide.launcher import menu
    from tide.adapters import SpawnResult
    from tide import fields
    from tide.init_home import scaffold_project

    proj = tmp_path / "tide-stack"
    proj.mkdir()
    scaffold_project(proj, name="tide-stack")
    # a session dir with input/<seed> + arc.md (the handoff's target shape)
    sess = proj / ".tide" / "arcs" / "01-@prz" / "arcs" / "01-session"
    (sess / "input").mkdir(parents=True)
    (sess / "arc.md").write_text("# 01-session\nstatus: active\n", encoding="utf-8")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# distil\n", encoding="utf-8")
    hq.offer(tmp_control_home, "stab", arc="01-@prz/01-session", project="tide-stack", seed=str(seed))
    rec = hq.list_offers(tmp_control_home)[0]

    class FakeAdapter:
        def spawn(self, *, command, cwd, title, dry_run):
            return SpawnResult(ok=True, detail="spawned", commands=[command])

    menu.launch_handoff(
        rec, [{"name": "tide-stack", "path": str(proj)}],
        control_home=tmp_control_home, adapter=FakeAdapter(), dry_run=False,
    )
    pinned = fields.read_field(sess / "arc.md", "claude-session")
    assert pinned and len(pinned) > 10  # a uuid was stamped → menu can --resume it


def test_install_hooks_wires_user_prompt_submit(tmp_project):
    from tide.hooks.install import install_hooks, HANDOFF_CONFIRM_CMD, USER_PROMPT_EVENT
    import json

    path, notes = install_hooks(tmp_project)
    data = json.loads(path.read_text(encoding="utf-8"))
    cmds = [
        h.get("command")
        for g in data["hooks"][USER_PROMPT_EVENT]
        for h in g.get("hooks", [])
    ]
    assert HANDOFF_CONFIRM_CMD in cmds
