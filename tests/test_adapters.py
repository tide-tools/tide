"""U11 unit — adapters: registry, tmux/orca dry-run, unknown raises, auto-detect.

Auto-detect contract (updated when macos adapter was added):
  - get_adapter() / resolve_from_settings(None) now call default_adapter_name()
    instead of hard-coding "orca".
  - default_adapter_name(): orca-on-PATH → "orca"; else Darwin → "macos"; else "tmux".
  - Explicit adapter names (via settings or --adapter) still always win.
  - Unknown names still raise AdapterError.

Tests that previously asserted ``get_adapter() is OrcaAdapter`` unconditionally
have been updated to mock shutil.which so they test the contract, not the
machine state.
"""

from __future__ import annotations

import shutil

import pytest

from tide import adapters
from tide.adapters import base
from tide.adapters.orca import OrcaAdapter
from tide.adapters.terminal_app import TerminalAppAdapter
from tide.adapters.tmux import TmuxAdapter


# --- registry --------------------------------------------------------------

def test_get_adapter_default_auto_detects_orca_when_on_path(monkeypatch):
    """When orca binary is present on PATH, the auto-detect default is OrcaAdapter."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca" if name == "orca" else None)
    a = adapters.get_adapter()
    assert isinstance(a, OrcaAdapter)
    assert a.name == "orca"


def test_orca_uses_terminal_create_not_keystroke():
    """Orca tabs open via `orca terminal create`, never AppleScript keystroke.

    keystroke types through the active keyboard layout (the φφφ bug) and can hit
    the wrong window; the native CLI runs the command directly in the right tab.
    """
    argv = OrcaAdapter().build_command(
        cwd="/Users/g/Documents/projects/mitehq",
        command=["claude", "--dangerously-skip-permissions", "@/tmp/seed.md"],
        title="tide-mitehq",
    )
    assert argv[:3] == ["orca", "terminal", "create"]
    assert "--worktree" in argv
    assert "path:/Users/g/Documents/projects/mitehq" in argv
    # the scoped command rides on --command as one shell-quoted string
    i = argv.index("--command")
    assert "claude --dangerously-skip-permissions @/tmp/seed.md" == argv[i + 1]
    assert "--focus" in argv


def test_get_adapter_by_name():
    assert isinstance(adapters.get_adapter("tmux"), TmuxAdapter)
    assert isinstance(adapters.get_adapter("ORCA"), OrcaAdapter)  # case-insensitive


def test_unknown_adapter_raises_listing_available():
    with pytest.raises(adapters.AdapterError) as exc:
        adapters.get_adapter("kitty")
    msg = str(exc.value)
    assert "kitty" in msg
    # the error lists what IS available (now includes macos)
    assert "orca" in msg and "tmux" in msg and "macos" in msg


def test_available_adapters_lists_three_in_order():
    """Registry now contains orca → macos → tmux in that insertion order."""
    assert adapters.available_adapters() == ["orca", "macos", "tmux"]


def test_resolve_from_settings_reads_terminal_adapter_key(monkeypatch):
    assert isinstance(adapters.resolve_from_settings({"terminal_adapter": "tmux"}), TmuxAdapter)
    # absent / blank / non-dict → auto-detect; mock orca absent + Darwin → macos
    monkeypatch.setattr(shutil, "which", lambda _: None)
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")
    assert isinstance(adapters.resolve_from_settings({}), TerminalAppAdapter)
    assert isinstance(adapters.resolve_from_settings({"terminal_adapter": "  "}), TerminalAppAdapter)
    assert isinstance(adapters.resolve_from_settings(None), TerminalAppAdapter)


# the scoped launch command the launcher would build; adapters carry it verbatim.
_LAUNCH = [base.SESSION_PROGRAM, "--strict-mcp-config", "--append-system-prompt", "@/tmp/seed.md"]


# --- tmux dry-run (the build-blueprint's required test) --------------------

def test_tmux_spawn_dry_run_builds_new_window_without_executing():
    a = TmuxAdapter()
    res = a.spawn(command=_LAUNCH, cwd="/p/focus", title="tide-focus", dry_run=True)
    assert res.ok is True
    assert "dry-run" in res.detail.lower()
    # single command: the new-window invocation, scoped to cwd + title, carrying
    # the launcher's scoped argv verbatim as the window program.
    assert len(res.commands) == 1
    new_window = res.commands[0]
    assert new_window[:2] == ["tmux", "new-window"]
    assert "-c" in new_window and "/p/focus" in new_window
    assert "-n" in new_window and "tide-focus" in new_window
    # the scoped claude argv (strict MCP + seed reference) rides at the tail
    assert base.SESSION_PROGRAM in new_window
    assert "--strict-mcp-config" in new_window
    assert "--append-system-prompt" in new_window


def test_tmux_build_commands_is_pure():
    a = TmuxAdapter()
    cmds = a.build_commands(command=_LAUNCH, cwd="/c", title="t")
    assert len(cmds) == 1
    assert cmds[0][0] == "tmux"
    assert cmds[0][-len(_LAUNCH):] == _LAUNCH  # command carried verbatim


# --- orca dry-run ----------------------------------------------------------

def test_orca_spawn_dry_run_builds_terminal_create_without_executing():
    a = OrcaAdapter()
    res = a.spawn(command=_LAUNCH, cwd="/p/x", title="tide-x", dry_run=True)
    assert res.ok is True
    cmd = res.commands[0]
    assert cmd[:3] == ["orca", "terminal", "create"]
    assert "path:/p/x" in cmd
    # the scoped command (with flags) rides on --command
    launch = cmd[cmd.index("--command") + 1]
    assert base.SESSION_PROGRAM in launch
    assert "--strict-mcp-config" in launch


# --- orca self-heal (register + retry on selector_not_found) ---------------

def _selector_error():
    """A CalledProcessError mirroring Orca's unregistered-repo failure."""
    import json as _json
    import subprocess as _sp

    payload = _json.dumps({"error": {"code": "selector_not_found"}})
    return _sp.CalledProcessError(returncode=1, cmd=["orca"], output=payload, stderr=payload)


