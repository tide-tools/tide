"""U5 unit — roster environment extension (candidate 15).

Tests for the OPTIONAL third field ``name | path | environment`` added to the
roster line format.  All existing 2-field behaviour must be preserved byte-for-byte
(backward-compatibility is the hard requirement).

Parse rule (see module docstring of roster.py for the authoritative statement):
  name   = text before the FIRST ``|``
  rest   = text after the FIRST ``|``
  if ``|`` exists in rest: split on the LAST ``|``  →  path + env
  else:                    path = rest, env absent (no key in dict)

This means paths may contain spaces but must not contain ``|`` — the same
restriction that always held.
"""

from __future__ import annotations

import pytest

from tide import cli, paths, roster
from tide.arc import board


# ---------------------------------------------------------------------------
# _parse_line — unit
# ---------------------------------------------------------------------------

def test_parse_line_legacy_two_field_returns_no_env_key():
    """Old-style 'name | path' must not gain an 'environment' key."""
    entry = roster._parse_line("focus | /p/focus")
    assert entry == {"name": "focus", "path": "/p/focus"}
    assert "environment" not in entry


def test_parse_line_three_field_returns_env_key():
    entry = roster._parse_line("block-builder | /p/block-builder | box")
    assert entry == {"name": "block-builder", "path": "/p/block-builder", "environment": "box"}


def test_parse_line_three_field_strips_whitespace():
    entry = roster._parse_line("bb |  /p/bb  |  box  ")
    assert entry["environment"] == "box"
    assert entry["path"] == "/p/bb"
    assert entry["name"] == "bb"


def test_parse_line_path_with_spaces_no_env():
    """Paths with spaces parse correctly when there is no environment field."""
    entry = roster._parse_line("demo | /Users/you/My Projects/demo")
    assert entry == {"name": "demo", "path": "/Users/you/My Projects/demo"}
    assert "environment" not in entry


def test_parse_line_env_empty_after_strip_is_dropped():
    """A trailing ' | ' with blank env is treated as no env — prevents ghost keys."""
    entry = roster._parse_line("focus | /p/focus |   ")
    # blank env → no environment key (same as 2-field line)
    assert "environment" not in entry
    assert entry["path"] == "/p/focus"


def test_parse_line_skips_blank_and_header_lines():
    assert roster._parse_line("") is None
    assert roster._parse_line("   ") is None
    assert roster._parse_line("# tide roster") is None


def test_parse_line_skips_no_pipe_line():
    assert roster._parse_line("no pipe here") is None


def test_parse_line_four_field_is_rejected():
    """A hand-edited 4-field line must return None, not silently bake a pipe into path.

    Without the fix ``_parse_line("name | path | env | spurious")`` would have
    returned ``{'path': 'path | env', 'environment': 'spurious'}`` — a literal
    pipe baked into the path value, causing silent data corruption.
    """
    assert roster._parse_line("name | path | env | spurious") is None


def test_parse_line_four_field_does_not_corrupt_path():
    """Corroborate: the returned dict (None) never carries a pipe-containing path."""
    result = roster._parse_line("myproject | /some/path | box | extra")
    assert result is None


# ---------------------------------------------------------------------------
# add — with optional env
# ---------------------------------------------------------------------------

def test_add_without_env_has_no_environment_key(tmp_control_home):
    """add() without env preserves old dict shape — existing callers are unaffected."""
    roster.add(tmp_control_home, "focus", "/p/focus")
    entries = roster.read_roster(tmp_control_home)
    assert entries == [{"name": "focus", "path": "/p/focus"}]
    assert "environment" not in entries[0]


def test_add_with_env_stores_environment(tmp_control_home):
    roster.add(tmp_control_home, "block-builder", "/p/bb", env="box")
    entries = roster.read_roster(tmp_control_home)
    assert entries == [{"name": "block-builder", "path": "/p/bb", "environment": "box"}]


def test_add_multiple_mixed_env(tmp_control_home):
    """Local and remote entries coexist in order."""
    roster.add(tmp_control_home, "focus", "/p/focus")
    roster.add(tmp_control_home, "block-builder", "/p/bb", env="box")
    roster.add(tmp_control_home, "pulse", "/p/pulse")
    entries = roster.read_roster(tmp_control_home)
    names = [e["name"] for e in entries]
    assert names == ["focus", "block-builder", "pulse"]
    assert "environment" not in entries[0]
    assert entries[1]["environment"] == "box"
    assert "environment" not in entries[2]


