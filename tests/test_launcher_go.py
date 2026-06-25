"""go — ``tide go``: the light ENTRY dispatcher (symmetric mirror of handoff).

``tide go`` routes the human back INTO tide with one light question — resume a
prior thread, or start new — then delegates the launch to ``tide terminal`` (the
single scoped+skip-perms path). These tests pin the resume classification (the
core logic: ``continue`` shows + seeds from the distil, ``close`` is hidden, no
handoff is ``raw`` from the passport), the menu rendering, and the dry-run wiring
(menus print, terminal launch is built but never exec'd).
"""

from __future__ import annotations

from pathlib import Path

from tide.launcher import go


# --- fixtures: build open arcs with/without handoffs in a control-home ------

def _make_arc(root: Path, name: str, *, goal: str = "do the thing") -> Path:
    """Create an open ``NN-slug`` arc dir with a minimal passport; return its dir."""
    arc = root / ".tide" / "arcs" / name
    arc.mkdir(parents=True, exist_ok=True)
    (arc / "arc.md").write_text(
        "# {0}\n\ngoal: {1}\nstatus: active\n".format(name, goal), encoding="utf-8"
    )
    return arc


def _write_handoff(arc: Path, *, date: str, mode: str, where: str = "") -> Path:
    """Drop a ``workspace/handoff-<date>.md`` distil with the given mode/where."""
    ws = arc / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    body = (
        "# tide handoff — x\n\nmode: {0}\narc: x\ndate: {1}\n\n"
        "## Where we are\n{2}\n"
    ).format(mode, date, where or "(state not distilled — fill before spawning)")
    path = ws / "handoff-{0}.md".format(date)
    path.write_text(body, encoding="utf-8")
    return path


# --- handoff inspection -----------------------------------------------------

def test_latest_handoff_picks_newest_by_date(tmp_control_home):
    arc = _make_arc(tmp_control_home, "01-thread")
    _write_handoff(arc, date="2026-06-20", mode="continue")
    newest = _write_handoff(arc, date="2026-06-25", mode="close")
    assert go.latest_handoff(arc) == newest
    assert go.handoff_mode(newest) == "close"


def test_handoff_oneliner_reads_where_we_are(tmp_control_home):
    arc = _make_arc(tmp_control_home, "01-thread")
    h = _write_handoff(arc, date="2026-06-25", mode="continue", where="picking up the factory build")
    assert go.handoff_oneliner(h) == "picking up the factory build"


def test_handoff_oneliner_empty_on_placeholder(tmp_control_home):
    arc = _make_arc(tmp_control_home, "01-thread")
    h = _write_handoff(arc, date="2026-06-25", mode="continue")  # default placeholder
    assert go.handoff_oneliner(h) == ""


# --- resume classification (the core logic) ---------------------------------

def test_continue_handoff_is_resumable_seeded_from_distil(tmp_control_home):
    arc = _make_arc(tmp_control_home, "01-thread")
    _write_handoff(arc, date="2026-06-25", mode="continue", where="mid factory build")
    threads = go.resumable_threads(tmp_control_home)
    assert len(threads) == 1
    t = threads[0]
    assert t.kind == go.KIND_CONTINUE
    assert t.handoff is not None
    assert t.summary == "mid factory build"


def test_close_handoff_is_hidden(tmp_control_home):
    arc = _make_arc(tmp_control_home, "01-thread")
    _write_handoff(arc, date="2026-06-25", mode="close")
    assert go.resumable_threads(tmp_control_home) == []


def test_no_handoff_is_raw_from_passport(tmp_control_home):
    _make_arc(tmp_control_home, "01-thread", goal="raise me raw")
    threads = go.resumable_threads(tmp_control_home)
    assert len(threads) == 1
    t = threads[0]
    assert t.kind == go.KIND_RAW
    assert t.handoff is None
    assert t.summary == "raise me raw"


