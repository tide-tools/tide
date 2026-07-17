"""Two-stage handoff queue — offer (stage 1) → confirmed pickup (stage 2)."""

from __future__ import annotations

import io

import pytest

from tests.conftest import fill_entry

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


# --- take is ATOMIC: it also closes the reception seam on the passport (cand 77) ---

def _project_with_seeded_offer(tmp_control_home):
    """A scaffolded project + thread/session + an offer whose seed is a real file."""
    from tide import roster
    from tide.arc import stream
    from tide.init_home import scaffold_project

    proj = tmp_control_home / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    roster.add(tmp_control_home, "proj", str(proj))
    entry = stream.new_thread(proj, "redesign")
    sess = stream.new_session(proj, "redesign", "kickoff")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# seed\n\nделай следующий шаг\n", encoding="utf-8")
    hq.offer(tmp_control_home, "kickoff", project="proj", seed=str(seed),
             arc="{0}/{1}".format(entry.name, sess.name))
    return sess


def test_take_stamps_passport_and_pulses(tmp_control_home):
    from tide import fields

    sess = _project_with_seeded_offer(tmp_control_home)
    hq.take(tmp_control_home, "kickoff", session="claude-xyz")

    passport = sess / "arc.md"
    # (1) offer→taken already covered elsewhere; (2) session pinned → ⟳ resume works;
    assert fields.read_field(passport, "claude-session") == "claude-xyz"
    # (3) status live, and (4) first pulse landed so the board reads it as alive.
    assert fields.read_field(passport, "status") == "active"
    text = passport.read_text(encoding="utf-8")
    assert "нить принята" in text
    assert fields.read_field(passport, "offloaded-at") not in (None, "0", "")


def test_confirm_hook_path_is_atomic_too(tmp_control_home):
    from tide import fields

    sess = _project_with_seeded_offer(tmp_control_home)
    hq.reserve(tmp_control_home, "kickoff", session="claude-hook")
    hq.confirm_for_session(tmp_control_home, "claude-hook")

    passport = sess / "arc.md"
    assert fields.read_field(passport, "claude-session") == "claude-hook"
    assert fields.read_field(passport, "status") == "active"
    assert "нить принята" in passport.read_text(encoding="utf-8")


def test_take_missing_seed_still_flips_registry(tmp_control_home):
    # No real seed → stamping is skipped, but the registry flip must still happen
    # (taking an offer never raises on the passport side).
    hq.offer(tmp_control_home, "no-seed", arc="-", project="p", seed="-")
    rec = hq.take(tmp_control_home, "no-seed", session="s")
    assert rec["status"] == hq.STATUS_TAKEN


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


def test_root_marks_project_with_offer(monkeypatch):
    """Pending handoffs no longer lead the root — the owning project is marked ⊕N instead."""
    from tide.launcher import menu

    captured = {}

    def fake_select(title, options, **kwargs):
        captured["options"] = list(options)
        return menu.select.BACK  # inspect the root, then cancel

    monkeypatch.setattr(menu.select, "select", fake_select)
    rec = {"slug": "stab", "project": "p", "mode": "continue", "seed": "-"}
    res = menu.navigate_interactive([{"name": "p", "path": "/p"}], handoffs=[rec])
    assert res is None  # backed out
    assert captured["options"] == ["p → /p  ⊕1"]  # project marked, no ⇄ continue row
    assert not any("⇄ continue" in o for o in captured["options"])


def test_project_offers_maps_seed_to_thread_and_session(tmp_path):
    """An offer's thread/session are derived from its seed PATH, not the arc field."""
    from tide.launcher import menu
    from tide.arc import stream
    from tide.init_home import scaffold_project

    proj = tmp_path / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    fill_entry(stream.new_thread(proj, "kickoff"))
    sess = stream.new_session(proj, "kickoff", "work")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# distil\n", encoding="utf-8")

    offers = menu.project_offers([{"slug": "h", "seed": str(seed)}], proj)
    assert len(offers) == 1
    assert offers[0]["thread"] == "kickoff" and offers[0]["session"] == "work"
    # an offer whose seed lives in another project is not mapped here
    assert menu.project_offers([{"slug": "x", "seed": "/elsewhere/in/s.md"}], proj) == []


