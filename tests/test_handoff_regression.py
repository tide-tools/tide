"""Handoff RECEPTION-SEAM regression — the synthetic end-to-end guard.

One place that walks the WHOLE handoff cycle (offer → pickup) and asserts a single
invariant — THE SEAM IS CLOSED — for EVERY path a thread can be received through.
It exists so a future change can't silently reopen the half-open seam that cands 76
(seed protocol read as an approval-gated plan) and 77 (``take`` flipped the registry
but never stamped the passport) fixed.

The invariant, after any successful take, whatever the entry path:
  1. registry: the offer is ``taken``;
  2. passport pins ``claude-session`` (⟳ resume works) AND it equals the registry's
     ``taken-by`` (registry and passport agree who holds the thread);
  3. passport ``status: active`` (the thread is live, not merely offered);
  4. a first pulse landed — ``## context`` carries "нить принята" and ``offloaded-at``
     is stamped — so the board reads the session as ALIVE, not "⌛ передача ждёт · ▶".

Run just this guard:  ``uv run pytest tests/test_handoff_regression.py``
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from tide import cli, fields, handoff_queue as hq, roster, slug
from tide.arc import stream
from tide.init_home import scaffold_project
from tide.launcher import handoff as lh, menu


class _OkAdapter:
    """A stub terminal adapter whose spawn always 'succeeds' (opens no window)."""

    def spawn(self, *, command, cwd, title, dry_run=False):
        from tide.adapters import SpawnResult
        return SpawnResult(ok=True, ref="stub", commands=[command])


def _build_offer(home):
    """A scaffolded project + thread/session + a real seeded offer. Returns the
    session dir and the offer key — the fixture every path in this file receives."""
    proj = home / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    roster.add(home, "proj", str(proj))
    entry = stream.new_thread(proj, "redesign")
    sess = stream.new_session(proj, "redesign", "kickoff")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# seed\n\nделай следующий шаг\n", encoding="utf-8")
    hq.offer(home, "kickoff", project="proj", seed=str(seed),
             arc="{0}/{1}".format(entry.name, sess.name))
    return sess, "kickoff"


def _assert_seam_closed(home, session_dir):
    """THE invariant — every reception path must leave exactly this state."""
    passport = session_dir / "arc.md"
    rec = hq.list_offers(home)[0]

    # 1. registry flipped
    assert rec["status"] == hq.STATUS_TAKEN, "offer never flipped to taken"

    # 2. session pinned, and registry ⇄ passport agree on the holder
    sid = (fields.read_field(passport, "claude-session") or "").strip()
    assert sid and sid != "-", "no claude-session pinned → no ⟳ resume button"
    assert rec["taken_by"] == sid, "registry taken-by ≠ passport claude-session"

    # 3. thread is live
    assert fields.read_field(passport, "status") == "active", "passport not marked active"

    # 4. first pulse landed → board sees it alive
    text = passport.read_text(encoding="utf-8")
    assert "нить принята" in text, "no first pulse in ## context"
    assert fields.read_field(passport, "offloaded-at") not in (None, "0", ""), \
        "offloaded-at not stamped → board still reads the session as a stub"


# --- one test per reception PATH; all assert the same seam invariant ---------

def test_seam_closed_via_cli_take(tmp_control_home, monkeypatch):
    """Path: manual ``tide handoffs take`` / board ▶ / ``tide arc open`` (cand 77 repro)."""
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    sess, key = _build_offer(tmp_control_home)
    rc = cli.main(["handoffs", "take", key, "--session", "claude-cli"])
    assert rc == 0
    _assert_seam_closed(tmp_control_home, sess)


def test_seam_closed_via_confirm_hook(tmp_control_home, monkeypatch):
    """Path: the picked-up session's FIRST message (UserPromptSubmit hook)."""
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    sess, key = _build_offer(tmp_control_home)
    proj = tmp_control_home / "proj"
    monkeypatch.chdir(proj)  # the hook gates on find_tide_root(cwd)
    hq.reserve(tmp_control_home, key, session="claude-hook")  # menu pins the taker
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id": "claude-hook"}'))
    rc = cli.main(["hook", "handoff-confirm"])
    assert rc == 0
    _assert_seam_closed(tmp_control_home, sess)


def test_seam_closed_via_menu_pickup(tmp_control_home):
    """Path: ``tide menu → Pick up`` (launch_handoff — the cand 76 path).

    Signed A (14.07): the seam closes in two beats — the launch reserves the offer
    for the minted sid (status stays offered: a dead-on-arrival terminal must not
    eat it), and the session's FIRST message flips it (confirm_for_session, the
    UserPromptSubmit hook's core). Both beats are mechanics, neither is the agent's.
    """
    sess, _key = _build_offer(tmp_control_home)
    record = hq.list_offers(tmp_control_home)[0]
    res = menu.launch_handoff(record, menu.list_entries(tmp_control_home),
                              control_home=tmp_control_home, adapter=_OkAdapter())
    assert res.ok
    reserved = hq.list_offers(tmp_control_home)[0]
    assert reserved["status"] == hq.STATUS_OFFERED
    sid = reserved["pickup_session"]
    assert sid and hq.confirm_for_session(tmp_control_home, sid)
    _assert_seam_closed(tmp_control_home, sess)


# --- and the negative guard: a failed pickup must NOT consume the offer -------

