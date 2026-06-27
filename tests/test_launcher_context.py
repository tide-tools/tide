"""U13 unit — launcher.context: scoped launch profile + command builder.

The heart of the feature: a fresh session must load ONLY what a project needs.
Default (no context.json) = lean: ``--strict-mcp-config`` and NO ``--mcp-config``,
so zero global MCP servers load. A profile can opt into a scoped mcp-config, a
tool allow-list, and extra flags.
"""

from __future__ import annotations

import json

from tide.launcher import context


# --- pure build_launch_command ---------------------------------------------

def test_default_lean_profile_is_strict_with_no_mcp_config():
    cmd = context.build_launch_command("/tmp/seed.md", dict(context.DEFAULT_PROFILE))
    assert cmd[0] == "claude"
    assert "--strict-mcp-config" in cmd
    # the clean default: strict scoping with NO --mcp-config ⇒ no global MCP loads
    assert "--mcp-config" not in cmd
    assert "--allowedTools" not in cmd
    # seed delivered by reference to the persisted file
    assert cmd[-2:] == ["--append-system-prompt", "@/tmp/seed.md"]


def test_profile_with_mcp_config_and_allowed_tools_includes_them():
    profile = {
        "strict_mcp": True,
        "mcp_config": "/cfg/scoped.json",
        "allowed_tools": ["Read", "Edit", "Bash"],
        "extra_args": ["--add-dir", "/code"],
    }
    cmd = context.build_launch_command("/tmp/s.md", profile)
    assert "--strict-mcp-config" in cmd
    # scoped mcp-config is loaded (the only MCP servers the session sees)
    i = cmd.index("--mcp-config")
    assert cmd[i + 1] == "/cfg/scoped.json"
    # allow-list joined comma-separated
    j = cmd.index("--allowedTools")
    assert cmd[j + 1] == "Read,Edit,Bash"
    # extra args carried verbatim, before the seed reference
    assert "--add-dir" in cmd and "/code" in cmd
    assert cmd[-2:] == ["--append-system-prompt", "@/tmp/s.md"]


def test_strict_mcp_false_drops_the_flag():
    profile = {"strict_mcp": False, "mcp_config": None, "allowed_tools": None, "extra_args": []}
    cmd = context.build_launch_command("/tmp/s.md", profile)
    assert "--strict-mcp-config" not in cmd


# --- load_profile (disk) ----------------------------------------------------

def test_load_profile_absent_file_is_lean_default(tmp_project):
    profile = context.load_profile(tmp_project)
    assert profile == context.DEFAULT_PROFILE
    cmd = context.build_launch_command("/tmp/s.md", profile)
    assert "--strict-mcp-config" in cmd and "--mcp-config" not in cmd


def test_load_profile_reads_context_json(tmp_project):
    from tide import paths

    paths.context_file(tmp_project).write_text(
        json.dumps(
            {"mcp_config": "/cfg/x.json", "allowed_tools": ["Read"], "extra_args": ["--debug"]}
        ),
        encoding="utf-8",
    )
    profile = context.load_profile(tmp_project)
    assert profile["mcp_config"] == "/cfg/x.json"
    assert profile["allowed_tools"] == ["Read"]
    assert profile["extra_args"] == ["--debug"]
    assert profile["strict_mcp"] is True  # default preserved


def test_load_profile_malformed_falls_back_to_lean(tmp_project):
    from tide import paths

    paths.context_file(tmp_project).write_text("{ not json", encoding="utf-8")
    # a broken config must NEVER widen what loads — fall back to the strict floor
    assert context.load_profile(tmp_project) == context.DEFAULT_PROFILE


def test_load_profile_ignores_wrong_types(tmp_project):
    from tide import paths

    paths.context_file(tmp_project).write_text(
        json.dumps({"strict_mcp": "yes", "allowed_tools": "Read", "mcp_config": 5}),
        encoding="utf-8",
    )
    profile = context.load_profile(tmp_project)
    # all malformed values dropped → lean default
    assert profile == context.DEFAULT_PROFILE


# --- render + CLI -----------------------------------------------------------

def test_render_profile_shows_command(tmp_project):
    out = context.render_profile(tmp_project)
    assert "strict_mcp:" in out
    assert "claude --strict-mcp-config" in out