def test_add_replace_name_updates_env(tmp_control_home):
    """Replacing an existing entry by name updates the env in-place."""
    roster.add(tmp_control_home, "bb", "/p/bb")
    roster.add(tmp_control_home, "pulse", "/p/pulse")
    roster.add(tmp_control_home, "bb", "/p/bb", env="box")
    entries = roster.read_roster(tmp_control_home)
    # bb keeps its slot, env added
    assert entries[0]["name"] == "bb"
    assert entries[0]["environment"] == "box"
    # pulse unchanged
    assert entries[1] == {"name": "pulse", "path": "/p/pulse"}


def test_add_replace_name_clears_env_when_omitted(tmp_control_home):
    """Re-registering without env clears a previously set env."""
    roster.add(tmp_control_home, "bb", "/p/bb", env="box")
    roster.add(tmp_control_home, "bb", "/p/bb")          # no env
    entries = roster.read_roster(tmp_control_home)
    assert entries[0] == {"name": "bb", "path": "/p/bb"}
    assert "environment" not in entries[0]


def test_add_with_none_env_behaves_as_no_env(tmp_control_home):
    """Explicitly passing env=None is identical to omitting it."""
    roster.add(tmp_control_home, "focus", "/p/focus", env=None)
    entries = roster.read_roster(tmp_control_home)
    assert "environment" not in entries[0]


def test_add_empty_name_raises(tmp_control_home):
    with pytest.raises(roster.RosterError):
        roster.add(tmp_control_home, "", "/p/x", env="box")


def test_add_empty_path_raises(tmp_control_home):
    with pytest.raises(roster.RosterError):
        roster.add(tmp_control_home, "focus", "", env="box")


def test_add_path_with_pipe_raises(tmp_control_home):
    """A path containing '|' must be rejected at write time to prevent parse corruption.

    Without the fix ``roster.add(root, "x", "/fo|bar")`` would silently write a
    roster line that rpartition would later mis-split, corrupting both path and env.
    """
    with pytest.raises(roster.RosterError, match="must not contain"):
        roster.add(tmp_control_home, "x", "/fo|bar")


def test_add_path_with_pipe_raises_even_with_env(tmp_control_home):
    """The pipe guard on path applies regardless of whether an env is supplied."""
    with pytest.raises(roster.RosterError):
        roster.add(tmp_control_home, "x", "/fo|bar", env="box")


# ---------------------------------------------------------------------------
# _render / _write — round-trip
# ---------------------------------------------------------------------------

def test_render_local_entry_omits_env_field():
    """Entries without env serialise as plain 'name | path' — byte-identical to old format."""
    entries = [{"name": "focus", "path": "/p/focus"}]
    text = roster._render(entries)
    assert "focus | /p/focus" in text
    # no third pipe in the data line
    lines = [ln for ln in text.splitlines() if "focus" in ln and not ln.startswith("#")]
    assert all(ln.count("|") == 1 for ln in lines)


def test_render_remote_entry_includes_env_field():
    entries = [{"name": "bb", "path": "/p/bb", "environment": "box"}]
    text = roster._render(entries)
    assert "bb | /p/bb | box" in text


def test_render_mixed_entries_format():
    entries = [
        {"name": "focus", "path": "/p/focus"},
        {"name": "bb", "path": "/p/bb", "environment": "box"},
        {"name": "pulse", "path": "/p/pulse"},
    ]
    text = roster._render(entries)
    lines = text.splitlines()
    assert lines[0] == "# tide roster"
    assert lines[1] == "focus | /p/focus"
    assert lines[2] == "bb | /p/bb | box"
    assert lines[3] == "pulse | /p/pulse"


def test_render_round_trips_byte_identical_for_old_format(tmp_control_home):
    """Writing and reading a local-only roster is byte-identical to the old behaviour."""
    roster.add(tmp_control_home, "focus", "/p/focus")
    roster.add(tmp_control_home, "pulse", "/p/pulse")
    text = paths.roster_file(tmp_control_home).read_text(encoding="utf-8")
    assert text == "# tide roster\nfocus | /p/focus\npulse | /p/pulse\n"


