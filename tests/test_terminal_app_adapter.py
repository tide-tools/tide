"""Tests for tide.adapters.terminal_app — macOS Terminal.app adapter.

TDD RED phase: all tests here are written BEFORE the adapter implementation.
They document the expected contract:

  1. dry_run shape — returns a single ``["osascript", "-e", <script>]`` command,
     does NOT execute subprocess, carries cwd + full claude argv in the script.
  2. Escaping — paths with spaces, double-quotes, and backslashes survive the two
     string-quoting layers (POSIX shell via shlex + AppleScript string via \\-escape).
  3. Registry — ``available_adapters()`` includes "macos" after "orca", before "tmux";
     ``get_adapter("macos")`` returns a TerminalAppAdapter instance.
  4. Auto-detect — ``get_adapter(None)`` runs orca→macos→tmux preference in order;
     explicit names always win; unknown names still raise ``AdapterError``.
  5. Real-spawn guard — missing osascript returns ``ok=False``; no exception raised.
"""

from __future__ import annotations

import shlex
import shutil
import sys
from typing import List

import pytest

from tide.adapters import AdapterError, available_adapters, get_adapter, resolve_from_settings
from tide.adapters.base import SESSION_PROGRAM, SpawnResult
from tide.adapters.orca import OrcaAdapter
from tide.adapters.tmux import TmuxAdapter

# the launcher's scoped argv (built by tide.launcher.context) — adapters carry verbatim.
_LAUNCH: List[str] = [
    SESSION_PROGRAM,
    "--strict-mcp-config",
    "--append-system-prompt",
    "@/tmp/seed.md",
]


# ---------------------------------------------------------------------------
# Import guard — must resolve BEFORE any tests run
# ---------------------------------------------------------------------------

def test_import_terminal_app_adapter():
    """TerminalAppAdapter is importable from tide.adapters.terminal_app."""
    from tide.adapters.terminal_app import TerminalAppAdapter  # noqa: F401


# ---------------------------------------------------------------------------
# 1. Dry-run shape
# ---------------------------------------------------------------------------

class TestDryRunShape:
    """The dry-run path builds an ``osascript -e <script>`` command without executing."""

    def _adapter(self):
        from tide.adapters.terminal_app import TerminalAppAdapter
        return TerminalAppAdapter()

    def test_returns_ok_true(self):
        res = self._adapter().spawn(command=_LAUNCH, cwd="/p/focus", title="tide-focus", dry_run=True)
        assert res.ok is True

    def test_detail_mentions_dry_run(self):
        res = self._adapter().spawn(command=_LAUNCH, cwd="/p/focus", title="tide-focus", dry_run=True)
        assert "dry-run" in res.detail.lower()

    def test_exactly_one_command(self):
        """A single osascript invocation; no intermediate shell helpers."""
        res = self._adapter().spawn(command=_LAUNCH, cwd="/p/focus", title="tide-focus", dry_run=True)
        assert len(res.commands) == 1

    def test_command_starts_with_osascript_dash_e(self):
        res = self._adapter().spawn(command=_LAUNCH, cwd="/p/focus", title="tide-focus", dry_run=True)
        cmd = res.commands[0]
        assert cmd[0] == "osascript"
        assert cmd[1] == "-e"
        assert isinstance(cmd[2], str) and len(cmd[2]) > 0

    def test_script_drives_terminal_app(self):
        """The AppleScript targets 'Terminal' (macOS Terminal.app)."""
        res = self._adapter().spawn(command=_LAUNCH, cwd="/p/focus", title="tide-focus", dry_run=True)
        script = res.commands[0][2]
        assert "Terminal" in script

    def test_script_contains_cwd(self):
        res = self._adapter().spawn(command=_LAUNCH, cwd="/p/focus", title="tide-focus", dry_run=True)
        script = res.commands[0][2]
        # The cwd path must appear somewhere (possibly quoted) in the script.
        assert "focus" in script

    def test_script_contains_session_program(self):
        res = self._adapter().spawn(command=_LAUNCH, cwd="/p/x", title="tide-x", dry_run=True)
        script = res.commands[0][2]
        assert SESSION_PROGRAM in script

    def test_scoped_flags_are_carried_verbatim(self):
        """All launcher argv flags must survive into the AppleScript string."""
        res = self._adapter().spawn(command=_LAUNCH, cwd="/p/x", title="tide-x", dry_run=True)
        script = res.commands[0][2]
        assert "--strict-mcp-config" in script
        assert "--append-system-prompt" in script
        assert "@/tmp/seed.md" in script

    def test_ref_is_safe_title(self):
        res = self._adapter().spawn(command=_LAUNCH, cwd="/p/x", title="my title", dry_run=True)
        # safe_title("my title") → "my-title"
        assert res.ref == "my-title"

    def test_dry_run_does_not_call_subprocess(self, monkeypatch):
        called: list = []
        monkeypatch.setattr("subprocess.run", lambda *a, **k: called.append(True))
        self._adapter().spawn(command=_LAUNCH, cwd="/p/x", title="t", dry_run=True)
        assert not called, "dry_run=True must NEVER execute subprocess"