def test_orca_self_heals_unregistered_repo(monkeypatch):
    """A selector_not_found failure → register the repo once, then retry → ok."""
    import subprocess as _sp

    a = OrcaAdapter()
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca")

    calls = []

    def fake_run(argv, **kwargs):
        if argv[:1] == ["git"]:  # worktree preflight probe — repo is ready
            return _sp.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")
        calls.append(argv)
        # 1st orca call: terminal create → fail with selector_not_found.
        # 2nd: repo add → ok. 3rd: terminal create retry → ok.
        if argv[:3] == ["orca", "terminal", "create"] and len(calls) == 1:
            raise _selector_error()
        return _sp.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_sp, "run", fake_run)

    res = a.spawn(command=_LAUNCH, cwd="/p/fresh", title="tide-fresh")

    assert res.ok is True
    assert "registering repo" in res.detail.lower()
    # exactly: create (fail) → repo add → create (retry)
    assert len(calls) == 3
    assert calls[1] == ["orca", "repo", "add", "--path", "/p/fresh"]
    assert calls[2][:3] == ["orca", "terminal", "create"]


def test_orca_spawn_returns_terminal_handle_from_json(monkeypatch):
    """spawn parses the created terminal's handle (--json) into ref — the registry key (cand 94)."""
    import json as _json
    import subprocess as _sp

    a = OrcaAdapter()
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca")

    def fake_run(argv, **kwargs):
        if argv[:1] == ["git"]:
            return _sp.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")
        out = _json.dumps({"result": {"terminal": {"handle": "term_xyz789"}}})
        return _sp.CompletedProcess(args=argv, returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(_sp, "run", fake_run)
    res = a.spawn(command=_LAUNCH, cwd="/p/x", title="tide-x")
    assert res.ok is True
    assert res.ref == "term_xyz789"       # the orca handle, not the title
    # and --json is on the create argv
    assert "--json" in a.build_command(cwd="/p/x", command=_LAUNCH)


def test_orca_spawn_falls_back_to_title_when_handle_unparseable(monkeypatch):
    import subprocess as _sp

    a = OrcaAdapter()
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca")

    def fake_run(argv, **kwargs):
        if argv[:1] == ["git"]:
            return _sp.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")
        return _sp.CompletedProcess(args=argv, returncode=0, stdout="not-json", stderr="")

    monkeypatch.setattr(_sp, "run", fake_run)
    res = a.spawn(command=_LAUNCH, cwd="/p/x", title="tide-x")
    assert res.ok is True and res.ref == "tide-x"  # legible fallback, spawn still succeeds


def test_orca_non_selector_failure_does_not_register_or_retry(monkeypatch):
    """A non-selector failure degrades to ok=False without registering/retrying."""
    import subprocess as _sp

    a = OrcaAdapter()
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca")

    calls = []

    def fake_run(argv, **kwargs):
        if argv[:1] == ["git"]:  # worktree preflight probe — repo is ready
            return _sp.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")
        calls.append(argv)
        raise _sp.CalledProcessError(returncode=1, cmd=argv, output="boom", stderr="boom")

    monkeypatch.setattr(_sp, "run", fake_run)

    res = a.spawn(command=_LAUNCH, cwd="/p/x", title="tide-x")

    assert res.ok is False
    # only the single terminal-create attempt — no repo add, no retry.
    assert len(calls) == 1
    assert calls[0][:3] == ["orca", "terminal", "create"]


def test_orca_self_heal_retry_still_fails_returns_graceful(monkeypatch):
    """If the retry also fails, the adapter returns the graceful ok=False result."""
    import subprocess as _sp

    a = OrcaAdapter()
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca")

    calls = []

    def fake_run(argv, **kwargs):
        if argv[:1] == ["git"]:  # worktree preflight probe — repo is ready
            return _sp.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")
        calls.append(argv)
        if argv[:3] == ["orca", "terminal", "create"]:
            raise _selector_error()
        return _sp.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_sp, "run", fake_run)

    res = a.spawn(command=_LAUNCH, cwd="/p/x", title="tide-x")

    assert res.ok is False
    # create (fail) → repo add (ok) → create retry (fail again) = 3 calls.
    assert len(calls) == 3


# --- SpawnResult / helpers -------------------------------------------------

def test_spawn_result_defaults():
    r = base.SpawnResult(ok=True)
    assert r.ref is None and r.detail == "" and r.commands == []


def test_safe_title_is_never_empty():
    assert base.safe_title("") == "tide"
    assert base.safe_title("a b/c") == "a-b-c"