def test_continue_overrides_when_latest_even_if_an_earlier_close(tmp_control_home):
    # latest handoff wins: an old close then a fresh continue → resumable continue
    arc = _make_arc(tmp_control_home, "01-thread")
    _write_handoff(arc, date="2026-06-20", mode="close")
    _write_handoff(arc, date="2026-06-25", mode="continue", where="back on it")
    threads = go.resumable_threads(tmp_control_home)
    assert [t.kind for t in threads] == [go.KIND_CONTINUE]


# --- menu rendering ---------------------------------------------------------

def test_render_resume_menu_lists_threads(tmp_control_home):
    arc = _make_arc(tmp_control_home, "01-thread")
    _write_handoff(arc, date="2026-06-25", mode="continue", where="where line")
    menu = go.render_resume_menu(go.resumable_threads(tmp_control_home))
    assert "Resume" in menu
    assert "01-thread" in menu
    assert "[continue]" in menu
    assert "where line" in menu


def test_render_resume_menu_empty_steers_to_new(tmp_control_home):
    assert "tide go --mode new" in go.render_resume_menu([])


def test_render_new_menu_has_just_chat_at_zero(tmp_control_home):
    _make_arc(tmp_control_home, "01-thread", goal="g1")
    menu = go.render_new_menu(go.new_options(tmp_control_home), tmp_control_home)
    assert "0) just chat" in menu
    assert "1) 01-thread" in menu
    assert "g1" in menu


# --- selection parsing ------------------------------------------------------

def test_parse_pick_valid_and_range():
    assert go.parse_pick("2", 3) == 2
    assert go.parse_pick("0", 3, allow_zero=True) == 0


def test_parse_pick_rejects_zero_without_allow():
    import pytest

    with pytest.raises(go.GoError):
        go.parse_pick("0", 3)


def test_parse_pick_rejects_out_of_range_and_garbage():
    import pytest

    with pytest.raises(go.GoError):
        go.parse_pick("9", 3)
    with pytest.raises(go.GoError):
        go.parse_pick("x", 3)


# --- seed resolution --------------------------------------------------------

def test_build_resume_seed_wraps_distil_with_head_pointer():
    out = go.build_resume_seed("my-arc", "## Where we are\nhalfway there")
    assert "resume thread: my-arc" in out
    assert "MIGRATE.md" in out  # head-role pointer
    assert "halfway there" in out


def test_seed_for_thread_dry_run_returns_placeholder(tmp_control_home):
    arc = _make_arc(tmp_control_home, "01-thread")
    _write_handoff(arc, date="2026-06-25", mode="continue", where="x")
    t = go.resumable_threads(tmp_control_home)[0]
    assert go.seed_for_thread(tmp_control_home, t, dry_run=True) == "<seed-file>"


# --- CLI dry-run wiring -----------------------------------------------------

