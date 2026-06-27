"""P2-3 Durability tests: atomic writes, merge idempotency, lock, frontmatter whitelist.

Covers the DURABILITY FOUNDATION tasks:
  - atomic_write: byte-correctness, parent creation, failure isolation (no .tmp litter)
  - file_lock: sequential acquire/release, stale-lock reclaim, exception safety
  - merge idempotency: journal-stamp is the single source of truth; re-run is no-op
  - merge crash simulation: canon written but mark_merged not called → re-run safe
  - ledger atomicity: failed write does not destroy existing entries
  - frontmatter whitelist: body keys (TODO:, NOTE:) not parsed; real keys still parse

All tests are independent; no shared mutable state.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest import mock

import pytest

from tide import fields, ledger, paths
from tide.io import atomic_write, file_lock
from tide.cannon import merge


# ---------------------------------------------------------------------------
# 1. atomic_write — unit
# ---------------------------------------------------------------------------


def test_atomic_write_byte_correct(tmp_path: Path) -> None:
    """Written content is byte-for-byte identical to the input text."""
    p = tmp_path / "out.txt"
    atomic_write(p, "hello world")
    assert p.read_text(encoding="utf-8") == "hello world"


def test_atomic_write_unicode_roundtrip(tmp_path: Path) -> None:
    """Unicode text (non-ASCII) survives the write cycle unchanged."""
    p = tmp_path / "unicode.txt"
    text = "status: проверка\ngoal: 中文 content\n"
    atomic_write(p, text)
    assert p.read_text(encoding="utf-8") == text


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    """Parent directories are created when they do not exist."""
    p = tmp_path / "deep" / "nested" / "out.txt"
    atomic_write(p, "content")
    assert p.read_text(encoding="utf-8") == "content"


def test_atomic_write_overwrites_existing_file(tmp_path: Path) -> None:
    """An existing file is replaced atomically with the new content."""
    p = tmp_path / "f.txt"
    p.write_text("old", encoding="utf-8")
    atomic_write(p, "new")
    assert p.read_text(encoding="utf-8") == "new"


def test_atomic_write_failure_leaves_original_intact(tmp_path: Path, monkeypatch) -> None:
    """If os.replace raises, the original file is preserved; no .tmp litter."""
    p = tmp_path / "state.txt"
    p.write_text("original", encoding="utf-8")

    def exploding_replace(src: str, dst: str) -> None:
        raise OSError("simulated disk full")

    monkeypatch.setattr(os, "replace", exploding_replace)

    with pytest.raises(OSError, match="simulated disk full"):
        atomic_write(p, "new content")

    assert p.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_failure_on_new_file_leaves_no_tmp(tmp_path: Path, monkeypatch) -> None:
    """If os.replace raises when writing a new file, no .tmp is left behind."""
    p = tmp_path / "new.txt"

    def exploding_replace(src: str, dst: str) -> None:
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", exploding_replace)

    with pytest.raises(OSError):
        atomic_write(p, "content")

    assert not p.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_temp_in_same_dir(tmp_path: Path) -> None:
    """Temp file lives in path.parent (same filesystem — safe for os.replace)."""
    sub = tmp_path / "sub"
    sub.mkdir()
    p = sub / "out.txt"

    captured: list[Path] = []
    real_replace = os.replace

    def capturing_replace(src: str, dst: str) -> None:
        captured.append(Path(src))
        real_replace(src, dst)

    with mock.patch("os.replace", capturing_replace):
        atomic_write(p, "hi")

    assert len(captured) == 1
    assert captured[0].parent == sub, "temp must be in path.parent, not /tmp or elsewhere"


def test_atomic_write_empty_string(tmp_path: Path) -> None:
    """Writing an empty string is valid and produces an empty file."""
    p = tmp_path / "empty.txt"
    atomic_write(p, "")
    assert p.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# 2. file_lock — unit
# ---------------------------------------------------------------------------


def test_file_lock_acquires_and_releases(tmp_path: Path) -> None:
    """Lock dir exists while held and is gone after the context exits."""
    lock_dir = tmp_path / ".tide" / "state" / ".merge.lock"

    with file_lock(lock_dir):
        assert lock_dir.is_dir()

    assert not lock_dir.exists()


def test_file_lock_sequential_reuse(tmp_path: Path) -> None:
    """A lock can be acquired again after it has been released."""
    lock_dir = tmp_path / "lock"

    with file_lock(lock_dir):
        pass

    assert not lock_dir.exists()

    with file_lock(lock_dir):
        assert lock_dir.is_dir()

    assert not lock_dir.exists()


def test_file_lock_releases_on_exception(tmp_path: Path) -> None:
    """Lock is released even when the guarded body raises."""
    lock_dir = tmp_path / "lock"

    with pytest.raises(ValueError, match="body failure"):
        with file_lock(lock_dir):
            raise ValueError("body failure")

    assert not lock_dir.exists()


def test_file_lock_stale_reclaim_dead_pid(tmp_path: Path) -> None:
    """A stale lock whose pid is dead and timestamp is old is reclaimed."""
    lock_dir = tmp_path / "lock"
    lock_dir.mkdir()
    stale_info = {
        "pid": 99999999,          # impossible pid — definitely not alive
        "created": time.time() - 9999,  # far in the past
    }
    (lock_dir / "lock.json").write_text(json.dumps(stale_info), encoding="utf-8")

    with file_lock(lock_dir, ttl=30.0):
        assert lock_dir.is_dir()

    assert not lock_dir.exists()


def test_file_lock_stale_reclaim_old_timestamp(tmp_path: Path) -> None:
    """A stale lock with our own pid but an ancient timestamp is reclaimed."""
    lock_dir = tmp_path / "lock"
    lock_dir.mkdir()
    stale_info = {
        "pid": os.getpid(),        # our pid — normally live, but TTL exceeded
        "created": time.time() - 9999,
    }
    (lock_dir / "lock.json").write_text(json.dumps(stale_info), encoding="utf-8")

    with file_lock(lock_dir, ttl=30.0):
        assert lock_dir.is_dir()

    assert not lock_dir.exists()


def test_file_lock_creates_parent_dirs(tmp_path: Path) -> None:
    """file_lock creates the parent directory of the lock dir if absent."""
    lock_dir = tmp_path / "deeply" / "nested" / ".merge.lock"
    assert not lock_dir.parent.exists()

    with file_lock(lock_dir):
        assert lock_dir.is_dir()


# ---------------------------------------------------------------------------
# 3. Merge idempotency — text level (pure)
# ---------------------------------------------------------------------------


def test_merge_idempotency_text_level_same_body() -> None:
    """merge_delta_text called twice with the same (date, slug) is a no-op."""
    canon = "# CANON.md — demo\n\n## Cannon journal\n"
    after_first = merge.merge_delta_text(
        canon, "some change", date="2026-06-27", slug="my-arc"
    )
    after_second = merge.merge_delta_text(
        after_first, "some change", date="2026-06-27", slug="my-arc"
    )

    assert after_first == after_second
    stamps = [l for l in after_first.splitlines() if "### 2026-06-27 · my-arc" in l]
    assert len(stamps) == 1, "exactly one stamp — no double-append"


def test_merge_idempotency_text_level_different_body() -> None:
    """Stamp is the key: different body but same slug/date → still a no-op."""
    canon = "# CANON.md — demo\n\n## Cannon journal\n"
    after_first = merge.merge_delta_text(
        canon, "body one", date="2026-06-27", slug="my-arc"
    )
    after_second = merge.merge_delta_text(
        after_first, "body two", date="2026-06-27", slug="my-arc"
    )

    assert after_first == after_second


def test_merge_idempotency_distinct_slugs_append() -> None:
    """Merging two distinct slugs on the same date produces two entries."""
    canon = "# CANON.md — demo\n\n## Cannon journal\n"
    after_first = merge.merge_delta_text(
        canon, "work A", date="2026-06-27", slug="arc-a"
    )
    after_second = merge.merge_delta_text(
        after_first, "work B", date="2026-06-27", slug="arc-b"
    )

    assert "### 2026-06-27 · arc-a" in after_second
    assert "### 2026-06-27 · arc-b" in after_second


# ---------------------------------------------------------------------------
# 4. Merge crash simulation — file level
# ---------------------------------------------------------------------------


def test_merge_crash_simulation(tmp_project: Path) -> None:
    """Canon written with stamp but mark_merged not called → re-run is a no-op.

    Simulates the crash window: the process writes the new CANON.md successfully
    (stamp is in the journal) but dies before mark_merged() stamps delta.md as
    merged. The next run must not double-append.
    """
    arc_dir = tmp_project / ".tide" / "arcs" / "01-fix"
    arc_dir.mkdir(parents=True)
    delta = arc_dir / "delta.md"
    delta.write_text("# delta — fix\nmerged: no\n\nsome delta content\n", encoding="utf-8")

    # Step 1: simulate the canon write succeeding but mark_merged crashing.
    delta_body = merge._delta_body(delta.read_text(encoding="utf-8"))
    canon_path = paths.canon_file(tmp_project)
    canon_text = canon_path.read_text(encoding="utf-8")
    merged_text = merge.merge_delta_text(
        canon_text, delta_body, date="2026-06-27", slug="fix"
    )
    canon_path.write_text(merged_text, encoding="utf-8")
    # delta.md still has "merged: no" (mark_merged never ran)

    # Step 2: re-run the full merge_delta (as a retry would do).
    merge.merge_delta(tmp_project, arc_dir, slug="fix", date="2026-06-27")

    result = paths.canon_file(tmp_project).read_text(encoding="utf-8")
    stamps = [l for l in result.splitlines() if "### 2026-06-27 · fix" in l]
    assert len(stamps) == 1, "journal must not be doubled after crash-retry"


# ---------------------------------------------------------------------------
# 5. Ledger atomicity
# ---------------------------------------------------------------------------


def test_ledger_failed_write_preserves_entries(tmp_project: Path, monkeypatch) -> None:
    """A truncated/failed _write does not destroy existing ledger entries.

    After atomic_write is swapped in, a failure at os.replace leaves the
    original file intact. Verified by patching os.replace to raise.
    """
    ledger.append(tmp_project, "__01-a__", ["delta"], "rev1")
    ledger.append(tmp_project, "__02-b__", ["report"], "rev2")

    deferred = paths.deferred_file(tmp_project)
    original_text = deferred.read_text(encoding="utf-8")

    # Simulate disk failure at the final atomic rename step.
    def exploding_replace(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", exploding_replace)

    with pytest.raises(OSError, match="disk full"):
        ledger.append(tmp_project, "__03-c__", ["proof"], "rev3")

    assert deferred.read_text(encoding="utf-8") == original_text, (
        "existing ledger entries must survive a failed write"
    )


def test_ledger_write_creates_parent_and_survives_round_trip(tmp_project: Path) -> None:
    """Ledger is round-trippable after atomic_write is the write backend."""
    ledger.append(tmp_project, "__05-thing__", ["delta", "report"], "abcdef")
    entries = ledger.entries(tmp_project)
    assert len(entries) == 1
    assert entries[0].arc == "__05-thing__"
    assert entries[0].deferred == ["delta", "report"]
    assert entries[0].cannon_rev == "abcdef"


# ---------------------------------------------------------------------------
# 6. Frontmatter key whitelist
# ---------------------------------------------------------------------------


def test_whitelist_body_todo_not_parsed(tmp_path: Path) -> None:
    """A body line 'TODO: fix this' must NOT be read as a frontmatter field."""
    doc = "# arc\nstatus: active\n\n## body\nTODO: fix this\n"
    assert fields.read_field_text(doc, "TODO") is None
    assert fields.read_field_text(doc, "todo") is None


def test_whitelist_unknown_capitalized_key_not_parsed() -> None:
    """Single-word keys not in KNOWN_KEYS are rejected, even without spaces."""
    doc = "# arc\nstatus: active\n\nNOTE: this is important\n"
    assert fields.read_field_text(doc, "NOTE") is None


def test_whitelist_unknown_key_after_body_heading() -> None:
    """An unknown key appearing after a ## heading is not a frontmatter field."""
    doc = "# arc\nstatus: active\n\n## section\nFIXME: do it\n"
    assert fields.read_field_text(doc, "FIXME") is None


