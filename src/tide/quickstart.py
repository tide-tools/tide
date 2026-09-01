"""tide.quickstart — "where is the instruction?", answered from inside the terminal.

The gap this closes (работа 59). A person installed tide, unfolded a home, made a
project — and asked, in those words: *there is no instruction, no web page, where
is it?* Every surface knew the next single gesture and none of them said where the
ROUTE lived. The guide existed the whole time, as ``QUICKSTART.md`` in a clone the
person had already forgotten about, or as a URL nobody had been told.

So the route ships INSIDE the package and needs neither the clone nor the network:
``tide quickstart`` prints it. Two things it is deliberately not — a copy of the
guide (that would rot in two places), and a wall of prose. It is the map: six
steps, one line each, the command that walks each one, and where the long-form
guide lives. Someone stuck mid-install needs *what do I type now*, not a tutorial.

The long form is reached by :func:`guide_location`: the checkout's own
``QUICKSTART.md`` when tide was installed from a clone, else the published page —
a Homebrew install has no clone at all, and telling that person to open a file
that does not exist is exactly the dead end this module exists to remove.
``--open`` hands whichever of the two to the browser.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import List, Optional, Tuple

PAGE_URL = "https://tide-tools.github.io/tide/quickstart.html"
GUIDE_FILE = "QUICKSTART.md"

# The route. One line per step, each with the gesture that walks it — this is what
# a person stuck in a terminal actually needs. Kept short on purpose: it must fit
# on one screen next to whatever they were already looking at.
STEPS: List[Tuple[str, str]] = [
    ("install", "git clone https://github.com/tide-tools/tide && cd tide && ./install.sh"),
    ("a home — the one folder you lead from",
     "mkdir ~/tide-home && cd ~/tide-home && tide init --git"),
    ("tell the shell where it is (put this in your profile)",
     "export TIDE_HOME=~/tide-home"),
    ("a project — any folder with code, old or new",
     'cd ~/code/myapp && tide adopt --goal "what this is for"'),
    ("the board — home and every project on one page", "tide board --open"),
    ("your first work, all the way to closed",
     'tide work add "…" · propose · agree · take · check --proof · close --word'),
    ("a session — from here on you speak in words", "cd ~/tide-home && tide menu"),
]


def guide_location(repo_root: Optional[Path] = None) -> Tuple[str, bool]:
    """Where the long-form guide is: ``(location, is_local)``.

    A clone install can read it off disk; a Homebrew install has no clone, so the
    published page is the honest answer rather than a path that is not there.
    """
    root = repo_root if repo_root is not None else _repo_root()
    if root is not None:
        f = Path(root) / GUIDE_FILE
        if f.is_file():
            return str(f), True
    return PAGE_URL, False


def _repo_root() -> Optional[Path]:
    """The checkout above the installed package, when tide came from a clone."""
    candidate = Path(__file__).resolve().parent.parent.parent
    return candidate if (candidate / "pyproject.toml").is_file() else None


def route_lines(repo_root: Optional[Path] = None) -> List[str]:
    """The printable route: the steps, then where the full guide lives."""
    out = ["tide — the first hour, in one screen", ""]
    for i, (what, how) in enumerate(STEPS, 1):
        out.append("  {0}. {1}".format(i, what))
        out.append("     {0}".format(how))
    location, is_local = guide_location(repo_root)
    out.append("")
    out.append("full guide (every step, with what you'll see):")
    out.append("  {0}".format(location))
    if is_local:
        out.append("  {0}".format(PAGE_URL))
    out.append("")
    out.append("open it in a browser:  tide quickstart --open")
    out.append("every command:         tide help")
    return out


def next_step_line(after: str) -> str:
    """The one-line route pointer a command prints when it finishes.

    Every surface already said its own next gesture; none said where the map was.
    This is that missing half-line, in the shape ``install.sh`` set: say the next
    gesture, then say where the whole route lives.
    """
    return "lost? the whole route in one screen: tide quickstart"


def _cmd_quickstart(args) -> int:
    if getattr(args, "open", False):
        location, is_local = guide_location()
        target = Path(location).as_uri() if is_local else location
        opened = webbrowser.open(target)
        print("tide quickstart: {0} {1}".format(
            "opened" if opened else "could not open a browser for", location))
        if not opened:
            print("  read it here instead:")
            for line in route_lines():
                print("  " + line)
        return 0
    for line in route_lines():
        print(line)
    return 0


def register(subparsers) -> None:
    """Add the top-level ``quickstart`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "quickstart",
        help="the first hour in one screen — the route, and where the full guide is",
    )
    p.add_argument(
        "--open",
        action="store_true",
        help="open the full guide in a browser (the clone's copy, else the published page)",
    )
    p.set_defaults(func=_cmd_quickstart, _cmd="quickstart")
