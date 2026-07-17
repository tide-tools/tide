"""Canon №1 (упрощение 16.07) — the current session is derived from the chain:
take() stamps nothing; the real multiple is an origin that PULSED after the take."""

from __future__ import annotations

from pathlib import Path

from tide import fields, handoff_queue as hq, registry, roster
from tide.arc import stream
from tide.offload import _closure_word_warning


def _two_generations(tmp_path, mode=hq.DEFAULT_MODE):
    """A rostered project with an ORIGIN session (sid pinned) and its handoff offer."""
    home = tmp_path / "home"
    (home / ".tide" / "handoffs").mkdir(parents=True)
    proj = tmp_path / "proj"
    (proj / ".tide" / "arcs").mkdir(parents=True)
    roster.add(home, "proj", str(proj))
    stream.new_thread(proj, "demo", goal="ship it")
    origin = stream.new_session(proj, "demo", "origin")
    fields.set_field(origin / "arc.md", "claude-session", "origin-sid")
    registry.record(home, "origin-sid", "term_origin", str(origin))
    target = stream.new_session(proj, "demo", "pickup")
    seed = target / "input" / "handoff-seed.md"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text("# distil\n", encoding="utf-8")
    hq.offer(home, "launcher", arc="demo/pickup", project="proj",
             seed=str(seed), from_session="origin-sid", mode=mode)
    key = hq.list_offers(home)[0]["name"]
    return home, proj, origin, target, key


def test_take_leaves_origin_unstamped(tmp_path):
    # canon №1 simplified (Гриша 16.07): the current session is DERIVED from the
    # chain — taking an offer stamps NOTHING on the origin. The hook's hint stays
    # queue-derived (is_dissolved), the registry entry is KEPT for ⟳ focus.
    home, proj, origin, target, key = _two_generations(tmp_path)
    hq.take(home, key, session="successor-sid")
    assert fields.read_field(origin / "arc.md", "dissolved") is None
    assert hq.is_dissolved(home, "origin-sid") is not None  # queue-derived hint
    assert registry.recorded_handle(home, "origin-sid") == "term_origin"
    # the successor's link is untouched territory (recorded by its launcher)
    assert (fields.read_field(target / "arc.md", "claude-session") or "") == "successor-sid"


def test_confirm_flip_leaves_origin_unstamped_too(tmp_path):
    home, proj, origin, target, key = _two_generations(tmp_path)
    hq.reserve(home, key, session="successor-sid")
    assert hq.confirm_for_session(home, "successor-sid")
    assert fields.read_field(origin / "arc.md", "dissolved") is None
    assert hq.is_dissolved(home, "origin-sid") is not None


def test_multiples_flags_origin_pulsing_after_take(tmp_path):
    # the real Mickey 17: the origin kept WORKING (offloaded-at) after the take
    home, proj, origin, target, key = _two_generations(tmp_path)
    hq.take(home, key, session="successor-sid")
    assert hq.multiples(home) == []  # quiet origin = open history, not a multiple
    fields.set_field(origin / "arc.md", "offloaded-at", "2099-01-01T00:00:00")
    assert len(hq.multiples(home)) == 1


def test_new_mode_take_keeps_origin_holding(tmp_path):
    # live 16.07 (offer 124-work-start): a mode:new offer seeds a DIFFERENT thread —
    # the origin never gave ITS thread away, so taking must not dissolve it, the
    # Mickey-17 pinch must stay silent, and the multiples detector must skip it.
    home, proj, origin, target, key = _two_generations(tmp_path, mode="new")
    hq.take(home, key, session="successor-sid")
    assert fields.read_field(origin / "arc.md", "dissolved") is None
    assert hq.is_dissolved(home, "origin-sid") is None
    assert hq.multiples(home) == []


def test_self_handoff_never_self_dissolves(tmp_path):
    # continue-in-place: the taker IS the origin (same sid) — no dissolution
    home, proj, origin, target, key = _two_generations(tmp_path)
    hq.take(home, key, session="origin-sid")
    assert fields.read_field(origin / "arc.md", "dissolved") is None
    assert registry.recorded_handle(home, "origin-sid") == "term_origin"


# --- cand 106: the closure-word detector needs an OBJECT, not a bare verb -----------

def _warn(tmp_path, text):
    stream.new_thread(tmp_path, "demo", goal="ship")
    s = stream.new_session(tmp_path, "demo", "s1")
    return _closure_word_warning(s / "arc.md", text)


def test_closure_warning_needs_object(tmp_project):
    # «старт-гейт закрыт» — про гейт, не про нить: детектор молчит (cand 106)
    assert _warn(tmp_project, "старт-гейт закрыт полностью, работаю дальше") is None


def test_closure_warning_fires_on_thread_object(tmp_project):
    assert _warn(tmp_project, "нить закрыта, итог в output") is not None


def test_closure_warning_fires_verb_first(tmp_project):
    assert _warn(tmp_project, "закрыл нить 06 руками") is not None


def test_closure_warning_ignores_intent(tmp_project):
    # future intent is not a claim — no false nag that trains dishonest pulses
    assert _warn(tmp_project, "закрою нить завтра после гейта") is None


def test_dissolve_keeps_registry_entry(tmp_path):
    # live 14.07: forgetting the origin's handle made ⟳ respawn a DUPLICATE tab —
    # the tab is usually still open, focus must keep working; only respawn is gated
    home, proj, origin, target, key = _two_generations(tmp_path)
    hq.take(home, key, session="successor-sid")
    assert registry.recorded_handle(home, "origin-sid") == "term_origin"