def test_render_round_trips_with_env(tmp_control_home):
    roster.add(tmp_control_home, "focus", "/p/focus")
    roster.add(tmp_control_home, "bb", "/p/bb", env="box")
    text = paths.roster_file(tmp_control_home).read_text(encoding="utf-8")
    assert text == "# tide roster\nfocus | /p/focus\nbb | /p/bb | box\n"


def test_three_field_round_trip_after_corruption_fix(tmp_control_home):
    """Sanity: a valid 3-field entry still round-trips correctly after the 4-field fix.

    Confirms the ``if '|' in path_part: return None`` guard does NOT affect
    legitimate 3-field lines whose path_part is pipe-free.
    """
    roster.add(tmp_control_home, "block-builder", "/Users/you/projects/block-builder", env="box")
    entries = roster.read_roster(tmp_control_home)
    assert entries == [
        {"name": "block-builder", "path": "/Users/you/projects/block-builder", "environment": "box"}
    ]
    # write → re-read is also identical
    entries2 = roster.read_roster(tmp_control_home)
    assert entries == entries2


# ---------------------------------------------------------------------------
# read_roster — file-level
# ---------------------------------------------------------------------------

def test_read_roster_legacy_file_no_env_key(tmp_control_home):
    """Reading an old-format roster.md produces entries with no 'environment' key."""
    paths.roster_file(tmp_control_home).write_text(
        "# tide roster\nfocus | /p/focus\npulse | /p/pulse\n",
        encoding="utf-8",
    )
    entries = roster.read_roster(tmp_control_home)
    for e in entries:
        assert "environment" not in e


def test_read_roster_env_file_has_env_key(tmp_control_home):
    paths.roster_file(tmp_control_home).write_text(
        "# tide roster\nfocus | /p/focus\nbb | /p/bb | box\n",
        encoding="utf-8",
    )
    entries = roster.read_roster(tmp_control_home)
    assert "environment" not in entries[0]
    assert entries[1]["environment"] == "box"


def test_read_roster_mixed_file_ordering(tmp_control_home):
    paths.roster_file(tmp_control_home).write_text(
        "# tide roster\na | /a | remote\nb | /b\nc | /c | other\n",
        encoding="utf-8",
    )
    entries = roster.read_roster(tmp_control_home)
    assert entries[0]["environment"] == "remote"
    assert "environment" not in entries[1]
    assert entries[2]["environment"] == "other"


# ---------------------------------------------------------------------------
# render_list — public surface
# ---------------------------------------------------------------------------

def test_render_list_local_entry_no_env(tmp_control_home):
    roster.add(tmp_control_home, "focus", "/p/focus")
    assert roster.render_list(tmp_control_home) == "focus | /p/focus"


def test_render_list_remote_entry_shows_env(tmp_control_home):
    roster.add(tmp_control_home, "bb", "/p/bb", env="box")
    assert roster.render_list(tmp_control_home) == "bb | /p/bb | box"


def test_render_list_mixed(tmp_control_home):
    roster.add(tmp_control_home, "focus", "/p/focus")
    roster.add(tmp_control_home, "bb", "/p/bb", env="box")
    out = roster.render_list(tmp_control_home)
    lines = out.splitlines()
    assert lines[0] == "focus | /p/focus"
    assert lines[1] == "bb | /p/bb | box"


# ---------------------------------------------------------------------------
# remove — env entries removed cleanly
# ---------------------------------------------------------------------------

def test_remove_env_entry(tmp_control_home):
    roster.add(tmp_control_home, "focus", "/p/focus")
    roster.add(tmp_control_home, "bb", "/p/bb", env="box")
    roster.remove(tmp_control_home, "bb")
    entries = roster.read_roster(tmp_control_home)
    assert [e["name"] for e in entries] == ["focus"]


# ---------------------------------------------------------------------------
# CLI — roster add --env
# ---------------------------------------------------------------------------