# ---------------------------------------------------------------------------
# 2. Escaping — the crux
# ---------------------------------------------------------------------------

class TestEscaping:
    """Shell/AppleScript two-layer escaping is correct for tricky inputs."""

    def _script(self, cwd: str, command: List[str] = None) -> str:
        from tide.adapters.terminal_app import TerminalAppAdapter
        cmd = command if command is not None else _LAUNCH
        res = TerminalAppAdapter().spawn(command=cmd, cwd=cwd, title="t", dry_run=True)
        return res.commands[0][2]

    def test_cwd_with_spaces_survives(self):
        """A cwd containing spaces appears correctly in the script (quoted by shlex)."""
        cwd = "/Users/john doe/my project"
        script = self._script(cwd)
        # shlex.quote produces '/Users/john doe/my project'; the single-quote form
        # embeds the space safely without needing AppleScript escaping.
        assert "john doe" in script
        assert "my project" in script

    def test_cwd_with_spaces_produces_valid_applescript_string(self):
        """The AppleScript string literal must open with 'do script "' and never
        contain a raw (unescaped) double-quote inside the payload — spaces in cwd
        are handled by shlex single-quoting, not by introducing extra "s."""
        cwd = "/Users/jane/path with spaces"
        script = self._script(cwd)
        # The template: tell application "Terminal" to do script "<payload>"
        # Extract the payload between the first do script " and the closing ".
        # Payload starts after `do script "` and ends before the last `"`
        payload_start = script.index('do script "') + len('do script "')
        payload = script[payload_start:-1]  # strip final "
        # No raw (unescaped) double-quote inside payload after properly escaping.
        # Any literal " in payload must be preceded by \
        import re
        unescaped = re.findall(r'(?<!\\)"', payload)
        assert not unescaped, (
            "unescaped double-quote found in AppleScript payload: {0!r}".format(payload)
        )

    def test_cwd_with_backslash_survives(self):
        """A backslash in cwd is double-escaped for the AppleScript string layer."""
        cwd = "/p/back\\slash"
        script = self._script(cwd)
        # The backslash must be escaped: \ → \\ in the AppleScript string.
        assert "\\\\" in script  # the literal \\ sequence appears in the AppleScript

    def test_cwd_with_double_quote_is_escaped(self):
        """A double-quote in cwd must be \\"-escaped in the AppleScript string."""
        cwd = '/path/with"quote'
        # shlex.quote('/path/with"quote') → '\'"/path/with\\"quote"\'' — actually
        # shlex handles " inside single-quotes normally: '/path/with"quote' is valid
        # as a shell single-quoted string because " is not special inside single-quotes.
        # BUT we must still escape it at the AppleScript string layer.
        script = self._script(cwd)
        # Whatever form it takes, the script must not have a bare " inside the payload.
        payload_start = script.index('do script "') + len('do script "')
        payload = script[payload_start:-1]
        import re
        unescaped = re.findall(r'(?<!\\)"', payload)
        assert not unescaped, "double-quote in cwd must be escaped in AppleScript payload"

    def test_multi_word_command_token_with_spaces_is_quoted(self):
        """A command token containing spaces is shell-quoted so the shell sees one arg."""
        cmd = [SESSION_PROGRAM, "--system-prompt", "hello world"]
        script = self._script("/p/x", command=cmd)
        # "hello world" must be shell-quoted in the script (e.g., 'hello world')
        assert "hello world" in script  # the text must be present
        # It must NOT appear as raw hello world without quotes; shlex would have quoted it.
        # We verify the shell layer sees it correctly by checking shlex.quote was applied.
        assert shlex.quote("hello world") in script

    def test_build_commands_pure_idempotent(self):
        """build_commands returns the same result for the same inputs every time."""
        from tide.adapters.terminal_app import TerminalAppAdapter
        a = TerminalAppAdapter()
        c1 = a.build_commands(command=_LAUNCH, cwd="/a/b", title="same")
        c2 = a.build_commands(command=_LAUNCH, cwd="/a/b", title="same")
        assert c1 == c2, "build_commands must be pure/idempotent"

    def test_build_commands_does_not_mutate_command_list(self):
        """build_commands must not modify the input argv."""
        from tide.adapters.terminal_app import TerminalAppAdapter
        original = list(_LAUNCH)
        TerminalAppAdapter().build_commands(command=original, cwd="/x", title="t")
        assert original == list(_LAUNCH), "build_commands must not mutate command"


