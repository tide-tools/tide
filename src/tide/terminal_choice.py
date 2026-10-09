"""tide.terminal_choice — which terminal opens your sessions, said out loud.

The gap (работа 59 п.5). All the machinery was already here: three adapters
(``orca`` / ``macos`` / ``tmux``), auto-detection, and a pin in the project's
``.claude/settings.json``. What was missing was the MOMENT — nothing ever told a
person a choice had been made. They met it as one grey line in the middle of
``tide adopt``'s output, ``orca CLI not on PATH — optional terminal manager``,
which reads as a warning about a thing you failed to install rather than as "here
is what will open your sessions, and here is how to change it".

Deliberately NOT a question on the first minute: at that point nobody knows what
they would be choosing between. So — auto-detect (unchanged), SAY what was picked
and why, and give the one line that changes it. That line has to be real, which is
why this module also writes the pin: before it, the pin could only be produced by
hand-editing JSON, so "you can change it" was not a thing a person could act on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Tuple

from . import harness
from .adapters import (
    ENV_ADAPTER,
    SETTINGS_KEY,
    AdapterError,
    auto_adapter_name,
    available_adapters,
    global_adapter_name,
    global_settings_path,
)

# Why each one gets picked, in the person's terms — not the implementation's.
WHY = {
    "orca": "Orca is on PATH — the author's own terminal manager, and the richest fit",
    "macos": "this is a Mac without Orca — sessions open in Terminal.app",
    "tmux": "no macOS terminal here — sessions open as tmux panes",
    "ao": "the desktop Agent Orchestrator — each session in its own AO worktree "
          "(the project is registered in AO on first launch)",
}
# Said only when the detected choice is not the recommended one, and only when
# acting on it is actually possible.
ORCA_NOTE = "orca (recommended by tide's author) would be used instead if installed"


def chosen_layer(root: Path) -> Tuple[str, str]:
    """The adapter this project will use and WHERE it came from:
    ``(name, "env"|"project"|"global"|"auto")``."""
    import os
    env = (os.environ.get(ENV_ADAPTER) or "").strip()
    if env:
        return env.lower(), "env"
    settings = _settings(root)
    value = settings.get(SETTINGS_KEY) if isinstance(settings, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip().lower(), "project"
    try:
        from . import paths
        home = paths.control_home(root)
    except Exception:  # noqa: BLE001 — no control-home: skip that layer
        home = None
    if home is not None and Path(home).resolve() != Path(root).resolve():
        hv = _settings(home).get(SETTINGS_KEY)
        if isinstance(hv, str) and hv.strip():
            return hv.strip().lower(), "control-home"
    g = global_adapter_name()
    if g:
        return g, "global"
    return auto_adapter_name(), "auto"


def chosen(root: Path) -> Tuple[str, bool]:
    """The adapter this project will use: ``(name, pinned_by_hand)``."""
    name, layer = chosen_layer(root)
    return name, layer != "auto"


def why(name: str, pinned, layer: str = "") -> str:
    """One clause explaining the choice, for printing after the name."""
    if layer == "env":
        return "set by ${0}".format(ENV_ADAPTER)
    if layer == "control-home":
        return "pinned in the control-home's .claude/settings.json (pickup/menu/spark/return)"
    if layer == "global":
        return "pinned globally in {0} — {1}".format(global_settings_path(),
                                                      WHY.get(name, ""))
    if pinned:
        return "pinned in this project's .claude/settings.json"
    return WHY.get(name, "auto-detected")


def announce_lines(root: Path) -> list:
    """What a command prints so the choice is met, not stumbled over."""
    name, layer = chosen_layer(root)
    pinned = layer != "auto"
    out = ["terminal: {0} — {1}".format(name, why(name, pinned, layer))]
    if not pinned and name != "orca":
        out.append("  ({0})".format(ORCA_NOTE))
    out.append("  change it: tide terminal-adapter --set {0}".format(
        " | ".join(n for n in available_adapters() if n != name)))
    return out


def pin(root: Path, name: str) -> Path:
    """Write *name* into the project's ``.claude/settings.json``; return that path.

    Merge-safe: the file carries the human's own hooks and permissions, so it is
    read, one key is set, and it is written back — never replaced.
    """
    key = (name or "").strip().lower()
    if key not in available_adapters():
        raise AdapterError(
            "unknown terminal adapter {0!r} — available: {1}".format(
                name, ", ".join(available_adapters())))
    path = harness.settings_path(root)
    data = _settings(root) or {}
    data[SETTINGS_KEY] = key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def unpin(root: Path) -> Optional[Path]:
    """Drop the pin so auto-detection decides again; None when nothing was pinned."""
    path = harness.settings_path(root)
    data = _settings(root)
    if not isinstance(data, dict) or SETTINGS_KEY not in data:
        return None
    del data[SETTINGS_KEY]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def pin_global(name: str) -> Path:
    """Write *name* as the GLOBAL default into ``~/.claude/settings.json``.

    Merge-safe and conservative: that file is the human's whole Claude config, so an
    unreadable one is NOT overwritten (error instead); otherwise one key is set and
    everything else is written back untouched (a ``.bak`` copy rides along).
    """
    key = (name or "").strip().lower()
    if key not in available_adapters():
        raise AdapterError(
            "unknown terminal adapter {0!r} — available: {1}".format(
                name, ", ".join(available_adapters())))
    path = global_settings_path()
    data: dict = {}
    if path.is_file():
        raw = path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            raise AdapterError("{0} is not valid JSON — not touching it".format(path))
        if not isinstance(data, dict):
            raise AdapterError("{0} is not a JSON object — not touching it".format(path))
        path.with_name(path.name + ".bak").write_text(raw, encoding="utf-8")
    data[SETTINGS_KEY] = key
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tide-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def unpin_global() -> Optional[Path]:
    """Drop the global pin; None when nothing was pinned there."""
    path = global_settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or SETTINGS_KEY not in data:
        return None
    del data[SETTINGS_KEY]
    tmp = path.with_name(path.name + ".tide-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _settings(root: Path) -> dict:
    """Parse the project settings.json; ``{}`` when absent or unreadable."""
    path = harness.settings_path(Path(root))
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


# --- CLI -------------------------------------------------------------------

def _cmd_terminal(args) -> int:
    root = Path.cwd()
    if getattr(args, "global_", False):
        if getattr(args, "auto", False):
            path = unpin_global()
            print("tide terminal-adapter: {0}".format(
                "global pin removed" if path else "nothing was pinned globally"))
            return 0
        if getattr(args, "set", None):
            path = pin_global(args.set)
            print("tide terminal-adapter: {0} — pinned globally in {1}".format(
                args.set.strip().lower(), path))
            print("  (a project pin or $TIDE_ADAPTER still wins; "
                  "undo: tide terminal-adapter --auto --global)")
            return 0
    if getattr(args, "auto", False):
        path = unpin(root)
        name, layer = chosen_layer(root)
        print("tide terminal-adapter: {0}".format(
            "pin removed — back to auto-detect" if path else "nothing was pinned"))
        print("  now: {0} — {1}".format(name, why(name, layer != "auto", layer)))
        return 0
    if getattr(args, "set", None):
        path = pin(root, args.set)
        print("tide terminal-adapter: {0} — pinned in {1}".format(args.set.strip().lower(), path))
        print("  back to auto: tide terminal-adapter --auto")
        return 0
    for line in announce_lines(root):
        print("tide " + line if line.startswith("terminal:") else line)
    return 0


def register(subparsers) -> None:
    """Add the top-level ``terminal`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "terminal-adapter",
        help="which terminal opens your sessions (orca|macos|tmux|ao) — show or pin it",
    )
    p.add_argument("--set", metavar="NAME",
                   help="pin one for this project ({0})".format(
                       "|".join(available_adapters())))
    p.add_argument("--auto", action="store_true",
                   help="remove the pin — go back to auto-detect")
    p.add_argument("--global", action="store_true", dest="global_",
                   help="with --set/--auto: the user-wide default in ~/.claude/settings.json "
                        "(every project without its own pin)")
    p.set_defaults(func=_cmd_terminal, _cmd="terminal-adapter")