class TestCliRosterAddWithEnv:
    """Integration tests for `tide roster add --env <env> <name> <path>`."""

    @pytest.fixture
    def in_control_home(self, tmp_control_home, monkeypatch):
        monkeypatch.chdir(tmp_control_home)
        return tmp_control_home

    def test_add_with_env_flag(self, in_control_home, capsys):
        rc = cli.main(["roster", "add", "--env", "box", "bb", "/p/bb"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "bb" in out
        # verify the file was written with env
        entries = roster.read_roster(in_control_home)
        assert entries[0]["environment"] == "box"

    def test_add_without_env_flag_stays_local(self, in_control_home, capsys):
        rc = cli.main(["roster", "add", "focus", "/p/focus"])
        assert rc == 0
        capsys.readouterr()
        entries = roster.read_roster(in_control_home)
        assert "environment" not in entries[0]

    def test_ls_shows_env(self, in_control_home, capsys):
        cli.main(["roster", "add", "--env", "box", "bb", "/p/bb"])
        capsys.readouterr()
        cli.main(["roster", "ls"])
        out = capsys.readouterr().out
        assert "bb | /p/bb | box" in out

    def test_add_env_then_replace_without_env_clears_it(self, in_control_home, capsys):
        cli.main(["roster", "add", "--env", "box", "bb", "/p/bb"])
        capsys.readouterr()
        cli.main(["roster", "add", "bb", "/p/bb"])
        capsys.readouterr()
        entries = roster.read_roster(in_control_home)
        assert "environment" not in entries[0]

    def test_add_with_env_print_message_includes_env(self, in_control_home, capsys):
        cli.main(["roster", "add", "--env", "box", "bb", "/p/bb"])
        out = capsys.readouterr().out
        assert "box" in out


# ---------------------------------------------------------------------------
# board._render_all — env visible in status --all
# ---------------------------------------------------------------------------

class TestRenderAllEnv:
    """_render_all threads environment into the block header for non-local entries."""

    def _write_roster(self, root, lines):
        paths.roster_file(root).write_text(
            "# tide roster\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )

    def test_local_entry_header_has_no_env_suffix(self, tmp_control_home):
        self._write_roster(tmp_control_home, ["focus | /nonexistent/focus"])
        out = board._render_all(tmp_control_home)
        # header line exists, no env annotation
        assert "=== focus" in out
        # no "env:" marker for local project
        focus_header = next(ln for ln in out.splitlines() if "=== focus" in ln)
        assert "env:" not in focus_header

    def test_remote_entry_header_shows_env(self, tmp_control_home):
        self._write_roster(tmp_control_home, ["bb | /nonexistent/bb | box"])
        out = board._render_all(tmp_control_home)
        assert "=== bb" in out
        header_line = next(ln for ln in out.splitlines() if "=== bb" in ln)
        assert "env: box" in header_line

    def test_mixed_roster_render(self, tmp_control_home):
        self._write_roster(tmp_control_home, [
            "focus | /nonexistent/focus",
            "bb | /nonexistent/bb | box",
        ])
        out = board._render_all(tmp_control_home)
        lines = out.splitlines()
        focus_hdr = next(ln for ln in lines if "=== focus" in ln)
        bb_hdr = next(ln for ln in lines if "=== bb" in ln)
        assert "env:" not in focus_hdr
        assert "env: box" in bb_hdr


# ---------------------------------------------------------------------------
# board.all_status_dict — environment key propagated
# ---------------------------------------------------------------------------

class TestAllStatusDictEnv:
    """all_status_dict includes 'environment' key (None for local, str for remote)."""

    def _write_roster(self, root, lines):
        paths.roster_file(root).write_text(
            "# tide roster\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )

    def test_local_entry_has_none_environment(self, tmp_control_home):
        self._write_roster(tmp_control_home, ["focus | /nonexistent/focus"])
        data = board.all_status_dict(tmp_control_home)
        assert len(data) == 1
        assert data[0]["environment"] is None

    def test_remote_entry_has_env_string(self, tmp_control_home):
        self._write_roster(tmp_control_home, ["bb | /nonexistent/bb | box"])
        data = board.all_status_dict(tmp_control_home)
        assert len(data) == 1
        assert data[0]["environment"] == "box"

    def test_mixed_environment_values(self, tmp_control_home):
        self._write_roster(tmp_control_home, [
            "focus | /nonexistent/focus",
            "bb | /nonexistent/bb | box",
        ])
        data = board.all_status_dict(tmp_control_home)
        by_name = {d["name"]: d for d in data}
        assert by_name["focus"]["environment"] is None
        assert by_name["bb"]["environment"] == "box"