# ---------------------------------------------------------------------------
# 3. Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_available_adapters_includes_macos(self):
        assert "macos" in available_adapters()

    def test_available_adapters_orca_before_macos_before_tmux(self):
        order = available_adapters()
        assert order.index("orca") < order.index("macos"), "'orca' must come before 'macos'"
        assert order.index("macos") < order.index("tmux"), "'macos' must come before 'tmux'"

    def test_get_adapter_macos_returns_terminal_app_adapter(self):
        from tide.adapters.terminal_app import TerminalAppAdapter
        a = get_adapter("macos")
        assert isinstance(a, TerminalAppAdapter)
        assert a.name == "macos"

    def test_get_adapter_macos_case_insensitive(self):
        from tide.adapters.terminal_app import TerminalAppAdapter
        assert isinstance(get_adapter("MACOS"), TerminalAppAdapter)
        assert isinstance(get_adapter("MacOS"), TerminalAppAdapter)

    def test_unknown_adapter_still_raises_with_macos_in_listing(self):
        """AdapterError message lists all adapters including new 'macos' entry."""
        with pytest.raises(AdapterError) as exc:
            get_adapter("kitty")
        msg = str(exc.value)
        assert "kitty" in msg
        assert "orca" in msg
        assert "macos" in msg
        assert "tmux" in msg


# ---------------------------------------------------------------------------
# 4. Auto-detect default resolution
# ---------------------------------------------------------------------------