def test_cli_go_dry_run_overview_prints_both_menus(tmp_control_home, monkeypatch, capsys):
    from tide import cli

    arc = _make_arc(tmp_control_home, "01-thread", goal="factory")
    _write_handoff(arc, date="2026-06-25", mode="continue", where="mid build")
    _make_arc(tmp_control_home, "02-other", goal="other work")
    monkeypatch.chdir(tmp_control_home)

    rc = cli.main(["go", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Resume" in out and "New" in out
    assert "[continue]" in out  # the resumable thread is classified
    assert "just chat" in out


def test_cli_go_resume_pick_dry_run_delegates_to_terminal(tmp_control_home, monkeypatch, capsys):
    from tide import cli

    arc = _make_arc(tmp_control_home, "01-thread", goal="factory")
    _write_handoff(arc, date="2026-06-25", mode="continue", where="mid build")
    (tmp_control_home / "MIGRATE.md").write_text("# migrate", encoding="utf-8")
    monkeypatch.chdir(tmp_control_home)

    rc = cli.main(["go", "--mode", "resume", "--pick", "1", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    # delegated to `tide terminal` — its scoped argv shows, nothing exec'd
    assert "would resume [continue] 01-thread" in out
    assert "--strict-mcp-config" in out
    assert "--dangerously-skip-permissions" in out
    # auth-preserving: the built command line itself never carries --bare
    cmd_line = next(ln for ln in out.splitlines() if "command:" in ln)
    assert "--bare" not in cmd_line


def test_cli_go_new_just_chat_dry_run_uses_migrate_head(tmp_control_home, monkeypatch, capsys):
    from tide import cli

    _make_arc(tmp_control_home, "01-thread")
    (tmp_control_home / "MIGRATE.md").write_text("# migrate", encoding="utf-8")
    monkeypatch.chdir(tmp_control_home)

    rc = cli.main(["go", "--mode", "new", "--pick", "0", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "just chat" in out
    assert "MIGRATE.md" in out  # the plain head seed terminal resolves


# --- in-flight gate ---------------------------------------------------------

def _write_contract(arc: Path, *, state: str) -> Path:
    """Drop a minimal ``contract.md`` in *arc* with the given ``state:`` field."""
    path = arc / "contract.md"
    path.write_text(
        "# contract — x\n\nslug: x\nstate: {0}\nsign: orchestrator @ 2026-06-25\n".format(state),
        encoding="utf-8",
    )
    return path


def _write_unmerged_delta(root: Path, name: str) -> Path:
    """Create a CLOSED arc carrying a non-empty, unmerged ``delta.md`` (an offender)."""
    arc = root / ".tide" / "arcs" / name  # name should be wrapped __…__
    arc.mkdir(parents=True, exist_ok=True)
    (arc / "delta.md").write_text(
        "# delta — x\nmerged: no\n\nreal body to merge\n", encoding="utf-8"
    )
    return arc


def test_inflight_clean_on_bare_control_home(tmp_control_home):
    assert go.inflight_signals(tmp_control_home).clean


def test_inflight_detects_unmerged_delta(tmp_control_home):
    _write_unmerged_delta(tmp_control_home, "__99-old__")
    s = go.inflight_signals(tmp_control_home)
    assert not s.clean
    assert "__99-old__" in s.unmerged


def test_inflight_detects_running_contract(tmp_control_home):
    arc = _make_arc(tmp_control_home, "01-thread")
    _write_contract(arc, state="running")
    s = go.inflight_signals(tmp_control_home)
    assert not s.clean
    assert ("01-thread", "running") in s.contracts


def test_render_inflight_clean_and_dirty():
    clean = go.InFlight([], [], [])
    assert "clean" in go.render_inflight(clean)
    dirty = go.InFlight(["__99-x__"], [("01-y", "output")], ["02-z"])
    text = go.render_inflight(dirty)
    assert "unmerged deltas: __99-x__" in text
    assert "01-y [output]" in text
    assert "drift: 02-z" in text


def test_wait_until_settled_returns_true_when_it_clears():
    calls = {"n": 0}

    def signal_fn(_root):
        calls["n"] += 1
        # dirty on the first poll, clean thereafter
        return go.InFlight([], [], []) if calls["n"] >= 2 else go.InFlight(["x"], [], [])

    sleeps = []
    ok = go.wait_until_settled(
        Path("/tmp"), sleep_fn=sleeps.append, signal_fn=signal_fn, interval=1.0, max_wait=10.0
    )
    assert ok is True
    assert sleeps == [1.0]  # slept once, then it was clean


def test_wait_until_settled_times_out_when_never_clears():
    sleeps = []
    ok = go.wait_until_settled(
        Path("/tmp"),
        sleep_fn=sleeps.append,
        signal_fn=lambda _r: go.InFlight(["x"], [], []),
        interval=2.0,
        max_wait=4.0,
    )
    assert ok is False
    assert sleeps == [2.0, 2.0]  # bounded — never an unbounded block


def test_cli_go_dry_run_overview_shows_inflight_check(tmp_control_home, monkeypatch, capsys):
    from tide import cli

    _make_arc(tmp_control_home, "01-thread")
    _write_unmerged_delta(tmp_control_home, "__99-old__")
    monkeypatch.chdir(tmp_control_home)

    rc = cli.main(["go", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "in-flight check" in out
    assert "__99-old__" in out  # the offender is surfaced in the overview


def test_cli_go_resume_dry_run_prints_inflight_then_launches(tmp_control_home, monkeypatch, capsys):
    from tide import cli

    arc = _make_arc(tmp_control_home, "01-thread", goal="factory")
    _write_handoff(arc, date="2026-06-25", mode="continue", where="mid build")
    (tmp_control_home / "MIGRATE.md").write_text("# migrate", encoding="utf-8")
    monkeypatch.chdir(tmp_control_home)

    rc = cli.main(["go", "--mode", "resume", "--pick", "1", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "in-flight check: clean" in out  # gate shown under dry-run
    assert "command:" in out               # still proceeds to the terminal dry-run


def test_cli_go_resume_dirty_cancel_does_not_launch(tmp_control_home, monkeypatch, capsys):
    from tide import cli

    arc = _make_arc(tmp_control_home, "01-thread", goal="factory")
    _write_handoff(arc, date="2026-06-25", mode="continue", where="mid build")
    _write_unmerged_delta(tmp_control_home, "__99-old__")
    (tmp_control_home / "MIGRATE.md").write_text("# migrate", encoding="utf-8")
    monkeypatch.chdir(tmp_control_home)
    monkeypatch.setattr("builtins.input", lambda *_a: "c")  # cancel at the gate

    rc = cli.main(["go", "--mode", "resume", "--pick", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cancelled" in out
    assert "command:" not in out  # the launch was NOT reached


def test_cli_go_resume_dirty_go_anyway_reaches_launch(tmp_control_home, monkeypatch, capsys):
    from tide import cli

    arc = _make_arc(tmp_control_home, "01-thread", goal="factory")
    _write_handoff(arc, date="2026-06-25", mode="continue", where="mid build")
    _write_unmerged_delta(tmp_control_home, "__99-old__")
    (tmp_control_home / "MIGRATE.md").write_text("# migrate", encoding="utf-8")
    monkeypatch.chdir(tmp_control_home)
    # 'g' enters anyway; force terminal into dry-run so no real session execs
    monkeypatch.setattr("builtins.input", lambda *_a: "g")
    monkeypatch.setattr(go.terminal, "cmd_terminal", lambda ns: (print("LAUNCHED") or 0))

    rc = cli.main(["go", "--mode", "resume", "--pick", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "LAUNCHED" in out  # gate let it through to the launch


# --- role-by-place ----------------------------------------------------------

def test_resolve_role_control_home_is_orchestrator(tmp_control_home):
    d = go.resolve_role(tmp_control_home)
    assert d.role == go.ROLE_ORCHESTRATOR
    assert d.env_role == "orchestrator"
    assert d.root == tmp_control_home
    assert d.reason == "control-home detected"
    assert d.is_orchestrator


def test_resolve_role_project_is_project_manager(tmp_project):
    d = go.resolve_role(tmp_project)
    assert d.role == go.ROLE_PROJECT_MANAGER
    assert d.env_role == "worker"
    assert d.root == tmp_project
    assert d.reason == "project {0}".format(tmp_project.name)
    assert not d.is_orchestrator


def test_resolve_role_non_tide_dir_errors(tmp_path):
    import pytest

    with pytest.raises(go.GoError):
        go.resolve_role(tmp_path / "nowhere")


def test_resolve_role_force_orchestrator_from_project(tmp_project):
    # --orchestrator forces the head even from a plain project dir
    d = go.resolve_role(tmp_project, force_orchestrator=True)
    assert d.role == go.ROLE_ORCHESTRATOR
    assert d.env_role == "orchestrator"
    assert d.reason == "--orchestrator forced"


def test_render_role_line():
    d = go.RoleDecision("project-manager", "worker", Path("/x/demo"), "project demo")
    assert go.render_role(d) == "role: project-manager (project demo)"


def test_project_manager_resume_seed_points_at_project_orientation():
    out = go.build_resume_seed("a", "## Where we are\nx", is_orchestrator=False)
    assert "project's manager" in out
    assert "tide context show" in out
    assert "MIGRATE.md" not in out  # not the head pointer


def test_project_orientation_seed_carries_the_triad(tmp_project):
    text = go.project_orientation_seed(tmp_project)
    assert "WORKER session scoped to THIS project" in text
    assert "read first" in text  # the context.render_enter triad
    assert "open arcs" in text


# --- role-by-place CLI dry-run ---------------------------------------------

def test_cli_go_project_dir_dry_run_shows_project_manager(tmp_project, monkeypatch, capsys):
    from tide import cli

    _make_arc(tmp_project, "01-thread", goal="proj work")
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["go", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "role: project-manager (project {0})".format(tmp_project.name) in out
    assert "plain project session" in out  # role-aware just-chat label


def test_cli_go_orchestrator_flag_forces_head_from_project(tmp_project, monkeypatch, capsys):
    from tide import cli

    monkeypatch.chdir(tmp_project)
    rc = cli.main(["go", "-O", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "role: orchestrator (--orchestrator forced)" in out


def test_cli_go_non_tide_dir_errors(tmp_path, monkeypatch, capsys):
    from tide import cli

    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    rc = cli.main(["go", "--dry-run"])
    assert rc == 1  # GoError → tide: … nonzero
    err = capsys.readouterr().err
    assert "not inside a tide project" in err


def test_cli_go_control_home_launch_carries_orchestrator_env(tmp_control_home, monkeypatch, capsys):
    from tide import cli

    _make_arc(tmp_control_home, "01-thread")
    (tmp_control_home / "MIGRATE.md").write_text("# migrate", encoding="utf-8")
    monkeypatch.chdir(tmp_control_home)
    rc = cli.main(["go", "--mode", "new", "--pick", "0", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TIDE_ROLE: orchestrator" in out


def test_cli_go_project_launch_carries_worker_env(tmp_project, monkeypatch, capsys):
    from tide import cli

    _make_arc(tmp_project, "01-thread", goal="proj work")
    monkeypatch.chdir(tmp_project)
    # new → just-chat (project orientation seed), dry-run shows TIDE_ROLE worker
    rc = cli.main(["go", "--mode", "new", "--pick", "0", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TIDE_ROLE: worker" in out
    assert "plain project session" in out


# --- front-door presentation ------------------------------------------------

def test_render_header_is_a_titled_banner():
    d = go.RoleDecision("orchestrator", "orchestrator", Path("/x"), "control-home detected")
    header = go.render_header(d)
    assert "tide · go" in header               # the titled banner
    assert "─" in header                       # the hairline rule
    assert "role: orchestrator (control-home detected)" in header


def test_resume_menu_columns_are_aligned():
    # two threads of different name length → the kind tag column lines up
    t1 = go.Thread(Path("/a"), "01-short", "short", go.KIND_CONTINUE, Path("/h"), "go on")
    t2 = go.Thread(Path("/b"), "02-a-much-longer-name", "x", go.KIND_RAW, None, "raw one")
    menu = go.render_resume_menu([t1, t2])
    col1 = menu.splitlines()[1].index("[continue]")
    col2 = menu.splitlines()[2].index("[raw]")
    assert col1 == col2  # aligned regardless of name length


def test_cli_go_prints_front_door_banner(tmp_control_home, monkeypatch, capsys):
    from tide import cli

    _make_arc(tmp_control_home, "01-thread")
    monkeypatch.chdir(tmp_control_home)
    rc = cli.main(["go", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "tide · go" in out                  # the human sees the door banner
    assert "in-flight check: clean" in out     # calm clean line