def test_whitelist_known_keys_all_parse() -> None:
    """Every key in KNOWN_KEYS round-trips via read_field_text (full coverage).

    Updated to include 'criteria', 'prev', and all orca-* keys so that every
    member of KNOWN_KEYS is exercised — the LOW coverage gap identified by the
    durability critic.
    """
    doc = (
        "# arc\n"
        "status: active\n"
        "cannon-rev: abc123\n"
        "reality-rev: def456\n"
        "merged: no\n"
        "goal: finish it\n"
        "criteria: all tests green\n"
        "supersedes: old-arc\n"
        "prev: __old-arc__\n"
        "project: myproj\n"
        "sign: human @ 2026-06-27\n"
        "accepted: no\n"
        "from: orchestrator\n"
        "state: open\n"
        "worktree-branch: feat/x\n"
        "deferred: delta\n"
        "mode: worker\n"
        "slug: c-07\n"
        "orca-issue: 42\n"
        "orca-workspace: /ws\n"
        "orca-base-branch: main\n"
    )
    expected = {
        "status": "active",
        "cannon-rev": "abc123",
        "reality-rev": "def456",
        "merged": "no",
        "goal": "finish it",
        "criteria": "all tests green",
        "supersedes": "old-arc",
        # prev: is an alias; read as "supersedes" via _match_keys
        "project": "myproj",
        "sign": "human @ 2026-06-27",
        "accepted": "no",
        "from": "orchestrator",
        "state": "open",
        "worktree-branch": "feat/x",
        "deferred": "delta",
        "mode": "worker",
        "slug": "c-07",
        "orca-issue": "42",
        "orca-workspace": "/ws",
        "orca-base-branch": "main",
    }
    for key, val in expected.items():
        assert fields.read_field_text(doc, key) == val, "key {!r} should parse".format(key)