class TestAutoDetect:
    """default_adapter_name() resolves: orca-on-PATH → orca; else Darwin → macos; else → tmux."""

    def test_default_adapter_name_orca_when_orca_on_path(self, monkeypatch):
        """orca binary present → default is 'orca'."""
        from tide.adapters import default_adapter_name
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca" if name == "orca" else None)
        assert default_adapter_name() == "orca"

    def test_default_adapter_name_macos_when_orca_absent_darwin(self, monkeypatch):
        """No orca binary, on Darwin → default is 'macos'."""
        from tide.adapters import default_adapter_name
        monkeypatch.setattr(shutil, "which", lambda _: None)
        monkeypatch.setattr(sys, "platform", "darwin")
        assert default_adapter_name() == "macos"

    def test_default_adapter_name_tmux_when_orca_absent_non_darwin(self, monkeypatch):
        """No orca binary, not Darwin → default is 'tmux'."""
        from tide.adapters import default_adapter_name
        monkeypatch.setattr(shutil, "which", lambda _: None)
        monkeypatch.setattr(sys, "platform", "linux")
        assert default_adapter_name() == "tmux"

    def test_get_adapter_none_returns_orca_when_orca_on_path(self, monkeypatch):
        """get_adapter(None) auto-detects; orca binary present → OrcaAdapter."""
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca" if name == "orca" else None)
        assert isinstance(get_adapter(None), OrcaAdapter)

    def test_get_adapter_none_returns_macos_when_orca_absent_darwin(self, monkeypatch):
        """get_adapter(None) on Darwin without orca binary → TerminalAppAdapter."""
        from tide.adapters.terminal_app import TerminalAppAdapter
        monkeypatch.setattr(shutil, "which", lambda _: None)
        monkeypatch.setattr(sys, "platform", "darwin")
        assert isinstance(get_adapter(None), TerminalAppAdapter)

    def test_get_adapter_none_returns_tmux_when_orca_absent_non_darwin(self, monkeypatch):
        """get_adapter(None) on Linux without orca → TmuxAdapter."""
        monkeypatch.setattr(shutil, "which", lambda _: None)
        monkeypatch.setattr(sys, "platform", "linux")
        assert isinstance(get_adapter(None), TmuxAdapter)

    def test_explicit_name_wins_even_when_orca_present(self, monkeypatch):
        """Passing an explicit name bypasses auto-detect completely."""
        from tide.adapters.terminal_app import TerminalAppAdapter
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca" if name == "orca" else None)
        monkeypatch.setattr(sys, "platform", "darwin")
        a = get_adapter("macos")
        assert isinstance(a, TerminalAppAdapter), "explicit 'macos' must win over orca-present auto-detect"

    def test_explicit_tmux_wins_over_auto_detect(self, monkeypatch):
        """Pinning 'tmux' explicitly is respected on Darwin."""
        monkeypatch.setattr(shutil, "which", lambda _: None)
        monkeypatch.setattr(sys, "platform", "darwin")
        assert isinstance(get_adapter("tmux"), TmuxAdapter)

    def test_unknown_name_raises_adapter_error(self):
        with pytest.raises(AdapterError):
            get_adapter("nonexistent")

    def test_blank_string_triggers_auto_detect(self, monkeypatch):
        """Empty / whitespace-only name triggers auto-detect just like None."""
        monkeypatch.setattr(shutil, "which", lambda name: "/orca" if name == "orca" else None)
        for blank in ("", "   "):
            assert isinstance(get_adapter(blank), OrcaAdapter)

    def test_resolve_from_settings_explicit_wins_over_auto_detect(self, monkeypatch):
        """Settings-pinned adapter beats auto-detect (orca binary present but settings say tmux)."""
        monkeypatch.setattr(shutil, "which", lambda name: "/orca" if name == "orca" else None)
        a = resolve_from_settings({"terminal_adapter": "tmux"})
        assert isinstance(a, TmuxAdapter)

    def test_resolve_from_settings_no_setting_uses_auto_detect_darwin(self, monkeypatch):
        """Absent terminal_adapter key → auto-detect. On Darwin without orca → macos."""
        from tide.adapters.terminal_app import TerminalAppAdapter
        monkeypatch.setattr(shutil, "which", lambda _: None)
        monkeypatch.setattr(sys, "platform", "darwin")
        assert isinstance(resolve_from_settings({}), TerminalAppAdapter)
        assert isinstance(resolve_from_settings(None), TerminalAppAdapter)

    def test_resolve_from_settings_blank_value_uses_auto_detect(self, monkeypatch):
        """Blank terminal_adapter value (e.g. '  ') → auto-detect."""
        from tide.adapters.terminal_app import TerminalAppAdapter
        monkeypatch.setattr(shutil, "which", lambda _: None)
        monkeypatch.setattr(sys, "platform", "darwin")
        a = resolve_from_settings({"terminal_adapter": "   "})
        assert isinstance(a, TerminalAppAdapter)


# ---------------------------------------------------------------------------
# 5. Real-spawn guard (osascript missing → ok=False, no raise)
# ---------------------------------------------------------------------------

class TestRealSpawnDegradation:
    def test_missing_osascript_returns_ok_false(self, monkeypatch):
        """When osascript is not on PATH, spawn returns ok=False gracefully."""
        monkeypatch.setattr(shutil, "which", lambda _: None)
        from tide.adapters.terminal_app import TerminalAppAdapter
        res = TerminalAppAdapter().spawn(command=_LAUNCH, cwd="/p/x", title="t", dry_run=False)
        assert res.ok is False
        assert res.detail  # must include a non-empty human hint
        # commands must still be populated (caller can print what would have run)
        assert res.commands

    def test_missing_osascript_detail_mentions_adapter_alternative(self, monkeypatch):
        """Failure detail must mention an alternative (tmux) so the user knows what to do."""
        monkeypatch.setattr(shutil, "which", lambda _: None)
        from tide.adapters.terminal_app import TerminalAppAdapter
        res = TerminalAppAdapter().spawn(command=_LAUNCH, cwd="/p/x", title="t", dry_run=False)
        assert "tmux" in res.detail.lower() or "adapter" in res.detail.lower()

    def test_missing_osascript_does_not_raise(self, monkeypatch):
        """No exception must propagate when osascript is absent."""
        monkeypatch.setattr(shutil, "which", lambda _: None)
        from tide.adapters.terminal_app import TerminalAppAdapter
        # This call must NOT raise.
        res = TerminalAppAdapter().spawn(command=_LAUNCH, cwd="/p/x", title="t", dry_run=False)
        assert isinstance(res, SpawnResult)