def test_project_offers_resolves_by_arc_even_if_seed_misplaced(tmp_path):
    """The arc field — not the seed location — decides the offer's home. A seed put in
    the wrong dir can no longer hide an offer from the menu (regression guard)."""
    from tide.launcher import menu
    from tide.arc import stream
    from tide.init_home import scaffold_project

    proj = tmp_path / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    thread = fill_entry(stream.new_thread(proj, "kickoff"))
    stream.new_session(proj, "kickoff", "work")
    # seed dumped in the THREAD's input (the wrong place), not the session's
    bad_seed = thread / "input" / "handoff-seed.md"
    bad_seed.parent.mkdir(parents=True, exist_ok=True)
    bad_seed.write_text("# distil\n", encoding="utf-8")
    rec = {"slug": "h", "arc": "kickoff/work", "seed": str(bad_seed)}

    offers = menu.project_offers([rec], proj)
    assert len(offers) == 1  # still found — resolved by the arc field
    assert offers[0]["thread"] == "kickoff" and offers[0]["session"] == "work"


def test_pickup_offered_session_inside_thread_returns_handoff_pick(tmp_path, monkeypatch):
    """A handoff is picked up from INSIDE its thread (project → Threads → thread → ⇄ → pick up)."""
    from tide.launcher import menu
    from tide.arc import stream
    from tide.init_home import scaffold_project

    proj = tmp_path / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    fill_entry(stream.new_thread(proj, "kickoff"))
    sess = stream.new_session(proj, "kickoff", "work")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# distil\n", encoding="utf-8")
    rec = {"slug": "h", "project": "proj", "mode": "continue", "seed": str(seed)}

    # the project (marked ⊕1) is index 0; go via the IN-THREAD path. The type step now
    # leads with "⇄ Handoffs (1)"(0), so Threads is index 1:
    # project(0) → Threads(1) → thread(0) → session ⇄(0) → pick up(0)
    seq = iter([0, 1, 0, 0, 0])
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
    fill_entry(stream.new_thread(proj, "kickoff"))
    sess = stream.new_session(proj, "kickoff", "work")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# distil\n", encoding="utf-8")
    hq.offer(tmp_control_home, "h", arc="kickoff/work", project="proj", seed=str(seed))
    rec = hq.list_offers(tmp_control_home)[0]

    # the project (marked ⊕1) is index 0; the type step leads with "⇄ Handoffs (1)"(0),
    # so Threads is index 1:
    # project(0) → Threads(1) → thread(0) → session ⇄(0) → dismiss(1); thread then
    # empties → the session step auto-creates a fresh first session (thread law).
    seq = iter([0, 1, 0, 0, 1])
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    menu.navigate_interactive([{"name": "proj", "path": str(proj)}], handoffs=[rec])

    assert hq.list_offers(tmp_control_home, status=hq.STATUS_OFFERED) == []  # no longer pending
    assert hq.list_offers(tmp_control_home)[0]["status"] == hq.STATUS_DROPPED  # soft-archived
    assert not sess.exists()  # untouched seeded session pruned


def test_launch_handoff_takes_offer_on_success(tmp_control_home, tmp_path):
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
    # signed A (14.07): the launch only RESERVES the offer for the minted sid — the
    # reception is real when the terminal says hello, so the flip to taken happens on
    # the session's first message (confirm_for_session), never at spawn.
    pending = hq.list_offers(tmp_control_home, status=hq.STATUS_OFFERED)
    assert pending and pending[0]["pickup_session"]
    sid = pending[0]["pickup_session"]
    assert "--session-id {0}".format(sid) in " ".join(captured["command"])
    assert hq.confirm_for_session(tmp_control_home, sid)
    assert not hq.list_offers(tmp_control_home, status=hq.STATUS_OFFERED)
    assert hq.list_offers(tmp_control_home, status=hq.STATUS_TAKEN)


def test_launch_handoff_leaves_offer_on_failed_launch(tmp_control_home, tmp_path):
    """A FAILED launch does NOT consume the offer — it stays offered, recoverable."""
    from tide.launcher import menu
    from tide.adapters import SpawnResult
    from tide.init_home import scaffold_project

    proj = tmp_path / "p"
    proj.mkdir()
    scaffold_project(proj, name="p")
    seed = tmp_path / "seed.md"
    seed.write_text("# distil\n", encoding="utf-8")
    hq.offer(tmp_control_home, "stab", arc="01-x", project="p", seed=str(seed))
    rec = hq.list_offers(tmp_control_home)[0]

    class FailAdapter:
        def spawn(self, *, command, cwd, title, dry_run):
            return SpawnResult(ok=False, detail="spawn failed", commands=[command])

    res = menu.launch_handoff(
        rec, [{"name": "p", "path": str(proj)}], control_home=tmp_control_home,
        adapter=FailAdapter(), dry_run=False,
    )
    assert not res.ok
    assert hq.list_offers(tmp_control_home, status=hq.STATUS_OFFERED)  # still recoverable


