"""59-first-hour — the route out of "where is the instruction?".

A person finished a clean install, unfolded a home and adopted a project, then
asked in those words: *there is no instruction, no web page, where is it?* Every
surface knew its own next gesture; none said where the whole route lived. These
tests hold the three answers in place — a command that prints the route with no
clone and no network, surfaces that end pointing at it, and a readable page the
landing actually links to.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tide import cli, quickstart, terminal_choice
from tide.adapters import SETTINGS_KEY

REPO = Path(__file__).resolve().parent.parent


# --- the route prints anywhere ---------------------------------------------

def test_route_needs_neither_clone_nor_network(tmp_path):
    # The whole point: a Homebrew install has no checkout, and a stuck person may
    # have no browser open. The route ships in the package and prints regardless.
    lines = quickstart.route_lines(repo_root=tmp_path)
    text = "\n".join(lines)
    assert quickstart.PAGE_URL in text
    for _, command in quickstart.STEPS:
        assert command in text


def test_every_step_carries_the_gesture_that_walks_it():
    # A route whose steps you cannot type is a table of contents, not a route.
    for what, command in quickstart.STEPS:
        assert what and command
        assert "tide " in command or command.startswith("git clone") or "export " in command


def test_guide_location_prefers_the_clone_then_the_page(tmp_path):
    assert quickstart.guide_location(repo_root=tmp_path) == (quickstart.PAGE_URL, False)
    (tmp_path / quickstart.GUIDE_FILE).write_text("# guide\n", encoding="utf-8")
    where, is_local = quickstart.guide_location(repo_root=tmp_path)
    assert is_local and where.endswith(quickstart.GUIDE_FILE)


def test_cli_quickstart_prints_the_route(capsys):
    assert cli.main(["quickstart"]) == 0
    out = capsys.readouterr().out
    assert "the first hour" in out
    assert "tide init --git" in out
    assert "tide help" in out


# --- the surfaces end on the route -----------------------------------------

def test_init_ends_with_the_route_not_the_inventory(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "--name", "home"]) == 0
    out = capsys.readouterr().out
    assert "next:" in out                     # the next gesture
    assert "export TIDE_HOME=" in out         # spelled out, with the real path
    assert "tide quickstart" in out           # and where the whole map is


def test_adopt_ends_with_the_terminal_and_the_route(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["adopt", "--name", "demo", "--no-orca", "--no-git"]) == 0
    out = capsys.readouterr().out
    assert "terminal:" in out                 # the choice, said out loud
    assert "tide terminal-adapter --set" in out
    assert "tide board --open" in out
    assert "tide quickstart" in out


# --- the terminal choice is met, not stumbled over -------------------------

def test_the_choice_says_what_and_why(tmp_path):
    lines = terminal_choice.announce_lines(tmp_path)
    assert lines[0].startswith("terminal: ")
    assert "—" in lines[0]                    # a reason, not just a name
    assert any("tide terminal-adapter --set" in ln for ln in lines)


def test_pin_survives_the_humans_own_settings(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
    terminal_choice.pin(tmp_path, "tmux")
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data[SETTINGS_KEY] == "tmux"
    assert data["permissions"] == {"allow": ["Bash"]}   # merge-safe, never replaced
    assert terminal_choice.chosen(tmp_path) == ("tmux", True)


def test_unpin_returns_to_auto_detect(tmp_path):
    terminal_choice.pin(tmp_path, "tmux")
    assert terminal_choice.unpin(tmp_path) is not None
    name, pinned = terminal_choice.chosen(tmp_path)
    assert pinned is False and name in ("orca", "macos", "tmux")
    assert terminal_choice.unpin(tmp_path) is None      # idempotent


def test_an_unknown_adapter_fails_loud(tmp_path):
    from tide.adapters import AdapterError

    with pytest.raises(AdapterError):
        terminal_choice.pin(tmp_path, "nope")


def test_a_pinned_choice_says_it_is_pinned(tmp_path):
    terminal_choice.pin(tmp_path, "macos")
    lines = terminal_choice.announce_lines(tmp_path)
    assert "pinned" in lines[0]
    # the offered alternatives never include the one already in use
    assert "--set orca | tmux" in lines[-1]


# --- the published page ----------------------------------------------------

def test_the_landing_links_the_page_not_a_markdown_render():
    # A person stuck in a terminal opens a page, not github.com/.../blob/…/*.md.
    index = (REPO / "docs" / "index.html").read_text(encoding="utf-8")
    assert "blob/main/QUICKSTART.md" not in index
    assert 'href="quickstart.html"' in index


def test_both_language_pages_exist_and_point_at_each_other():
    en = (REPO / "docs" / "quickstart.html").read_text(encoding="utf-8")
    ru = (REPO / "docs" / "quickstart.ru.html").read_text(encoding="utf-8")
    assert 'href="quickstart.ru.html"' in en
    assert 'href="quickstart.html"' in ru
    assert 'lang="en"' in en and 'lang="ru"' in ru


def test_the_page_and_the_guide_do_not_drift():
    """The page is hand-written; this is what keeps it honest.

    Every command the markdown guide teaches must appear on the page — so a step
    added to one and forgotten in the other fails here instead of on a stranger's
    first hour.
    """
    guide = (REPO / "QUICKSTART.md").read_text(encoding="utf-8")
    page = (REPO / "docs" / "quickstart.html").read_text(encoding="utf-8")
    taught = {m.strip() for m in re.findall(r"^(tide [a-z-]+(?: [a-z-]+)?)", guide, re.M)}
    missing = sorted(c for c in taught if c not in page)
    assert not missing, "on the guide but not the page: {0}".format(missing)


def test_the_page_teaches_the_way_out_when_stuck():
    page = (REPO / "docs" / "quickstart.html").read_text(encoding="utf-8")
    for way in ("tide quickstart", "tide help", "tide doctor", "tide report"):
        assert way in page
