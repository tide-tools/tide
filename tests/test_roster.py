"""U5 unit — roster: control-home 'name | path' add/rm/ls round-trips."""

from __future__ import annotations

import pytest

from tide import paths, roster


def test_empty_roster_reads_as_empty(tmp_control_home):
    assert roster.read_roster(tmp_control_home) == []
    assert roster.render_list(tmp_control_home) == "(no projects)"


def test_add_round_trips_name_and_path(tmp_control_home):
    roster.add(tmp_control_home, "focus", "/Users/you/projects/focus")
    entries = roster.read_roster(tmp_control_home)
    assert entries == [{"name": "focus", "path": "/Users/you/projects/focus"}]
    # the header survives
    assert paths.roster_file(tmp_control_home).read_text(
        encoding="utf-8"
    ).startswith("# tide roster\n")


def test_add_appends_in_order(tmp_control_home):
    roster.add(tmp_control_home, "focus", "/p/focus")
    roster.add(tmp_control_home, "pulse", "/p/pulse")
    names = [e["name"] for e in roster.read_roster(tmp_control_home)]
    assert names == ["focus", "pulse"]


def test_add_existing_name_replaces_path_in_place(tmp_control_home):
    roster.add(tmp_control_home, "focus", "/p/old")
    roster.add(tmp_control_home, "pulse", "/p/pulse")
    roster.add(tmp_control_home, "focus", "/p/new")
    entries = roster.read_roster(tmp_control_home)
    # focus keeps its slot, path updated; no duplicate
    assert entries == [
        {"name": "focus", "path": "/p/new"},
        {"name": "pulse", "path": "/p/pulse"},
    ]


def test_path_with_spaces_round_trips(tmp_control_home):
    roster.add(tmp_control_home, "demo", "/Users/you/My Projects/demo")
    assert roster.read_roster(tmp_control_home)[0]["path"] == "/Users/you/My Projects/demo"


def test_remove_round_trips(tmp_control_home):
    roster.add(tmp_control_home, "focus", "/p/focus")
    roster.add(tmp_control_home, "pulse", "/p/pulse")
    roster.remove(tmp_control_home, "focus")
    names = [e["name"] for e in roster.read_roster(tmp_control_home)]
    assert names == ["pulse"]


def test_remove_last_keeps_header(tmp_control_home):
    roster.add(tmp_control_home, "focus", "/p/focus")
    roster.remove(tmp_control_home, "focus")
    assert roster.read_roster(tmp_control_home) == []
    assert paths.roster_file(tmp_control_home).read_text(
        encoding="utf-8"
    ).startswith("# tide roster")


def test_remove_absent_raises(tmp_control_home):
    with pytest.raises(roster.RosterError):
        roster.remove(tmp_control_home, "ghost")


def test_add_empty_name_raises(tmp_control_home):
    with pytest.raises(roster.RosterError):
        roster.add(tmp_control_home, "  ", "/p/x")


def test_add_empty_path_raises(tmp_control_home):
    with pytest.raises(roster.RosterError):
        roster.add(tmp_control_home, "focus", "")


def test_add_creates_roster_file_when_missing(tmp_path):
    # bare dir, no roster.md yet → add bootstraps it with the header.
    roster.add(tmp_path, "focus", "/p/focus")
    assert paths.roster_file(tmp_path).is_file()
    assert roster.read_roster(tmp_path) == [{"name": "focus", "path": "/p/focus"}]


def test_render_list_shows_name_pipe_path(tmp_control_home):
    roster.add(tmp_control_home, "focus", "/p/focus")
    assert roster.render_list(tmp_control_home) == "focus | /p/focus"
