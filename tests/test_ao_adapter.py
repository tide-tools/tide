"""The ao adapter against a FAKE desktop-AO CLI (no app, no daemon, no claude).

The fake binary is a tiny python script that keeps projects/sessions in a JSON state
file and logs every call — enough of `ao` 0.13's surface (project ls/get/add/
set-config, spawn, session get/restore, send) to drive the adapter end to end.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tide import handoff_queue, paths, registry
from tide.adapters import ao as ao_mod
from tide.adapters.ao import AoAdapter, AoTranslateError, translate

FAKE = r'''#!{py}
import json, os, sys
st_path = os.environ["FAKE_AO_STATE"]
st = json.load(open(st_path)) if os.path.exists(st_path) else {{"projects": {{}}, "sessions": {{}}, "calls": []}}
a = sys.argv[1:]
st["calls"].append(a)
def save():
    json.dump(st, open(st_path, "w"))
def opt(name):
    return a[a.index(name) + 1] if name in a else None
def fail(msg):
    save(); print(msg, file=sys.stderr); sys.exit(1)
if a[:2] == ["project", "ls"]:
    print(json.dumps({{"projects": [{{"id": k, "name": v.get("name", k)}} for k, v in st["projects"].items()]}}))
elif a[:2] == ["project", "get"]:
    p = st["projects"].get(a[2])
    if p is None: fail("Unknown project (PROJECT_NOT_FOUND)")
    print(json.dumps({{"status": "ok", "project": dict(p, id=a[2])}}))
elif a[:2] == ["project", "add"]:
    pid = opt("--id")
    if pid in st["projects"]: fail("project id taken")
    st["projects"][pid] = {{"path": opt("--path"), "name": opt("--name"), "config": {{"containerReap": {{}}}}}}
    print("registered project " + pid)
elif a[:2] == ["project", "set-config"]:
    st["projects"][a[2]]["config"] = json.loads(opt("--config-json"))
elif a[0] == "spawn":
    pid = opt("--project")
    if pid not in st["projects"]: fail("unknown project")
    n = sum(1 for s in st["sessions"].values() if s["project"] == pid) + 1
    sid = "{{0}}-{{1}}".format(pid, n)
    st["sessions"][sid] = {{"project": pid, "name": opt("--name"), "prompt": opt("--prompt"),
                           "terminated": False, "env": st["projects"][pid]["config"].get("env", {{}})}}
    print('spawned session {{0}} "{{1}}" (idle) [prompt 1 B, system 1 B]'.format(sid, opt("--name")))
elif a[:2] == ["session", "get"]:
    s = st["sessions"].get(a[2])
    if s is None: fail("Unknown session (SESSION_NOT_FOUND)")
    print(json.dumps({{"session": {{"id": a[2], "isTerminated": s["terminated"],
                                  "status": "terminated" if s["terminated"] else "idle",
                                  "activity": {{"state": s.get("state", "idle")}}}}}}))
elif a[:2] == ["session", "restore"]:
    st["sessions"][a[2]]["terminated"] = False
    st["sessions"][a[2]]["state"] = "idle"
    print("session restored")
elif a[:2] == ["session", "resume-agent"]:
    s = st["sessions"][a[2]]
    if s["terminated"]: fail("session terminated")
    s["state"] = "idle"
    print("agent resumed")
elif a[0] == "send":
    s = st["sessions"].get(opt("--session"))
    if s is None or s["terminated"]: fail("session not running")
    s.setdefault("said", []).append(opt("--message"))
else:
    fail("fake ao: unknown " + " ".join(a))
save()
'''


@pytest.fixture
def fake_ao(tmp_path, monkeypatch):
    """Install the fake `ao`; return a reader for its state."""
    binp = tmp_path / "fake-ao"
    binp.write_text(FAKE.format(py=sys.executable), encoding="utf-8")
    binp.chmod(binp.stat().st_mode | stat.S_IXUSR)
    state = tmp_path / "ao-state.json"
    monkeypatch.setenv("TIDE_AO_BIN", str(binp))
    monkeypatch.setenv("FAKE_AO_STATE", str(state))
    monkeypatch.setattr(ao_mod, "bring_app_up", lambda: None)
    monkeypatch.setattr(ao_mod, "_control_home", lambda: "/ctl/home")

    def read():
        return json.loads(state.read_text()) if state.exists() else {
            "projects": {}, "sessions": {}, "calls": []}
    return read


def _git_repo(path: Path, *, claude_dir=True, ignore_claude=True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True,  # noqa: E731
                                    capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (path / ".tide" / "arcs").mkdir(parents=True)
    (path / ".tide" / "arcs" / "a.md").write_text("x\n")
    if ignore_claude:
        (path / ".gitignore").write_text("/.claude/\n")
    if claude_dir:
        (path / ".claude").mkdir()
        (path / ".claude" / "settings.json").write_text("{}\n")
    run("add", "-A")
    run("commit", "-qm", "init")
    return path


SEED = "/seeds/seed.md"
SID = "11111111-2222-3333-4444-555555555555"


def _fresh(role="orchestrator", extra=()):
    return ["env", "TIDE_ROLE={0}".format(role), "claude",
            "--dangerously-skip-permissions", "--session-id", SID,
            "--strict-mcp-config", "--mcp-config", "/m.json", *extra,
            "--append-system-prompt", "@" + SEED, "go now"]


# --- translate -----------------------------------------------------------------

def test_translate_fresh_carries_seed_sid_role_and_notes_mcp():
    spec = translate(_fresh(), "/p", main_tree="/main")
    assert spec.seed_file == SEED and spec.session_id == SID
    assert spec.role == "orchestrator" and spec.skip_permissions
    assert any("MCP" in n for n in spec.notes)
    assert SEED in spec.prompt and "handoff-confirm" in spec.prompt
    # F35: пре-пик tide в промпт не вписываем — сессия под AO живёт под другим id
    assert SID not in spec.prompt
    assert "tide hook handoff-confirm </dev/null" in spec.prompt
    assert "/main" in spec.prompt and spec.prompt.rstrip().endswith("go now")


def test_launch_turn_is_calm_and_switches_off_the_ao_template():
    """SOC-181 п.10: no «прочитай целиком… главнее AO», no «НЕ отчитывайся» — the seed
    is the start, begin with its first step; the AO-template parts that do not apply
    here are named once."""
    spec = translate(_fresh(), "/p", main_tree="/main")
    text = spec.prompt
    assert "Твой старт — сид {0} (роль: orchestrator):".format(SEED) in text
    assert "начни с первого шага" in text
    for part in ("implementation worker", "сабагентов", "~/.ao/data/artifacts", "ao report"):
        assert part in text
    for noise in ("целиком", "системный промпт", "главнее", "НЕ "):
        assert noise not in text
    assert len(text.replace("go now", "").encode("utf-8")) < 1400


def test_translate_model_passes_through():
    spec = translate(_fresh(extra=("--model", "sonnet")), "/p")
    assert spec.model == "sonnet"
    argv = AoAdapter._spawn_argv(spec, "proj", "t")
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_translate_resume_shape_becomes_a_continuation():
    import shlex
    fresh = _fresh()[2:]  # drop env prefix
    resume = ["claude", "--dangerously-skip-permissions", "--resume", SID]
    cmd = ["env", "TIDE_ROLE=orchestrator", "sh", "-c",
           "{0} || {1}".format(shlex.join(resume), shlex.join(fresh))]
    spec = translate(cmd, "/p")
    assert spec.resumed_from == SID and spec.seed_file == SEED
    assert "продолжение сессии" in spec.prompt
    assert "handoff-confirm" not in spec.prompt  # a return is not a pickup


@pytest.mark.parametrize("cmd,needle", [
    (["claude", "--append-system-prompt", "inline text"], "@<file>"),
    (["claude", "--weird", "--append-system-prompt", "@/s"], "--weird"),
    (["bash", "-lc", "x"], "only launches claude"),
    (["env", "FOO=1", "claude", "--append-system-prompt", "@/s"], "FOO"),
    (["claude", "go"], "no @<seed>"),
])
def test_translate_refuses_what_ao_cannot_carry(cmd, needle):
    with pytest.raises(AoTranslateError, match=None) as exc:
        translate(cmd, "/p")
    assert needle in str(exc.value)


def test_translate_limit_is_utf8_bytes_not_chars():
    cyr = "ж" * 9000  # 9000 chars, 18000 bytes UTF-8 — over AO's 16 KiB cap
    cmd = _fresh()[:-1] + [cyr]
    with pytest.raises(AoTranslateError) as exc:
        translate(cmd, "/p")
    assert "bytes" in str(exc.value) and "16384" in str(exc.value)
    ok = translate(_fresh()[:-1] + ["ж" * 7000], "/p")  # 14000 bytes + preamble fits
    assert len(ok.prompt.encode("utf-8")) <= 16384


# --- spawn + registration --------------------------------------------------------

def test_spawn_registers_project_merges_config_and_parses_id(tmp_path, fake_ao):
    repo = _git_repo(tmp_path / "My Repo")
    res = AoAdapter().spawn(command=_fresh(), cwd=str(repo), title="handoff · x")
    assert res.ok, res.detail
    assert res.ref == "my-repo-1"
    st = fake_ao()
    proj = st["projects"]["my-repo"]
    assert os.path.realpath(proj["path"]) == os.path.realpath(str(repo))
    cfg = proj["config"]
    assert cfg["env"]["TIDE_HOME"] == "/ctl/home"
    assert cfg["env"]["TIDE_ROLE"] == "orchestrator"
    assert os.path.realpath(cfg["env"]["TIDE_PROJECT_ROOT"]) == os.path.realpath(str(repo))
    assert cfg["symlinks"] == [".claude"]  # untracked .claude rides in; tracked .tide not
    assert cfg["agentConfig"]["permissions"] == "bypass-permissions"
    assert cfg["defaultBranch"] == "main"  # no remote → AO can't infer it
    assert cfg["containerReap"] == {}  # AO's own keys survive the merge
    spawn = [c for c in st["calls"] if c[0] == "spawn"][0]
    assert spawn[spawn.index("--harness") + 1] == "claude-code"
    assert spawn[spawn.index("--name") + 1] == "handoff · x"
    prompt = spawn[spawn.index("--prompt") + 1]
    assert SEED in prompt and str(repo.resolve()) in prompt
    assert "registered" in res.detail and "MCP" in res.detail


def test_spawn_is_idempotent_on_registration(tmp_path, fake_ao):
    repo = _git_repo(tmp_path / "proj")
    assert AoAdapter().spawn(command=_fresh(), cwd=str(repo)).ok
    assert AoAdapter().spawn(command=_fresh(), cwd=str(repo)).ok
    calls = fake_ao()["calls"]
    assert sum(1 for c in calls if c[:2] == ["project", "add"]) == 1
    assert sum(1 for c in calls if c[:2] == ["project", "set-config"]) == 1
    assert sorted(fake_ao()["sessions"]) == ["proj-1", "proj-2"]


def test_spawn_keeps_foreign_config_and_picks_free_id(tmp_path, fake_ao):
    repo = _git_repo(tmp_path / "proj")
    other = tmp_path / "elsewhere"
    other.mkdir()
    state = {"projects": {"proj": {"path": str(other), "config": {}}},
             "sessions": {}, "calls": []}
    Path(os.environ["FAKE_AO_STATE"]).write_text(json.dumps(state))
    assert AoAdapter().spawn(command=_fresh(), cwd=str(repo)).ok
    st = fake_ao()
    assert "proj-2" in st["projects"] and st["projects"]["proj"]["config"] == {}
    # now a pre-existing config of the human's own must survive a merge
    st["projects"]["proj-2"]["config"]["env"]["MINE"] = "1"
    st["projects"]["proj-2"]["config"]["symlinks"].append("node_modules")
    Path(os.environ["FAKE_AO_STATE"]).write_text(json.dumps(st))
    assert AoAdapter().spawn(command=_fresh(role="worker"), cwd=str(repo)).ok
    cfg = fake_ao()["projects"]["proj-2"]["config"]
    assert cfg["env"]["MINE"] == "1" and cfg["env"]["TIDE_ROLE"] == "worker"
    assert cfg["symlinks"] == [".claude", "node_modules"]


def test_spawn_untracked_tide_is_symlinked(tmp_path, fake_ao):
    repo = tmp_path / "p"
    _git_repo(repo)
    subprocess.run(["git", "-C", str(repo), "rm", "-rq", "--cached", ".tide"], check=True)
    (repo / ".gitignore").write_text("/.claude/\n/.tide/\n")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "untrack"], check=True)
    assert AoAdapter().spawn(command=_fresh(), cwd=str(repo)).ok
    assert fake_ao()["projects"]["p"]["config"]["symlinks"] == [".claude", ".tide"]


def test_spawn_non_git_is_a_clear_error(tmp_path, fake_ao):
    res = AoAdapter().spawn(command=_fresh(), cwd=str(tmp_path))
    assert not res.ok and "not a git repository" in res.detail
    assert fake_ao()["calls"] == []


def test_spawn_without_the_app_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("TIDE_AO_BIN", str(tmp_path / "nope"))
    res = AoAdapter().spawn(command=_fresh(), cwd=str(tmp_path))
    assert not res.ok and "not found" in res.detail


def test_dry_run_builds_without_calling(tmp_path, fake_ao):
    res = AoAdapter().spawn(command=_fresh(), cwd=str(tmp_path), dry_run=True)
    assert res.ok and res.commands and res.commands[0][1] == "spawn"
    assert fake_ao()["calls"] == []


# --- focus / say / alive ----------------------------------------------------------

def test_focus_live_restores_terminated_unknown_false(tmp_path, fake_ao):
    repo = _git_repo(tmp_path / "proj")
    ref = AoAdapter().spawn(command=_fresh(), cwd=str(repo)).ref
    a = AoAdapter()
    assert a.alive(ref) and a.focus(ref)
    st = fake_ao()
    st["sessions"][ref]["terminated"] = True
    Path(os.environ["FAKE_AO_STATE"]).write_text(json.dumps(st))
    assert not a.alive(ref)
    assert a.focus(ref)  # revived by `ao session restore`
    assert ["session", "restore", ref] in fake_ao()["calls"]
    assert a.alive(ref)
    assert not a.focus("term_orca-handle") and not a.alive("nope-9")


def test_exited_agent_is_not_alive_and_focus_resumes_it(tmp_path, fake_ao):
    repo = _git_repo(tmp_path / "proj")
    ref = AoAdapter().spawn(command=_fresh(), cwd=str(repo)).ref
    a = AoAdapter()
    st = fake_ao()
    st["sessions"][ref]["state"] = "exited"  # after `ao session exit-agent`
    Path(os.environ["FAKE_AO_STATE"]).write_text(json.dumps(st))
    assert not a.alive(ref)
    assert a.focusable(ref)
    assert a.focus(ref)  # revived by `ao session resume-agent`
    calls = fake_ao()["calls"]
    assert ["session", "resume-agent", ref] in calls
    assert ["session", "restore", ref] not in calls
    assert a.alive(ref)


def test_say_sends_to_the_session(tmp_path, fake_ao):
    repo = _git_repo(tmp_path / "proj")
    ref = AoAdapter().spawn(command=_fresh(), cwd=str(repo)).ref
    assert AoAdapter().say(ref, "план согласован")
    assert fake_ao()["sessions"][ref]["said"] == ["план согласован"]
    assert not AoAdapter().say("ghost-1", "x")


# --- the tide side: root mapping, confirm, self-register --------------------------

def test_find_tide_root_maps_an_ao_worktree_to_the_main_tree(tmp_path, monkeypatch):
    main = _git_repo(tmp_path / "main")
    data = tmp_path / "aodata"
    wt = data / "worktrees" / "main" / "main-1"
    wt.parent.mkdir(parents=True)
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "-b", "ao/x", str(wt)],
                   check=True)
    assert (wt / ".tide").is_dir()  # the committed, stale copy
    monkeypatch.setenv("AO_DATA_DIR", str(data))
    assert paths.find_tide_root(wt) == main.resolve()
    (wt / "sub").mkdir()
    assert paths.find_tide_root(wt / "sub") == main.resolve()
    # outside AO's worktrees nothing changes
    assert paths.find_tide_root(main) == main.resolve()


def test_handoff_confirm_maps_ao_session_to_tide_sid(tmp_path, monkeypatch):
    registry.record(tmp_path, SID, "proj-3", "/arc")
    monkeypatch.setenv("AO_SESSION_ID", "proj-3")
    got = handoff_queue._session_candidates(tmp_path, "ao-minted-claude-sid")
    assert got == ["ao-minted-claude-sid", SID]
    monkeypatch.delenv("AO_SESSION_ID")
    assert handoff_queue._session_candidates(tmp_path, "x") == ["x"]


def test_self_pair_uses_ao_session_as_handle():
    assert registry.self_pair({"CLAUDE_CODE_SESSION_ID": "s", "AO_SESSION_ID": "p-1"}) == ("s", "p-1")
    assert registry.self_pair({"CLAUDE_CODE_SESSION_ID": "s", "AO_SESSION_ID": "p-1",
                               "ORCA_TERMINAL_HANDLE": "term_x"}) == ("s", "term_x")


# --- return (board ⟳) through ao -------------------------------------------------

def test_return_without_ao_ref_spawns_a_continuation(tmp_path, fake_ao):
    from tide.launcher import return_cmd
    home = tmp_path / "home"
    home.mkdir()
    repo = _git_repo(tmp_path / "proj")
    registry.record(home, SID, "term_dead-orca-tab", "/arc/path")
    out = return_cmd.run_return(home, sid=SID, project=repo, arc="/arc/path",
                                adapter_name="ao")
    assert out["ok"] and out["action"] == "resumed", out
    assert out["handle"] == "proj-1"
    assert registry.recorded_handle(home, SID) == "proj-1"
    prompt = fake_ao()["sessions"]["proj-1"]["prompt"]
    assert "продолжение сессии {0}".format(SID) in prompt and "/arc/path" in prompt


def test_return_with_ao_ref_restores_and_says(tmp_path, fake_ao):
    from tide.launcher import return_cmd
    home = tmp_path / "home"
    home.mkdir()
    repo = _git_repo(tmp_path / "proj")
    ref = AoAdapter().spawn(command=_fresh(), cwd=str(repo)).ref
    registry.record(home, SID, ref, "/arc")
    st = fake_ao()
    st["sessions"][ref]["terminated"] = True
    Path(os.environ["FAKE_AO_STATE"]).write_text(json.dumps(st))
    dry = return_cmd.run_return(home, sid=SID, project=repo, adapter_name="ao", dry_run=True)
    assert dry["action"] == "focused"  # read-only probe: terminated is restorable
    assert ["session", "restore", ref] not in fake_ao()["calls"]  # dry-run touched nothing
    out = return_cmd.run_return(home, sid=SID, project=repo, adapter_name="ao", say="ход")
    assert out["action"] == "focused" and out["said"] is True
    assert fake_ao()["sessions"][ref]["said"] == ["ход"]
    assert len(fake_ao()["sessions"]) == 1  # no duplicate session
