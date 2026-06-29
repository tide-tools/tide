"""tide.launcher.select — an arrow-key picker with a graceful non-tty fallback.

In a real terminal (both ``stdin`` and ``stdout`` are ttys) :func:`select`
renders a small **curses** menu: a title line, the options as a vertical list
with the highlighted row inverted, an optional ``+ new`` row, and a hint line.
Arrow keys ↑/↓ (or ``k``/``j``) move the highlight, Enter selects, ``q``/Esc
cancels. Everywhere else — pipes, tests, scripted ``--pick`` — it falls back to
printing a numbered list and reading one line with :func:`input`, mapping
``0``/empty/``new`` to the :data:`NEW` sentinel and ``1..N`` to a 0-based index
(the same shape :func:`tide.launcher.menu.parse_pick` enforced).

curses is imported lazily inside the tty branch, so importing this module never
touches the terminal; the interactive session runs under :func:`curses.wrapper`
so the terminal is restored even when the loop raises.
"""

from __future__ import annotations

import sys
from typing import List, Union

from ..arc.stream import StreamError

# Sentinel returned when the "+ new" row is chosen (mirrors menu.PICK_NEW, but a
# distinct value so the caller never confuses it with a real 0-based index).
NEW = "new"

# Sentinel returned when the human goes back to the previous screen (← / Esc / h).
BACK = "back"

# Keys the fallback parse treats as "+ new" (same set as menu.parse_pick).
_NEW_TOKENS = ("", "0", "n", "new", "+")
# Keys the fallback parse treats as "go back" (when allow_back).
_BACK_TOKENS = ("b", "back", "<", "-")

_HINT = "↑/↓ move · Enter select · q cancel"
_HINT_BACK = "↑/↓ move · Enter select · ←/Esc back"


class SelectError(StreamError):
    """Bad pick on the non-tty fallback — mirrors :class:`menu.MenuError`.

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same arm parse errors already use (prints ``tide: …``, exits nonzero).
    """


def is_interactive_tty() -> bool:
    """True only when BOTH stdin and stdout are real terminals (curses-capable)."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def select(
    title: str,
    options: List[str],
    *,
    allow_new: bool = True,
    new_label: str = "+ new",
    allow_back: bool = False,
) -> Union[int, str]:
    """Pick one of *options*; return its 0-based index, :data:`NEW` for "+ new", or
    :data:`BACK` for "go to the previous screen" (when *allow_back*).

    On a real terminal this is an interactive curses menu (arrows/k/j move, Enter
    selects; ←/Esc/h → :data:`BACK` when *allow_back*, else q/Esc cancels to
    :data:`NEW`/index ``0``). Otherwise it prints a numbered list and reads one
    line, mapping ``0``/empty/``new`` → :data:`NEW`, ``b``/``<`` → :data:`BACK`
    (when allowed), ``1..len`` → index, raising :class:`SelectError` otherwise.
    """
    if is_interactive_tty() and (options or allow_new):
        try:
            return _run_curses(title, list(options), allow_new, new_label, allow_back)
        except Exception:  # noqa: BLE001
            # curses can fail on narrow / remote / non-standard terminals (e.g. a
            # mobile Orca tab): addnstr ERR, setupterm, a 0-size window. Never crash
            # the whole menu for it — curses.wrapper has already restored the
            # terminal, so degrade to the numbered-list prompt below.
            pass
    return _fallback(title, list(options), allow_new, new_label, allow_back)


# --- non-tty fallback ------------------------------------------------------

def _fallback(title, options, allow_new, new_label, allow_back=False):
    """Print a numbered list and read one line (the scripted / piped path)."""
    print(title)
    if allow_new:
        print("  0) {0}".format(new_label))
    for i, opt in enumerate(options, start=1):
        print("  {0}) {1}".format(i, opt))
    if allow_back:
        print("  (b = back)")
    return _parse_pick(_input_line("select> "), len(options), allow_new, allow_back)


def _input_line(prompt):
    """input() that treats EOF (piped/empty stdin) as an empty answer."""
    try:
        return input(prompt)
    except EOFError:
        return ""


def _parse_pick(raw, count, allow_new, allow_back=False):
    """Map a typed pick to :data:`NEW`/:data:`BACK` or a 0-based index."""
    s = (raw or "").strip().lower()
    if allow_back and s in _BACK_TOKENS:
        return BACK
    if allow_new and s in _NEW_TOKENS:
        return NEW
    if not s.isdigit():
        raise SelectError("select: invalid {0!r} (a number, 0 = new)".format(raw))
    n = int(s)
    if 1 <= n <= count:
        return n - 1
    raise SelectError("select: {0} out of range (1..{1})".format(n, count))


# --- interactive curses menu -----------------------------------------------

def _build_rows(options, allow_new, new_label):
    """Highlightable rows + the starting cursor (first real option, else "+ new")."""
    rows = []
    if allow_new:
        rows.append(("new", new_label, -1))
    for i, opt in enumerate(options):
        rows.append(("opt", opt, i))
    start = 0
    for idx, row in enumerate(rows):
        if row[0] == "opt":
            start = idx
            break
    return rows, start


def _run_curses(title, options, allow_new, new_label, allow_back=False):
    """Drive the arrow-key menu under curses.wrapper; return index / :data:`NEW` / :data:`BACK`."""
    import curses  # lazy: importing the module never starts the terminal

    rows, start = _build_rows(options, allow_new, new_label)

    def _main(stdscr):
        curses.curs_set(0)
        cur = start
        while True:
            _draw(curses, stdscr, title, rows, cur, allow_back)
            ch = stdscr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                cur = (cur - 1) % len(rows)
            elif ch in (curses.KEY_DOWN, ord("j")):
                cur = (cur + 1) % len(rows)
            elif ch in (curses.KEY_ENTER, ord("\n"), ord("\r")):
                kind, _, idx = rows[cur]
                return NEW if kind == "new" else idx
            elif allow_back and ch in (curses.KEY_LEFT, ord("h"), 27):  # ←/h/Esc → back
                return BACK
            elif ch in (ord("q"), 27):  # q or Esc → cancel
                return BACK if allow_back else (NEW if allow_new else 0)

    return curses.wrapper(_main)


def _draw(curses, stdscr, title, rows, cur, allow_back=False):
    """Paint the title, the option rows (highlight inverted), and the hint line."""
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    cap = max(width - 1, 1)
    stdscr.addnstr(0, 0, title[:cap], cap, curses.A_BOLD)
    for i, (_, label, _idx) in enumerate(rows):
        y = i + 2
        if y >= max(height - 1, 1):
            break
        marker = "> " if i == cur else "  "
        text = "  {0}{1}".format(marker, label)
        attr = curses.A_REVERSE if i == cur else curses.A_NORMAL
        stdscr.addnstr(y, 0, text[:cap], cap, attr)
    if height >= 2:
        hint = _HINT_BACK if allow_back else _HINT
        stdscr.addnstr(height - 1, 0, hint[:cap], cap, curses.A_DIM)
    stdscr.refresh()