def test_whitelist_prev_alias_still_works() -> None:
    """The prev: alias for supersedes: is preserved after whitelisting."""
    doc = "# arc\nstatus: active\nprev: old-arc\n"
    assert fields.read_field_text(doc, "supersedes") == "old-arc"
    assert fields.read_field_text(doc, "prev") == "old-arc"


def test_whitelist_set_field_not_confused_by_body_unknown_key() -> None:
    """set_field_text must not treat a body 'TODO:' line as an existing field."""
    doc = "# arc\nstatus: active\n\n## notes\nTODO: something\n"
    result = fields.set_field_text(doc, "goal", "do the thing")
    assert "goal: do the thing" in result
    assert "TODO: something" in result


def test_whitelist_orca_fields_parse() -> None:
    """orca-* field names that are in the whitelist parse correctly."""
    doc = "# arc\norca-issue: 42\norca-workspace: /path/to/ws\norca-base-branch: main\n"
    assert fields.read_field_text(doc, "orca-issue") == "42"
    assert fields.read_field_text(doc, "orca-workspace") == "/path/to/ws"
    assert fields.read_field_text(doc, "orca-base-branch") == "main"


def test_whitelist_known_keys_includes_criteria() -> None:
    """'criteria' must be in the whitelist and parse correctly (LOW coverage fix)."""
    doc = "# contract\nslug: c-07\ncriteria: all tests green and coverage >= 80%\n"
    assert fields.read_field_text(doc, "criteria") == "all tests green and coverage >= 80%"


