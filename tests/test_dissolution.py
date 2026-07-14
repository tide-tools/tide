"""I6 — dissolution as mechanics: take() stamps the origin, no pulse-word heuristics."""

from __future__ import annotations

from pathlib import Path

from tide import fields, handoff_queue as hq, registry, roster
from tide.arc import stream
from tide.offload import _closure_word_warning


def _two_generations(tmp_path):
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
             seed=str(seed), from_session="origin-sid")
    key = hq.list_offers(home)[0]["name"]
    return home, proj, origin, target, key


def test_take_dissolves_origin_mechanically(tmp_path):
    home, proj, origin, target, key = _two_generations(tmp_path)
    hq.take(home, key, session="successor-sid")
    # I6: the origin's passport is stamped — the harness dissolves the origin, not
    # the agent's discipline. Its registry entry is KEPT (live 14.07: the tab is
    # usually still open, ⟳ must focus it; only the RESPAWN is gated, in return_cmd).
    assert (fields.read_field(origin / "arc.md", "dissolved") or "").strip()
    assert registry.recorded_handle(home, "origin-sid") == "term_origin"
    # the successor's link is untouched territory (recorded by its launcher)
    assert (fields.read_field(target / "arc.md", "claude-session") or "") == "successor-sid"


def test_confirm_flip_dissolves_origin_too(tmp_path):
    home, proj, origin, target, key = _two_generations(tmp_path)
    hq.reserve(home, key, session="successor-sid")
    assert hq.confirm_for_session(home, "successor-sid")
    assert (fields.read_field(origin / "arc.md", "dissolved") or "").strip()


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