def test_cli_context_show_prints_scoped_command(tmp_project, monkeypatch, capsys):
    from tide import cli

    monkeypatch.chdir(tmp_project)
    rc = cli.main(["context", "show"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "claude --strict-mcp-config" in out
    assert "no global MCP" in out


# --- loading strategy: read_first + surface_on_entry ------------------------

def test_strategy_defaults_are_compute_readfirst_and_surface_on(tmp_project):
    profile = context.load_profile(tmp_project)
    assert profile["read_first"] is None  # None ⇒ compute the default
    assert profile["surface_on_entry"] is True


def test_load_profile_reads_strategy_keys(tmp_project):
    from tide import paths

    paths.context_file(tmp_project).write_text(
        json.dumps({"read_first": ["CLAUDE.md", "docs/ARCH.md"], "surface_on_entry": False}),
        encoding="utf-8",
    )
    profile = context.load_profile(tmp_project)
    assert profile["read_first"] == ["CLAUDE.md", "docs/ARCH.md"]
    assert profile["surface_on_entry"] is False


def test_strategy_keys_round_trip_with_tool_keys(tmp_project):
    # the two halves coexist in one file — chandler's tool keys and our strategy keys
    from tide import paths

    paths.context_file(tmp_project).write_text(
        json.dumps({"mcp_config": "/cfg/x.json", "read_first": ["A.md"], "surface_on_entry": False}),
        encoding="utf-8",
    )
    profile = context.load_profile(tmp_project)
    assert profile["mcp_config"] == "/cfg/x.json"  # tool half preserved
    assert profile["read_first"] == ["A.md"]  # strategy half preserved


def test_strategy_wrong_types_fall_back_to_defaults(tmp_project):
    from tide import paths

    paths.context_file(tmp_project).write_text(
        json.dumps({"read_first": "CLAUDE.md", "surface_on_entry": "yes"}),
        encoding="utf-8",
    )
    profile = context.load_profile(tmp_project)
    assert profile["read_first"] is None
    assert profile["surface_on_entry"] is True


def test_resolve_read_first_default_only_includes_existing(tmp_project):
    # tmp_project has canon/CANON.md but NO CLAUDE.md → default surfaces only canon
    profile = context.load_profile(tmp_project)
    reads = context.resolve_read_first(tmp_project, profile)
    assert any("CANON.md" in r for r in reads)
    assert "CLAUDE.md" not in reads  # not on disk → not in computed default

    (tmp_project / "CLAUDE.md").write_text("# demo\n", encoding="utf-8")
    reads2 = context.resolve_read_first(tmp_project, profile)
    assert reads2[0] == "CLAUDE.md"  # now present → leads the order


def test_resolve_read_first_explicit_is_verbatim_even_if_missing(tmp_project):
    profile = dict(context.DEFAULT_PROFILE)
    profile["read_first"] = ["does/not/exist.md"]
    # explicit list is honoured verbatim — a missing file is a signal, not filtered
    assert context.resolve_read_first(tmp_project, profile) == ["does/not/exist.md"]


def test_render_read_first_marks_missing(tmp_project):
    profile = dict(context.DEFAULT_PROFILE)
    profile["read_first"] = ["does/not/exist.md"]
    out = context.render_read_first(tmp_project, profile)
    assert "does/not/exist.md  (missing)" in out


def test_render_enter_surfaces_open_arcs_and_candidates(tmp_project, monkeypatch, capsys):
    from tide import cli

    monkeypatch.chdir(tmp_project)
    cli.main(["arc", "new", "wire-the-thing"])
    cli.main(["candidate", "add", "a-future-idea"])
    capsys.readouterr()  # drain creation output

    rc = cli.main(["context", "show"])
    assert rc == 0
    out = capsys.readouterr().out
    # tool block still present
    assert "claude --strict-mcp-config" in out
    # read order present
    assert "read first" in out
    # the on-entry work summary names the open arc + the candidate + questions line
    assert "open arcs (1)" in out
    assert "wire-the-thing" in out
    assert "candidates (1)" in out
    assert "a-future-idea" in out
    assert "open questions: none" in out


def test_render_enter_surfaces_open_questions(tmp_project, monkeypatch, capsys):
    from tide import cli

    monkeypatch.chdir(tmp_project)
    cli.main(["arc", "new", "needs-input"])
    cli.main(["contract", "new", "needs-input"])
    cli.main(["contract", "ask", "needs-input", "which-database-do-we-target"])
    capsys.readouterr()  # drain

    cli.main(["context", "show"])
    out = capsys.readouterr().out
    assert "open questions (1)" in out
    assert "which-database-do-we-target" in out


def test_render_enter_surface_off_hides_summary(tmp_project, monkeypatch, capsys):
    from tide import cli, paths

    paths.context_file(tmp_project).write_text(
        json.dumps({"surface_on_entry": False}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_project)
    cli.main(["arc", "new", "hidden-arc"])
    capsys.readouterr()

    cli.main(["context", "show"])
    out = capsys.readouterr().out
    assert "open arcs" not in out  # surfacing suppressed
    assert "read first" in out  # read order still shown


def test_render_enter_notes_legacy_arcs_dir(tmp_project, monkeypatch, capsys):
    from tide import cli

    (tmp_project / ".arcs").mkdir()
    monkeypatch.chdir(tmp_project)
    cli.main(["context", "show"])
    out = capsys.readouterr().out
    assert "legacy .arcs/ present" in out