def test_whitelist_prev_reads_as_known_key() -> None:
    """'prev' (supersedes alias) is explicitly in KNOWN_KEYS and parses."""
    from tide.fields import KNOWN_KEYS
    assert "prev" in KNOWN_KEYS
    doc = "# arc\nprev: __old-arc__\n"
    assert fields.read_field_text(doc, "prev") == "old-arc"


# ---------------------------------------------------------------------------
# 7. CRITICAL — file_lock stale-reclaim TOCTOU fix
# ---------------------------------------------------------------------------


def test_file_lock_stale_reclaim_uses_atomic_rename(tmp_path: Path, monkeypatch) -> None:
    """Stale reclaim uses os.rename (atomic CAS), not direct shutil.rmtree on lock_dir.

    With the old code (shutil.rmtree directly on lock_dir), this test fails because
    os.rename is never called. With the fix, os.rename is called first so only one
    racer can claim the stale lock.
    """
    lock_dir = tmp_path / "lock"
    lock_dir.mkdir()
    stale_info = {"pid": 99999999, "created": time.time() - 9999}
    (lock_dir / "lock.json").write_text(json.dumps(stale_info), encoding="utf-8")

    rename_srcs: list[Path] = []
    real_rename = os.rename

    def tracking_rename(src: str, dst: str) -> None:
        rename_srcs.append(Path(src))
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", tracking_rename)

    with file_lock(lock_dir, ttl=30.0):
        assert lock_dir.is_dir()

    assert rename_srcs, "stale reclaim MUST use os.rename (atomic claim) before rmtree"
    assert rename_srcs[0] == lock_dir, "rename source must be the live lock_dir"


def test_file_lock_stale_reclaim_rename_race_loss_retries(tmp_path: Path, monkeypatch) -> None:
    """When os.rename fails (race lost to another reclaimer), the loop retries and acquires.

    Simulates: two reclaimers both see stale; one wins the rename (clearing it),
    then releases; the loser gets FileNotFoundError from rename, continues the loop,
    and eventually acquires via mkdir (the winner already released).
    """
    import shutil as _shutil
    from tide.io import file_lock as _fl

    lock_dir = tmp_path / "lock"
    lock_dir.mkdir()
    stale_info = {"pid": 99999999, "created": time.time() - 9999}
    (lock_dir / "lock.json").write_text(json.dumps(stale_info), encoding="utf-8")

    real_rename = os.rename
    call_count = [0]

    def race_losing_rename(src: str, dst: str) -> None:
        call_count[0] += 1
        if call_count[0] == 1:
            # Simulate losing the race: the "winner" process already renamed and
            # cleaned up, so lock_dir is gone.  Remove src (our claimed temp name
            # was never created since we never win) and raise.
            _shutil.rmtree(src, ignore_errors=True)
            raise FileNotFoundError("race lost — winner already cleaned up")
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", race_losing_rename)

    # Must still acquire after the race loss (lock_dir is now absent, mkdir succeeds)
    with file_lock(lock_dir, ttl=30.0, retry=0.0, attempts=10):
        assert lock_dir.is_dir()

    assert not lock_dir.exists()