def test_failed_pickup_keeps_offer_and_seam_open(tmp_control_home):
    """The two-stage guarantee: a spawn that fails leaves the offer recoverable."""
    from tide.adapters import SpawnResult

    class _FailAdapter:
        def spawn(self, *, command, cwd, title, dry_run=False):
            return SpawnResult(ok=False, detail="no terminal", commands=[command])

    sess, _key = _build_offer(tmp_control_home)
    record = hq.list_offers(tmp_control_home)[0]
    res = menu.launch_handoff(record, menu.list_entries(tmp_control_home),
                              control_home=tmp_control_home, adapter=_FailAdapter())
    assert not res.ok
    assert hq.list_offers(tmp_control_home)[0]["status"] == hq.STATUS_OFFERED
    # No RECEPTION happened: the seam-closing act is the first pulse (``take`` runs it
    # only on a successful launch). Passport-born fields (status: active, a pre-pinned
    # claude-session) aren't the signal — the pulse is: no "нить принята", offloaded-at
    # still 0. That's what keeps the board from painting a failed pickup as live.
    passport = sess / "arc.md"
    assert "нить принята" not in passport.read_text(encoding="utf-8")
    assert fields.read_field(passport, "offloaded-at") in (None, "0", "")


# --- one session ahead: the IDEA must survive a CHAIN of handoffs (A→B→C) -----

def test_idea_survives_a_handoff_chain(tmp_control_home, monkeypatch):
    """Across A→B→C the thread's throughline reaches EVERY session (not just next-step),
    lineage stays reconstructable, and exactly one holder is live (no Mickey-17).

    Guards the deeper leak a 2-hop probe found: the reception seam was mechanical, but
    the IDEA was still delivered only by the offering agent's discipline — so over a
    chain each session saw only its local step and the original goal eroded. Now
    ``run_handoff`` stamps the throughline into every thread seed.
    """
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    proj = tmp_control_home / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    roster.add(tmp_control_home, "proj", str(proj))

    GOAL = "довести большую идею X до релиза"
    stream.new_thread(proj, "big-idea", goal=GOAL)
    start = stream.new_session(proj, "big-idea", "start")
    fields.set_field(start / "arc.md", "claude-session", "sid-A")

    prev_id, lineage = "sid-A", []
    for hop, next_id in enumerate(["sid-B", "sid-C"], start=1):
        res = lh.run_handoff(
            proj, arc_ref="big-idea", mode="continue",
            summary="# seed\n\nследующий шаг: доделать кусок {0}\n".format(hop),
            from_session=prev_id,
        )
        offer = hq.list_offers(tmp_control_home, status="offered")[0]
        # (1) throughline delivered: the seed carries the thread goal, not just the step
        assert GOAL in Path(offer["seed"]).read_text(encoding="utf-8"), \
            "seed dropped the throughline — the idea leaks across the chain"
        hq.take(tmp_control_home, offer["name"], session=next_id)
        sess_name = offer["arc"].split("/")[-1]
        born = next(s for s in (proj / ".tide" / "arcs").rglob(sess_name) if (s / "arc.md").is_file())
        lineage.append((sess_name, fields.read_field(born / "arc.md", "from")))
        prev_id = next_id

    # (2) lineage reconstructable: each pickup chains to its predecessor. ``from:``
    # stores the predecessor's bare slug (entry_slug), not its numbered dir name.
    assert lineage[0][1] == "start", "chain broke: B.from ≠ A"
    assert lineage[1][1] == slug.entry_slug(lineage[0][0]), "chain broke: C.from ≠ B"
    # (3) one holder: both past origins dissolved, only the last is live (no Mickey-17)
    assert hq.is_dissolved(tmp_control_home, "sid-A")
    assert hq.is_dissolved(tmp_control_home, "sid-B")
    assert not hq.is_dissolved(tmp_control_home, "sid-C")
    assert {m["from_session"] for m in hq.multiples(tmp_control_home)} == {"sid-A", "sid-B"}
    # (4) the goal itself never left the thread
    assert fields.read_field(stream.passport_path(
        next((proj / ".tide" / "arcs").glob("*big-idea*"))), "goal") == GOAL


def test_pulse_lands_on_the_right_pickup_when_slugs_collide(tmp_control_home):
    """Two sessions share the slug ``pickup``; taking the SECOND must pulse the SECOND.

    Regression for a bug a live 6-hop dogfood caught (cand 78): the seam pulse resolved
    the session by slug, and every handoff pickup is ``NN-pickup`` → entry_slug ``pickup``,
    so the lookup hit the FIRST sibling. A fresh pickup's "нить принята" landed on an
    OLDER session while the real one still read as a stub (board would invite a duplicate ▶).
    """
    proj = tmp_control_home / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    roster.add(tmp_control_home, "proj", str(proj))
    stream.new_thread(proj, "big-idea", goal="ship the idea")
    first = stream.new_session(proj, "big-idea", "pickup")   # 01-pickup
    second = stream.new_session(proj, "big-idea", "pickup")  # 02-pickup — same slug
    assert slug.entry_slug(first.name) == slug.entry_slug(second.name) == "pickup"

    seed = second / "input" / "handoff-seed.md"
    seed.write_text("# seed\n\nделай шаг\n", encoding="utf-8")
    hq.offer(tmp_control_home, "second", project="proj", seed=str(seed),
             arc="big-idea/{0}".format(second.name))
    hq.take(tmp_control_home, "second", session="sid-2")

    # the SECOND (taken) session got the pulse; the first sibling was left untouched
    assert "нить принята" in (second / "arc.md").read_text(encoding="utf-8")
    assert fields.read_field(second / "arc.md", "offloaded-at") not in (None, "0", "")
    assert "нить принята" not in (first / "arc.md").read_text(encoding="utf-8")
    assert fields.read_field(first / "arc.md", "offloaded-at") in (None, "0", "")