def test_handoffs_shown_at_type_step(tmp_path, monkeypatch):
    """A project's offers are their OWN option at the type step: ⇄ Handoffs (N)."""
    from tide.launcher import menu
    from tide.arc import stream
    from tide.init_home import scaffold_project

    proj = tmp_path / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    fill_entry(stream.new_thread(proj, "kickoff"))
    sess = stream.new_session(proj, "kickoff", "work")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# distil\n", encoding="utf-8")
    rec = {"slug": "h", "project": "proj", "mode": "continue", "seed": str(seed)}

    seen = {}

    def fake_select(title, options, **kwargs):
        if title.startswith("What in"):
            seen["type"] = list(options)
            return 0  # ⇄ Handoffs (1)
        if title.startswith("Handoff to continue"):
            seen["offers"] = list(options)
            return 0  # the one offer
        return 0

    monkeypatch.setattr(menu.select, "select", fake_select)
    res = menu.navigate_interactive([{"name": "proj", "path": str(proj)}], handoffs=[rec])
    assert res[0] == menu.HANDOFF_PICK and res[1] is rec  # picked up from the type step
    assert seen["type"][0].startswith("⇄ Handoffs")  # a first-class option there
    assert seen["offers"] and "kickoff" in seen["offers"][0]  # labelled by its thread


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


# --- multiples detection: one holder per thread (Mickey 17 guard) ----------

def test_is_dissolved_true_after_origin_offer_is_taken(tmp_control_home):
    hq.offer(tmp_control_home, "pass-it", arc="t/02", project="p", seed="-",
             from_session="origin-A")
    hq.take(tmp_control_home, "pass-it", session="successor-B")
    rec = hq.is_dissolved(tmp_control_home, "origin-A")
    assert rec is not None and rec["taken_by"] == "successor-B"
    # the successor itself is NOT dissolved; an unrelated session isn't either
    assert hq.is_dissolved(tmp_control_home, "successor-B") is None
    assert hq.is_dissolved(tmp_control_home, "someone-else") is None


def test_is_dissolved_none_while_offer_only_offered(tmp_control_home):
    hq.offer(tmp_control_home, "pending", arc="t/02", project="p", seed="-",
             from_session="origin-A")
    # still merely offered (successor not live) → origin has NOT dissolved yet
    assert hq.is_dissolved(tmp_control_home, "origin-A") is None


def test_multiples_quiet_for_history_pairs(tmp_control_home):
    # canon №1 simplified (16.07): a handed-off origin merely EXISTING is open
    # history — multiples flags only an origin that PULSED after the take (see
    # test_dissolution.test_multiples_flags_origin_pulsing_after_take for the
    # positive case on a real rostered session)
    hq.offer(tmp_control_home, "one", arc="t/02", project="p", seed="-",
             from_session="A")
    hq.take(tmp_control_home, "one", session="B")
    hq.offer(tmp_control_home, "two", arc="t/03", project="p", seed="-")  # no origin
    hq.take(tmp_control_home, "two", session="C")
    assert hq.multiples(tmp_control_home) == []


def test_list_shows_origin_lineage_for_taken(tmp_control_home):
    hq.offer(tmp_control_home, "liney", arc="t/02", project="p", seed="-",
             from_session="A")
    hq.take(tmp_control_home, "liney", session="B")
    out = hq.render_list(tmp_control_home)
    assert "from A" in out and "by B" in out


# --- offer-target validation (fail-fast, cands 16/17) -----------------------

def _rostered_project(home, name="x", dirname="realproj"):
    """A scaffolded project dir rostered under *name*; returns its root."""
    from tide.arc import stream

    proj = home / dirname
    (proj / ".tide" / "arcs").mkdir(parents=True)
    (home / "roster.md").write_text(
        "# tide roster\n{0} | {1}\n".format(name, proj), encoding="utf-8"
    )
    return proj


def test_validate_target_rejects_unrostered_project(tmp_control_home):
    # cand 17: offered as the dev dir-name 'ai-hot', rostered as 'x' — must
    # refuse AT OFFER TIME, naming the valid roster names.
    _rostered_project(tmp_control_home, name="x")
    with pytest.raises(hq.HandoffError, match="not in roster.*x"):
        hq.validate_target(tmp_control_home, project="ai-hot", arc="-")


def test_validate_target_passes_rostered_project_without_arc(tmp_control_home):
    _rostered_project(tmp_control_home, name="x")
    hq.validate_target(tmp_control_home, project="x", arc="-")  # no raise


