"""Unit tests for tide.ledger — the deferred-reconciliation debt ledger."""

from __future__ import annotations

from tide import ledger, paths


def test_empty_ledger_reads_as_no_entries(tmp_project):
    assert ledger.entries(tmp_project) == []
    assert ledger.count(tmp_project) == 0
    assert not paths.deferred_file(tmp_project).exists()


def test_append_writes_a_parseable_entry(tmp_project):
    ledger.append(tmp_project, "__03-fix-leak__", ["delta", "report", "proof"], "abc123def456")
    items = ledger.entries(tmp_project)
    assert len(items) == 1
    e = items[0]
    assert e.arc == "__03-fix-leak__"
    assert e.deferred == ["delta", "report", "proof"]
    assert e.canon_rev == "abc123def456"
    assert e.ref == "fix-leak"  # markers stripped for resolution


def test_append_round_trips_through_the_file(tmp_project):
    ledger.append(tmp_project, "__01-a__", ["report"], "rev1")
    ledger.append(tmp_project, "__02-b__", ["delta", "proof"], "rev2")
    # Fresh read off disk (no in-memory state).
    items = ledger.entries(tmp_project)
    assert [e.arc for e in items] == ["__01-a__", "__02-b__"]
    assert [e.deferred for e in items] == [["report"], ["delta", "proof"]]


def test_append_is_idempotent_per_arc(tmp_project):
    ledger.append(tmp_project, "__01-a__", ["report"], "rev1")
    ledger.append(tmp_project, "__01-a__", ["delta", "report"], "rev2")  # re-land
    items = ledger.entries(tmp_project)
    assert len(items) == 1  # replaced, not duplicated
    assert items[0].deferred == ["delta", "report"]
    assert items[0].canon_rev == "rev2"


def test_find_resolves_by_bare_slug(tmp_project):
    ledger.append(tmp_project, "__07-thing__", ["proof"], "rev")
    assert ledger.find(tmp_project, "thing").arc == "__07-thing__"
    assert ledger.find(tmp_project, "__07-thing__").arc == "__07-thing__"
    assert ledger.find(tmp_project, "absent") is None


def test_remove_drops_the_line_and_deletes_file_when_empty(tmp_project):
    ledger.append(tmp_project, "__01-a__", ["report"], "rev1")
    assert ledger.remove(tmp_project, "a") is True
    assert ledger.count(tmp_project) == 0
    # Fully-paid ledger removes the file (no stale empty header lingering).
    assert not paths.deferred_file(tmp_project).exists()


def test_remove_absent_is_a_no_op(tmp_project):
    ledger.append(tmp_project, "__01-a__", ["report"], "rev1")
    assert ledger.remove(tmp_project, "absent") is False
    assert ledger.count(tmp_project) == 1


def test_ledger_file_is_human_readable_with_catch_up_hint(tmp_project):
    ledger.append(tmp_project, "__01-a__", ["delta"], "rev1")
    text = paths.deferred_file(tmp_project).read_text(encoding="utf-8")
    assert "tide reconcile" in text
    assert "- arc: __01-a__" in text
