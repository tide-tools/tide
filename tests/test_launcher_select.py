"""launcher.select — the non-tty fallback of the arrow-key picker.

The curses path needs a real terminal (none under pytest), so these tests only
exercise the fallback: with stdin/stdout forced non-tty, ``select`` must print a
numbered list and read one line, mapping 0/empty/new → NEW and 1..N → a 0-based
index, raising on garbage — and it must NEVER enter curses when there is no tty.
"""

from __future__ import annotations

import builtins

import pytest

from tide.launcher import select


@pytest.fixture
def non_tty(monkeypatch):
    """Force both streams to report non-tty (the scripted/piped path)."""
    monkeypatch.setattr(select.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(select.sys.stdout, "isatty", lambda: False)


def _feed(monkeypatch, answer):
    """Make builtins.input return *answer* once (curses must never be reached)."""
    monkeypatch.setattr(builtins, "input", lambda prompt="": answer)


# --- tty detection ---------------------------------------------------------

def test_is_interactive_tty_false_when_streams_piped(non_tty):
    assert select.is_interactive_tty() is False


def test_curses_failure_degrades_to_numbered_fallback(monkeypatch):
    # On a tty where curses blows up (narrow/remote/mobile terminal), select must
    # NOT crash the menu — it falls back to the numbered-list prompt.
    monkeypatch.setattr(select.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(select.sys.stdout, "isatty", lambda: True)

    def _boom(*_a, **_k):
        raise RuntimeError("addnstr() returned ERR")  # a curses-style failure

    monkeypatch.setattr(select, "_run_curses", _boom)
    _feed(monkeypatch, "2")
    assert select.select("pick", ["a", "b", "c"]) == 1  # fell back, no crash


def test_non_tty_never_enters_curses(non_tty, monkeypatch):
    # If curses were touched the import/wrapper would blow up under pytest; force
    # the issue by making any curses use explode, then prove fallback is taken.
    def _boom(*_a, **_k):
        raise AssertionError("curses must not run without a tty")

    monkeypatch.setattr(select, "_run_curses", _boom)
    _feed(monkeypatch, "1")
    assert select.select("pick", ["a", "b"]) == 0


# --- fallback parsing ------------------------------------------------------

def test_fallback_returns_zero_based_index(non_tty, monkeypatch):
    _feed(monkeypatch, "2")
    assert select.select("pick", ["a", "b", "c"]) == 1


def test_fallback_first_row_is_index_zero(non_tty, monkeypatch):
    _feed(monkeypatch, "1")
    assert select.select("pick", ["a", "b"]) == 0


@pytest.mark.parametrize("answer", ["0", "", "new", "n", "+"])
def test_fallback_new_tokens_return_new(non_tty, monkeypatch, answer):
    _feed(monkeypatch, answer)
    assert select.select("pick", ["a", "b"]) == select.NEW


def test_fallback_eof_is_new(non_tty, monkeypatch):
    def _eof(prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", _eof)
    assert select.select("pick", ["a"]) == select.NEW


@pytest.mark.parametrize("answer", ["x", "9", "1.5", "-1"])
def test_fallback_bad_input_raises(non_tty, monkeypatch, answer):
    _feed(monkeypatch, answer)
    with pytest.raises(select.SelectError):
        select.select("pick", ["a", "b"])


def test_fallback_no_new_rejects_zero(non_tty, monkeypatch):
    # allow_new=False (the project step) has no "+ new" row, so 0/empty are bad.
    _feed(monkeypatch, "0")
    with pytest.raises(select.SelectError):
        select.select("pick", ["a", "b"], allow_new=False)


def test_fallback_no_new_returns_index(non_tty, monkeypatch):
    _feed(monkeypatch, "2")
    assert select.select("pick", ["a", "b"], allow_new=False) == 1


def test_fallback_prints_numbered_list_with_new_row(non_tty, monkeypatch, capsys):
    _feed(monkeypatch, "1")
    select.select("Title here", ["alpha", "beta"], new_label="+ new thread")
    out = capsys.readouterr().out
    assert "Title here" in out
    assert "0) + new thread" in out
    assert "1) alpha" in out
    assert "2) beta" in out
