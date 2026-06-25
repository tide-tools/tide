"""terminal — ``tide terminal``: clean logged-in seeded session in THIS terminal.

The native absorption of ``~/.local/bin/tide-go``. The build must reuse the scoped
``context.build_launch_command`` (strict MCP, seed-by-reference), add
``--disable-slash-commands`` to trim skill noise, and NEVER add ``--bare`` (that
would drop the OAuth auth that lives in ``~/.claude.json``). Verified via the pure
builder + ``--dry-run`` (no live nested session, no exec).
"""

from __future__ import annotations

from pathlib import Path

from tide.launcher import context, terminal


# --- pure build_terminal_command -------------------------------------------

def test_terminal_command_is_scoped_and_seeded_without_bare():
    profile = dict(context.DEFAULT_PROFILE)
    cmd = terminal.build_terminal_command("/tmp/seed.md", profile)
    assert cmd[0] == "claude"
    # scoped: zero global MCP servers
    assert "--strict-mcp-config" in cmd
    assert "--mcp-config" not in cmd
    # skill noise trimmed, like tide-go
    assert "--disable-slash-commands" in cmd
    # interactive head: permission prompts skipped by default (deliberate operator choice)
    assert "--dangerously-skip-permissions" in cmd
    # seed delivered by reference at the tail
    assert cmd[-2:] == ["--append-system-prompt", "@/tmp/seed.md"]
    # auth-preserving: --bare would drop OAuth (~/.claude.json) → never present
    assert "--bare" not in cmd


def test_terminal_command_reuses_context_builder_shape():
    profile = dict(context.DEFAULT_PROFILE)
    base = context.build_launch_command("/tmp/s.md", profile)
    full = terminal.build_terminal_command("/tmp/s.md", profile)
    # every token of the scoped builder survives; we only ADD the head-session flags
    assert set(base).issubset(set(full))
    assert [t for t in full if t not in base] == [
        "--disable-slash-commands",
        "--dangerously-skip-permissions",
    ]


def test_skip_permissions_is_head_only_not_in_shared_builder():
    # the autonomous/spawned path uses context.build_launch_command — it must NEVER
    # carry skip-permissions; only the in-terminal head (build_terminal_command) opts in.
    profile = dict(context.DEFAULT_PROFILE)
    spawned = context.build_launch_command("/tmp/s.md", profile)
    assert "--dangerously-skip-permissions" not in spawned


def test_skip_permissions_can_be_turned_off():
    profile = dict(context.DEFAULT_PROFILE)
    cmd = terminal.build_terminal_command("/tmp/s.md", profile, skip_permissions=False)
    assert "--dangerously-skip-permissions" not in cmd


def test_disable_slash_can_be_turned_off():
    profile = dict(context.DEFAULT_PROFILE)
    cmd = terminal.build_terminal_command("/tmp/s.md", profile, disable_slash=False)
    assert "--disable-slash-commands" not in cmd


def test_disable_slash_not_duplicated():
    profile = {"strict_mcp": True, "mcp_config": None, "allowed_tools": None,
               "extra_args": ["--disable-slash-commands"]}
    cmd = terminal.build_terminal_command("/tmp/s.md", profile)
    assert cmd.count("--disable-slash-commands") == 1


# --- seed resolution --------------------------------------------------------

def test_seed_prefers_explicit_override(tmp_path):
    seed = tmp_path / "my-seed.md"
    seed.write_text("hi", encoding="utf-8")
    assert terminal.resolve_seed_file(tmp_path, str(seed)) == str(seed.resolve())


def test_seed_falls_back_to_migrate_then_resume(tmp_path):
    # RESUME present but MIGRATE wins when both exist
    (tmp_path / "RESUME.md").write_text("resume", encoding="utf-8")
    assert terminal.resolve_seed_file(tmp_path).endswith("RESUME.md")
    (tmp_path / "MIGRATE.md").write_text("migrate", encoding="utf-8")
    assert terminal.resolve_seed_file(tmp_path).endswith("MIGRATE.md")


def test_seed_generates_minimal_when_none_present(tmp_path):
    out = terminal.resolve_seed_file(tmp_path)
    assert Path(out).is_file()
    assert "clean" in Path(out).read_text(encoding="utf-8").lower()


# --- control-home resolution ------------------------------------------------

def test_find_control_home_climbs_to_roster(tmp_control_home):
    nested = tmp_control_home / "sub" / "deep"
    nested.mkdir(parents=True)
    assert terminal.find_control_home(nested) == tmp_control_home.resolve()


# --- CLI dry-run ------------------------------------------------------------

def test_cli_terminal_dry_run_prints_command(tmp_control_home, monkeypatch, capsys):
    from tide import cli

    (tmp_control_home / "MIGRATE.md").write_text("# migrate", encoding="utf-8")
    monkeypatch.chdir(tmp_control_home)
    rc = cli.main(["terminal", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "claude --disable-slash-commands --dangerously-skip-permissions --strict-mcp-config" in out
    assert "MIGRATE.md" in out
    # the built command line must not carry --bare (auth-preserving)
    cmd_line = next(ln for ln in out.splitlines() if "command:" in ln)
    assert "--bare" not in cmd_line
    assert "auth:" in out  # the auth-kept note is shown
    assert "perms:" in out  # the skip-permissions note is shown
    assert "deliberate operator choice" in out


def test_cli_terminal_dry_run_no_skip_permissions_flag(tmp_control_home, monkeypatch, capsys):
    from tide import cli

    (tmp_control_home / "MIGRATE.md").write_text("# migrate", encoding="utf-8")
    monkeypatch.chdir(tmp_control_home)
    rc = cli.main(["terminal", "--dry-run", "--no-skip-permissions"])
    assert rc == 0
    cmd_line = next(ln for ln in capsys.readouterr().out.splitlines() if "command:" in ln)
    assert "--dangerously-skip-permissions" not in cmd_line