def test_validate_target_skips_blank_project_and_empty_roster(tmp_control_home):
    hq.validate_target(tmp_control_home, project=None, arc="whatever")
    hq.validate_target(tmp_control_home, project="-", arc="whatever")
    # roster.md holds no entries → nothing to enforce
    hq.validate_target(tmp_control_home, project="ghost", arc="-")


def test_validate_target_rejects_unresolvable_arc(tmp_control_home):
    # cand 16: an --arc that resolves to nothing was silently mapped onto the
    # ACTIVE session — the offer surfaced under a stranger thread.
    from tide.arc import stream

    proj = _rostered_project(tmp_control_home, name="x")
    stream.new_thread(proj, "understand")
    with pytest.raises(hq.HandoffError, match="does not resolve"):
        hq.validate_target(tmp_control_home, project="x", arc="99-@ghost/01-s")


def test_validate_target_accepts_thread_and_session_by_name_or_slug(tmp_control_home):
    from tide.arc import stream

    proj = _rostered_project(tmp_control_home, name="x")
    entry = stream.new_thread(proj, "redesign")
    sess = stream.new_session(proj, "redesign", "kickoff")
    hq.validate_target(tmp_control_home, project="x",
                       arc="{0}/{1}".format(entry.name, sess.name))  # dir names
    hq.validate_target(tmp_control_home, project="x", arc="redesign/kickoff")  # slugs
    hq.validate_target(tmp_control_home, project="x", arc=entry.name)  # thread only


def test_validate_target_rejects_missing_session_inside_thread(tmp_control_home):
    from tide.arc import stream

    proj = _rostered_project(tmp_control_home, name="x")
    entry = stream.new_thread(proj, "redesign")
    stream.new_session(proj, "redesign", "kickoff")
    with pytest.raises(hq.HandoffError, match="no session"):
        hq.validate_target(tmp_control_home, project="x",
                           arc="{0}/77-ghost".format(entry.name))


def test_cli_offer_fails_fast_on_bad_project(tmp_control_home, monkeypatch, capsys):
    _rostered_project(tmp_control_home, name="x")
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    rc = cli.main(["handoffs", "offer", "oops", "--project", "ai-hot", "--arc", "-"])
    assert rc == 1
    assert "not in roster" in capsys.readouterr().err
    assert hq.list_offers(tmp_control_home) == []  # nothing hung


def test_cli_offer_still_hangs_valid_offer(tmp_control_home, monkeypatch, capsys):
    from tide.arc import stream

    proj = _rostered_project(tmp_control_home, name="x")
    entry = stream.new_thread(proj, "redesign")
    sess = stream.new_session(proj, "redesign", "kickoff")
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    rc = cli.main(["handoffs", "offer", "kickoff", "--project", "x",
                   "--arc", "{0}/{1}".format(entry.name, sess.name)])
    assert rc == 0
    assert [r["slug"] for r in hq.list_offers(tmp_control_home)] == ["kickoff"]


def test_list_offers_newest_first(tmp_control_home):
    # cand 35: the fresh offer is the one being picked up NOW — top, not under
    # a scroll. House law: newest-first everywhere.
    hq.offer(tmp_control_home, "old", arc="-", project="p", seed="-")
    hq.offer(tmp_control_home, "mid", arc="-", project="p", seed="-")
    hq.offer(tmp_control_home, "new", arc="-", project="p", seed="-")
    assert [r["slug"] for r in hq.list_offers(tmp_control_home)] == ["new", "mid", "old"]
    out = hq.render_list(tmp_control_home)
    assert out.index("new") < out.index("mid") < out.index("old")


def test_drop_archives_seed_before_pruning(tmp_control_home, tmp_path):
    from pathlib import Path

    # cand 116 п.5: drop нетронутой сессии архивирует её сид (__seeds__/),
    # а не уносит карту входа молча вместе с каталогом
    sess = tmp_path / "proj" / ".tide" / "arcs" / "t" / "arcs" / "02-pickup"
    (sess / "input").mkdir(parents=True)
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# карта входа\n", encoding="utf-8")
    hq.offer(tmp_control_home, "drop-me", arc="t/02-pickup", project="p",
             seed=str(seed))
    key = hq.list_offers(tmp_control_home)[0]["name"]
    rec, pruned = hq.drop(tmp_control_home, key)
    assert pruned and not sess.exists()
    grave = Path(rec["path"]).parent / "__seeds__"
    saved = list(grave.glob("*-seed.md"))
    assert saved and "карта входа" in saved[0].read_text(encoding="utf-8")
