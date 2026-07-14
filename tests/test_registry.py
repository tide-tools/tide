"""tide.registry — the sid-keyed launch registry (terminals.json). cand 94."""

from __future__ import annotations

import json
from datetime import datetime

from tide import registry


def _read_raw(home):
    return json.loads((home / registry.REGISTRY_FILENAME).read_text(encoding="utf-8"))


# --- read / record ---------------------------------------------------------

def test_read_missing_is_empty(tmp_path):
    assert registry.read(tmp_path) == {}


def test_record_writes_sid_keyed_entry(tmp_path):
    registry.record(tmp_path, "sid-1", "term_abc", "/p/.tide/arcs/01-@t/arcs/01-s",
                    now=datetime(2026, 7, 13, 20, 0, 0))
    data = _read_raw(tmp_path)
    assert data == {
        "sid-1": {"handle": "term_abc", "arc": "/p/.tide/arcs/01-@t/arcs/01-s",
                  "ts": "2026-07-13T20:00:00"}
    }


def test_record_is_last_writer_wins_per_sid(tmp_path):
    registry.record(tmp_path, "sid-1", "term_old", "/a")
    registry.record(tmp_path, "sid-1", "term_new", "/a")
    assert registry.read(tmp_path)["sid-1"]["handle"] == "term_new"


def test_record_keeps_other_sids(tmp_path):
    registry.record(tmp_path, "sid-1", "term_a", "/a")
    registry.record(tmp_path, "sid-2", "term_b", "/b")
    assert set(registry.read(tmp_path)) == {"sid-1", "sid-2"}


def test_record_noop_on_empty_sid_or_handle(tmp_path):
    registry.record(tmp_path, "", "term_a", "/a")
    registry.record(tmp_path, "sid-1", "", "/a")
    assert registry.read(tmp_path) == {}


def test_forget_removes_and_is_idempotent(tmp_path):
    registry.record(tmp_path, "sid-1", "term_a", "/a")
    registry.forget(tmp_path, "sid-1")
    assert registry.read(tmp_path) == {}
    registry.forget(tmp_path, "sid-1")  # no error second time


# --- resolve (live-handle cross-check) -------------------------------------

def test_resolve_returns_handle_when_live(tmp_path):
    registry.record(tmp_path, "sid-1", "term_a", "/a")
    assert registry.resolve(tmp_path, "sid-1", live_handles={"term_a", "term_x"}) == "term_a"


def test_resolve_none_when_handle_dead(tmp_path):
    registry.record(tmp_path, "sid-1", "term_a", "/a")
    # term_a not in the live set → dead → caller resumes/sparks
    assert registry.resolve(tmp_path, "sid-1", live_handles={"term_x"}) is None


def test_resolve_none_for_unknown_sid(tmp_path):
    assert registry.resolve(tmp_path, "ghost", live_handles={"term_a"}) is None


def test_resolve_none_for_empty_sid(tmp_path):
    assert registry.resolve(tmp_path, "", live_handles={"term_a"}) is None


def test_two_sessions_of_a_thread_resolve_independently(tmp_path):
    # the cand 94 repro: one thread, two live sessions — each resolves to ITS OWN
    # terminal (an arc-keyed registry could not tell them apart → duplicate spawn).
    registry.record(tmp_path, "sid-verify", "term_1", "/t/arcs/01-verify")
    registry.record(tmp_path, "sid-run", "term_2", "/t/arcs/02-run")
    live = {"term_1", "term_2"}
    assert registry.resolve(tmp_path, "sid-verify", live_handles=live) == "term_1"
    assert registry.resolve(tmp_path, "sid-run", live_handles=live) == "term_2"


# --- prune -----------------------------------------------------------------

def test_prune_drops_dead_entries(tmp_path):
    registry.record(tmp_path, "sid-live", "term_a", "/a")
    registry.record(tmp_path, "sid-dead", "term_b", "/b")
    removed = registry.prune(tmp_path, live_handles={"term_a"})
    assert removed == 1
    assert set(registry.read(tmp_path)) == {"sid-live"}


def test_prune_noop_when_all_live(tmp_path):
    registry.record(tmp_path, "sid-1", "term_a", "/a")
    assert registry.prune(tmp_path, live_handles={"term_a"}) == 0


def test_prune_empty_registry(tmp_path):
    assert registry.prune(tmp_path, live_handles=set()) == 0


def test_prune_keeps_everything_when_live_set_empty(tmp_path):
    # An empty live-set may just mean orca failed to answer — never wipe the registry.
    registry.record(tmp_path, "sid-1", "term_a", "/a")
    registry.record(tmp_path, "sid-2", "term_b", "/b")
    assert registry.prune(tmp_path, live_handles=set()) == 0
    assert set(registry.read(tmp_path)) == {"sid-1", "sid-2"}