# ---------------------------------------------------------------------------
# 8. MEDIUM 2 — _write_lock_info failure must not leak lock_dir
# ---------------------------------------------------------------------------


def test_file_lock_write_info_failure_cleans_up_lock_dir(tmp_path: Path, monkeypatch) -> None:
    """If _write_lock_info raises after mkdir, the lock dir is removed (no leak).

    With the old code, mkdir succeeds, _write_lock_info raises, the for-loop exits
    without the finally registering, so lock_dir is left behind permanently.
    With the fix, the acquisition code wraps _write_lock_info and cleans up on failure.
    """
    import tide.io as _io_mod

    lock_dir = tmp_path / "lock"

    def failing_write_info(d: Path) -> None:
        raise OSError("disk full — cannot write lock.json")

    monkeypatch.setattr(_io_mod, "_write_lock_info", failing_write_info)

    with pytest.raises(OSError, match="disk full"):
        with file_lock(lock_dir):
            pass  # never reached

    assert not lock_dir.exists(), (
        "lock_dir must be removed if _write_lock_info fails — no permanent leak"
    )


# ---------------------------------------------------------------------------
# 9. HIGH — stamp_canon_baseline must execute while the lock is held
# ---------------------------------------------------------------------------


def test_merge_delta_stamp_baseline_called_inside_lock(tmp_project: Path, monkeypatch) -> None:
    """stamp_canon_baseline is invoked while the merge lock is held.

    With the old code the lock is released after atomic_write(canon), so
    stamp_canon_baseline runs outside the lock. With the fix, all three steps
    (atomic_write, mark_merged, stamp_canon_baseline) are inside the with-block.
    """
    from tide.cannon import reality as _reality_mod

    arc_dir = tmp_project / ".tide" / "arcs" / "01-fix"
    arc_dir.mkdir(parents=True)
    (arc_dir / "delta.md").write_text(
        "# delta — fix\nmerged: no\n\nsome work\n", encoding="utf-8"
    )

    lock_dir = paths.state_dir(tmp_project) / ".merge.lock"
    stamp_saw_lock: list[bool] = []

    real_stamp = _reality_mod.stamp_canon_baseline

    def checking_stamp(root: Path) -> object:
        stamp_saw_lock.append(lock_dir.is_dir())
        return real_stamp(root)

    monkeypatch.setattr(_reality_mod, "stamp_canon_baseline", checking_stamp)

    merge.merge_delta(tmp_project, arc_dir, slug="fix", date="2026-06-27")

    assert stamp_saw_lock, "stamp_canon_baseline must have been called"
    assert stamp_saw_lock[0], (
        "stamp_canon_baseline must be called WHILE the lock is held "
        "(lock_dir must exist during the call)"
    )


def test_merge_delta_two_sequential_merges_preserve_both_entries(tmp_project: Path) -> None:
    """Two sequential merge_delta calls both keep their journal entries intact."""
    arc1 = tmp_project / ".tide" / "arcs" / "01-alpha"
    arc1.mkdir(parents=True)
    (arc1 / "delta.md").write_text(
        "# delta — alpha\nmerged: no\n\nwork A\n", encoding="utf-8"
    )

    arc2 = tmp_project / ".tide" / "arcs" / "02-beta"
    arc2.mkdir(parents=True)
    (arc2 / "delta.md").write_text(
        "# delta — beta\nmerged: no\n\nwork B\n", encoding="utf-8"
    )

    merge.merge_delta(tmp_project, arc1, slug="alpha", date="2026-06-27")
    merge.merge_delta(tmp_project, arc2, slug="beta", date="2026-06-27")

    canon = paths.canon_file(tmp_project).read_text(encoding="utf-8")
    assert "### 2026-06-27 · alpha" in canon
    assert "### 2026-06-27 · beta" in canon
    assert canon.index("### 2026-06-27 · alpha") < canon.index("### 2026-06-27 · beta")
