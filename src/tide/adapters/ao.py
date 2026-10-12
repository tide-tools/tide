"""tide.adapters.ao — open sessions in the desktop Agent Orchestrator (AO ≥ 0.13).

The NEW AO is a desktop app with a Go daemon (``127.0.0.1:3001``, data ``~/.ao``).
Its CLI ships INSIDE the app bundle; the ``ao`` found on PATH is usually the OLD
Node AO (0.9.x) and must not be used — so the binary is always called by absolute
path (:data:`DEFAULT_AO_BIN`, overridable with ``$TIDE_AO_BIN``).

AO does not run a command we hand it: it builds the ``claude`` invocation itself
(chat mode, its own worker system prompt, user/project/local settings) in a FRESH
git worktree ``~/.ao/data/worktrees/<project>/<session>``. So this adapter
TRANSLATES the launcher's argv instead of carrying it verbatim:

* ``--append-system-prompt @<seed>`` — rides the first turn as "read this file
  fully, it is your system prompt" (the file stays where the launcher put it).
* ``--session-id <sid>`` — AO cannot pin claude's id. The first turn asks the
  session to confirm the handoff under tide's sid (``tide hook handoff-confirm``
  with that id on stdin); the hook also maps ``$AO_SESSION_ID`` back to tide's sid
  through the registry, so the echo is a safety net, not the only path. tide's sid
  is only a pre-pick here: claude's real id is learned after start (SessionStart
  payload / ``$CLAUDE_CODE_SESSION_ID``) and swapped into every record by
  :func:`tide.sessions.ao_relink`.
* ``env TIDE_ROLE=<role>`` — AO has no per-session env, only per-project env
  (``ao project set-config --env``), resolved at spawn. The role is written there
  right before the spawn (and named in the first turn).
* ``--dangerously-skip-permissions`` → project ``permissions: bypass-permissions``.
* ``--model`` → ``ao spawn --model`` (per session).
* ``--strict-mcp-config`` / ``--mcp-config`` / ``--allowedTools`` — AO cannot pass
  them: the session loads the user's global MCP servers. Accepted (AO is the
  default terminal), with a one-line note in the spawn detail.
* ``sh -c "claude --resume <sid> … || <fresh>"`` (the return/⟳ shape) — AO cannot
  resume an outside claude conversation by id; the fresh half is launched with a
  "you are a continuation of session <sid>" note. A session that LIVED in AO is
  revived instead by :meth:`AoAdapter.focus` (``ao session restore`` /
  ``ao session resume-agent``).

The project is registered on the fly (``ao project add``) and its config merged —
never replaced (``set-config`` replaces the whole object, so we read, merge, write):
env ``TIDE_HOME`` (control-home) / ``TIDE_PROJECT_ROOT`` (main tree) / ``TIDE_ROLE``,
symlinks for UNTRACKED ``.claude`` / ``.tide`` (hooks + rules reach the worktree);
a TRACKED ``.claude`` can't be linked, so a ``postCreate`` step
(``tide install-hooks --worktree``) merges tide's hooks into the worktree's own copy
before the agent starts (merged on tide's spawn AND by ``tide ao ensure`` /
``tide install-hooks`` for projects whose sessions are started from the AO app);
``defaultBranch`` when the repo has no remote HEAD.

The worktree carries the COMMITTED copy of the repo — including a committed
``.tide/``. :func:`tide.paths.find_tide_root` maps any AO worktree back to its main
tree, so every ``tide`` call in the session reads and writes the real ``.tide``;
the first turn tells the agent to edit files in the main tree too.

Liveness has two layers in ``ao session get --json``: ``session.isTerminated`` (the
AO session) and ``session.activity.state`` ∈ active|idle|waiting_input|blocked|exited
(the agent process). The agent is alive ⇔ not terminated AND state != ``exited``.
``focus`` = live check, then revive: terminated → ``ao session restore``; session up
but agent exited → ``ao session resume-agent``; then bring the app up (``open -a``)
— AO has no "focus this session" verb. ``say`` = ``ao send --session --message``.

``ao spawn --prompt`` caps the first turn at 16 KiB in UTF-8 BYTES (over it AO
answers ``PROMPT_TOO_LONG`` and creates nothing), so :func:`translate` checks the
byte length up front — Cyrillic counts double.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .base import SESSION_PROGRAM, SpawnResult, TerminalAdapter

DEFAULT_AO_BIN = "/Applications/Agent Orchestrator.app/Contents/Resources/daemon/ao"
AO_BIN_ENV = "TIDE_AO_BIN"
AO_APP_NAME = "Agent Orchestrator"
HARNESS = "claude-code"
NAME_LIMIT = 100  # ao spawn --name max length
PROMPT_LIMIT_BYTES = 16 * 1024  # ao spawn --prompt cap, UTF-8 bytes (PROMPT_TOO_LONG)
BYPASS = "bypass-permissions"
PROJECT_ROOT_ENV = "TIDE_PROJECT_ROOT"

_MCP_FLAGS = {"--strict-mcp-config": 0, "--mcp-config": 1, "--allowedTools": 1}
_SPAWNED = re.compile(r"spawned session (\S+)")
_SKIP = "--dangerously-skip-permissions"


class AoTranslateError(ValueError):
    """The launcher's argv holds something AO cannot carry."""


class AoError(RuntimeError):
    """An ``ao`` CLI call failed (message is the human-readable reason)."""


# --- binary -------------------------------------------------------------------

def _agent_exited(session: dict) -> bool:
    """``session.activity.state == "exited"`` — the agent process is gone while the
    AO session may still be up (after ``exit-agent``)."""
    activity = session.get("activity") or {}
    return isinstance(activity, dict) and activity.get("state") == "exited"


def ao_bin() -> str:
    """The NEW AO CLI (absolute path; ``$TIDE_AO_BIN`` overrides)."""
    return (os.environ.get(AO_BIN_ENV) or "").strip() or DEFAULT_AO_BIN


def ao_available() -> bool:
    """True when the new AO CLI exists and is executable."""
    p = ao_bin()
    return os.path.isfile(p) and os.access(p, os.X_OK)


def _run(args: List[str], *, timeout: int = 60, cwd: Optional[str] = None
         ) -> subprocess.CompletedProcess:
    return subprocess.run([ao_bin(), *args], capture_output=True, text=True,
                          timeout=timeout, cwd=cwd)


def _call(args: List[str], *, timeout: int = 60) -> str:
    """Run ao; return stdout or raise :class:`AoError` with ao's own words."""
    try:
        proc = _run(args, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AoError("ao {0}: {1}".format(args[0], exc))
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise AoError("ao {0}: {1}".format(
            " ".join(args[:2]), msg[-1] if msg else "exit {0}".format(proc.returncode)))
    return proc.stdout or ""


def _json(args: List[str], *, timeout: int = 30) -> dict:
    out = _call(args, timeout=timeout)
    try:
        data = json.loads(out or "{}")
    except ValueError:
        raise AoError("ao {0}: not JSON".format(" ".join(args[:2])))
    return data if isinstance(data, dict) else {}


# --- translate ----------------------------------------------------------------

@dataclass
class AoLaunch:
    """What the launcher's argv means to AO."""

    prompt: str
    seed_file: str
    session_id: Optional[str] = None
    role: Optional[str] = None
    model: Optional[str] = None
    skip_permissions: bool = False
    resumed_from: Optional[str] = None
    user_prompt: str = ""
    notes: List[str] = field(default_factory=list)


def _split_env(command: List[str]) -> Tuple[Dict[str, str], List[str]]:
    """Peel a leading ``env A=B …`` off *command* → (env, rest)."""
    env: Dict[str, str] = {}
    rest = list(command)
    if rest and rest[0] == "env":
        rest = rest[1:]
        while rest and "=" in rest[0] and not rest[0].startswith("-"):
            k, v = rest.pop(0).split("=", 1)
            env[k] = v
    return env, rest


def _unwrap_resume(argv: List[str]) -> Tuple[List[str], Optional[str]]:
    """``sh -c "claude --resume X … || <fresh>"`` → (fresh argv, X); else (argv, None)."""
    if argv[:2] != ["sh", "-c"] or len(argv) < 3:
        return argv, None
    tokens = shlex.split(argv[2])
    if "||" not in tokens:
        raise AoTranslateError("cannot read the resume shape: {0!r}".format(argv[2]))
    cut = tokens.index("||")
    first, fresh = tokens[:cut], tokens[cut + 1:]
    sid = None
    if "--resume" in first:
        i = first.index("--resume")
        sid = first[i + 1] if i + 1 < len(first) else None
    return fresh, sid


# SOC-181 п.10: the launch turn is short and calm. It names the seed as the start
# (role, goal, next step live there), says which parts of the AO worker template
# do not apply here, and asks to begin with the seed's first step. The seed path
# keeps the «Твой старт — сид <path>» shape: :data:`tide.sessions._LAUNCH_SEED_RE`
# reads it back to learn which passport a chat owns.
AO_TEMPLATE_OFF = (
    "Из шаблона AO у нас не действуют: «implementation worker» (роль — из сида), "
    "запрет сабагентов (помощники через Agent — норма), отчёты в ~/.ao/data/artifacts "
    "(файлы — в workspace/ и output/ своей арки) и ao report как итог (итог — "
    "комментарий в задаче)."
)


def build_prompt(spec: AoLaunch, *, main_tree: Optional[str] = None) -> str:
    """The first user turn that stands in for system prompt + sid pin + cwd."""
    parts: List[str] = []
    if main_tree:
        parts.append(
            "Ты запущен tide через Agent Orchestrator. Проект — {0}: работай там "
            "(cd {0}, абсолютные пути). Каталог AO-worktree, где ты стартовал, — только "
            "точка запуска, в нём ничего не правь.".format(main_tree)
        )
    if spec.session_id and not spec.resumed_from:
        # SOC-101 F35: не вписывать сюда id. spec.session_id — пре-пик tide, AO
        # запускает claude под СВОИМ id, и сессия видела в промпте чужой id
        # (приём 260: 013be01e… при живой ec902aef). Хук сам берёт id этой
        # сессии из $CLAUDE_CODE_SESSION_ID, а пре-пик — из реестра по $AO_SESSION_ID.
        parts.append(
            "Приём: `tide hook handoff-confirm </dev/null` — id сессии tide возьмёт "
            "сам; если хук уже подтвердил, команда промолчит. Нить станет твоей с "
            "первым tide offload, сказанным своими словами."
        )
    if spec.resumed_from:
        parts.append(
            "Это продолжение сессии {0}: прежний чат в AO не поднять, контекст — "
            "по сиду и по курсору арки.".format(spec.resumed_from)
        )
    role = spec.role or ""
    parts.append(
        "Твой старт — сид {0}{1}: в нём роль, цель нити и следующий шаг. Прочитай его "
        "и начни с первого шага. {2}".format(
            spec.seed_file, " (роль: {0})".format(role) if role else "", AO_TEMPLATE_OFF)
    )
    if spec.user_prompt:
        parts.append(spec.user_prompt)
    return "\n\n".join(p.strip() for p in parts if p.strip())


def translate(command: List[str], cwd: str, *, main_tree: Optional[str] = None) -> AoLaunch:
    """Turn the launcher's ``claude …`` argv into an :class:`AoLaunch`.

    Pure. Raises :class:`AoTranslateError` naming what AO cannot carry — never
    drops a meaningful flag silently (dropped scoping is said in ``notes``).
    """
    del cwd  # kept for signature compatibility
    env, argv = _split_env(command)
    argv, resumed_from = _unwrap_resume(argv)
    inner_env, argv = _split_env(argv)
    env.update(inner_env)
    if not argv or argv[0] != SESSION_PROGRAM:
        raise AoTranslateError("ao only launches claude, got {0!r}".format(shlex.join(argv)))

    for k in env:
        if k != "TIDE_ROLE":
            raise AoTranslateError("ao sets no per-session env, cannot carry {0}".format(k))

    seed_file = None
    session_id = None
    model = None
    skip = False
    mcp: List[str] = []
    positional: List[str] = []
    i = 1
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if a == "--append-system-prompt" and nxt is not None:
            if not nxt.startswith("@"):
                raise AoTranslateError("ao takes no per-session system prompt; only an "
                                       "@<file> seed can be handed over")
            seed_file = nxt[1:]
            i += 2
        elif a == "--session-id" and nxt is not None:
            session_id = nxt
            i += 2
        elif a == "--model" and nxt is not None:
            model = nxt
            i += 2
        elif a == _SKIP:
            skip = True
            i += 1
        elif a in _MCP_FLAGS:
            mcp.append(a)
            i += 1 + _MCP_FLAGS[a]
        elif a.startswith("-"):
            raise AoTranslateError("ao cannot pass claude flag {0}".format(a))
        else:
            positional.append(a)
            i += 1

    if seed_file is None:
        raise AoTranslateError("no @<seed> in the launch command — nothing to hand ao")
    spec = AoLaunch(prompt="", seed_file=seed_file, session_id=session_id,
                    role=env.get("TIDE_ROLE"), model=model, skip_permissions=skip,
                    resumed_from=resumed_from, user_prompt=" ".join(positional))
    if mcp:
        spec.notes.append("MCP scoping not carried by AO ({0}) — global MCP servers "
                          "load".format(" ".join(mcp)))
    if resumed_from:
        spec.notes.append("claude --resume {0} impossible in AO — fresh session with a "
                          "continuation note".format(resumed_from[:8]))
    spec.prompt = build_prompt(spec, main_tree=main_tree)
    size = len(spec.prompt.encode("utf-8"))
    if size > PROMPT_LIMIT_BYTES:
        raise AoTranslateError(
            "first turn is {0} bytes UTF-8 ({1} chars), AO caps --prompt at {2} bytes".format(
                size, len(spec.prompt), PROMPT_LIMIT_BYTES))
    return spec


# --- project registration -----------------------------------------------------

def _git(cwd: str, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                              timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def main_tree(cwd: str) -> Path:
    """The main working tree of the git repo at *cwd* (raises AoError if none)."""
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if not top or not common:
        raise AoError("{0} is not a git repository — AO runs every session in a git "
                      "worktree; `git init` + a first commit, or pick another adapter "
                      "(tide terminal-adapter --set orca)".format(cwd))
    if _git(cwd, "rev-parse", "--verify", "-q", "HEAD") is None:
        raise AoError("{0} has no commits yet — AO needs one to cut a worktree".format(top))
    c = Path(common)
    return c.parent if c.name == ".git" else Path(top)


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def slug_for(path: Path) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", path.name.lower()).strip("-")
    return s or "project"


def find_project(path: Path) -> Optional[dict]:
    """The registered AO project whose path is *path* (``project get`` shape) or None."""
    want = _real(str(path))
    for p in _json(["project", "ls", "--json"]).get("projects") or []:
        pid = (p or {}).get("id")
        if not pid:
            continue
        try:
            full = _json(["project", "get", pid, "--json"]).get("project") or {}
        except AoError:
            continue
        if full.get("path") and _real(full["path"]) == want:
            return full
    return None


# AO postCreate step that wires tide's hooks into a session worktree whose .claude
# is the repo's committed copy (not a symlink) — see harness.install_hooks_in_worktree.
POST_CREATE_MARK = "install-hooks --worktree"


def post_create_command() -> Optional[str]:
    """``<abs tide> install-hooks --worktree``, or None when tide isn't on PATH (AO
    fails the whole spawn on a postCreate that can't run, so never guess a path)."""
    tide = shutil.which("tide")
    return "{0} {1}".format(shlex.quote(tide), POST_CREATE_MARK) if tide else None


def _untracked_present(tree: Path, rel: str) -> bool:
    """*rel* exists in the main tree and git tracks nothing under it."""
    if not (tree / rel).exists():
        return False
    return not (_git(str(tree), "ls-files", "--", rel) or "")


def hooks_config(current: dict, *, tree: Path) -> dict:
    """*current* with only what makes tide's hooks reach a session worktree merged
    in: symlinks for UNTRACKED ``.claude`` / ``.tide``, else (tracked ``.claude``)
    the ``postCreate`` step. Everything else in the config is left as is."""
    cfg = json.loads(json.dumps(current or {}))
    links = list(cfg.get("symlinks") or [])
    for rel in (".claude", ".tide"):
        if rel not in links and _untracked_present(tree, rel):
            links.append(rel)
    if links:
        cfg["symlinks"] = links
    if ".claude" not in links:
        cmd = post_create_command()
        hooks = list(cfg.get("postCreate") or [])
        if cmd and not any(POST_CREATE_MARK in str(c) for c in hooks):
            cfg["postCreate"] = hooks + [cmd]
    return cfg


def desired_config(current: dict, *, tree: Path, control_home: Optional[str],
                   role: Optional[str], skip_permissions: bool) -> dict:
    """*current* project config with tide's needs merged in (pure apart from git)."""
    cfg = json.loads(json.dumps(current or {}))
    env = dict(cfg.get("env") or {})
    env[PROJECT_ROOT_ENV] = str(tree)
    if control_home:
        env["TIDE_HOME"] = str(control_home)
    if role:
        env["TIDE_ROLE"] = role
    cfg["env"] = env
    cfg = hooks_config(cfg, tree=tree)
    if skip_permissions:
        ac = dict(cfg.get("agentConfig") or {})
        ac["permissions"] = BYPASS
        cfg["agentConfig"] = ac
    if not cfg.get("defaultBranch") or cfg.get("defaultBranch") == "auto":
        if _git(str(tree), "symbolic-ref", "-q", "refs/remotes/origin/HEAD") is None:
            branch = _git(str(tree), "symbolic-ref", "-q", "--short", "HEAD")
            if branch:
                cfg["defaultBranch"] = branch
    return cfg


def ensure_project(tree: Path, *, control_home: Optional[str], role: Optional[str],
                   skip_permissions: bool) -> Tuple[str, List[str]]:
    """Register *tree* in AO if needed and merge tide's config → (project id, notes)."""
    notes: List[str] = []
    proj = find_project(tree)
    if proj is None:
        taken = {p.get("id") for p in _json(["project", "ls", "--json"]).get("projects") or []}
        base = slug_for(tree)
        pid, n = base, 2
        while pid in taken:
            pid, n = "{0}-{1}".format(base, n), n + 1
        _call(["project", "add", "--path", str(tree), "--id", pid, "--name", tree.name,
               "--worker-agent", HARNESS])
        notes.append("registered {0} in AO as {1}".format(tree, pid))
        proj = _json(["project", "get", pid, "--json"]).get("project") or {"id": pid}
    pid = proj["id"]
    current = proj.get("config") or {}
    want = desired_config(current, tree=tree, control_home=control_home, role=role,
                          skip_permissions=skip_permissions)
    if want != current:
        notes.extend(write_config(pid, want))
    return pid, notes


def write_config(pid: str, want: dict) -> List[str]:
    """Write the WHOLE merged *want* and read it back.

    ``ao project set-config`` REPLACES the config (its help says so for the field
    flags like ``--post-create`` as much as for ``--config-json``), so the only
    safe write is read → merge → write the whole object. A key AO did not keep
    comes back as a note."""
    _call(["project", "set-config", pid, "--config-json",
           json.dumps(want, ensure_ascii=False)])
    try:
        got = (_json(["project", "get", pid, "--json"]).get("project") or {}).get(
            "config") or {}
    except AoError as exc:
        return ["{0}: written, read-back failed ({1})".format(pid, exc)]
    lost = sorted(k for k in want if got.get(k) != want[k])
    return ["{0}: AO did not keep {1}".format(pid, ", ".join(lost))] if lost else []


def terminated_sessions() -> Set[str]:
    """Ids of AO sessions AO itself marks terminated (``isTerminated``), read-only via
    ``ao session ls --json --include-terminated --all``. Their worktree dirs may still
    lie on disk; they are dead, not «live without hooks» (``tide doctor``)."""
    data = _json(["session", "ls", "--json", "--include-terminated", "--all"])
    rows = data.get("data") or data.get("sessions") or []
    return {str(r.get("id")).strip() for r in rows
            if isinstance(r, dict) and r.get("id") and r.get("isTerminated")}


def list_projects() -> List[dict]:
    """Every registered AO project in the ``project get`` shape (path + config)."""
    out: List[dict] = []
    for p in _json(["project", "ls", "--json"]).get("projects") or []:
        pid = (p or {}).get("id")
        if not pid:
            continue
        try:
            full = _json(["project", "get", pid, "--json"]).get("project") or {}
        except AoError:
            continue
        full.setdefault("id", pid)
        out.append(full)
    return out


@dataclass
class EnsureResult:
    """What :func:`ensure_hooks` found / did for one AO project."""

    pid: str
    path: str
    changed: bool = False
    added: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # SOC-101 cand 276: ``.claude`` is linked into the sessions, but the main
    # settings.json it points at lacks these tide hooks (short names).
    missing: List[str] = field(default_factory=list)

    @property
    def cure(self) -> str:
        """The one command that wires :attr:`missing` into the main tree."""
        return "cd {0} && tide install-hooks".format(shlex.quote(self.path))


def ensure_hooks(proj: dict, *, dry_run: bool = False) -> EnsureResult:
    """Make sure AO sessions of the registered *proj* get tide's hooks — whoever
    starts them (tide or AO directly). Only symlinks / ``postCreate`` are merged;
    env, agentConfig and the rest are carried over untouched."""
    pid = proj.get("id") or "?"
    res = EnsureResult(pid=pid, path=str(proj.get("path") or ""))
    tree = Path(res.path)
    if not res.path or not tree.is_dir():
        res.notes.append("path {0!r} not on disk — skipped".format(res.path))
        return res
    current = proj.get("config") or {}
    want = hooks_config(current, tree=tree)
    if ".claude" in (want.get("symlinks") or []):
        # a link is not proof: the sessions see exactly the main settings.json
        from .. import harness
        res.missing = harness.missing_hooks_at(harness.settings_path(tree))
    if want == current:
        return res
    for key in ("symlinks", "postCreate"):
        for item in want.get(key) or []:
            if item not in (current.get(key) or []):
                res.added.append("{0} += {1}".format(key, item))
    res.changed = True
    if not dry_run:
        res.notes.extend(write_config(pid, want))
    return res


def ensure_hooks_for_tree(tree: Path, *, dry_run: bool = False) -> Optional[EnsureResult]:
    """:func:`ensure_hooks` for the AO project registered at *tree*; None when AO
    is not installed or the tree is not an AO project (nothing to do)."""
    if not ao_available():
        return None
    proj = find_project(tree)
    return ensure_hooks(proj, dry_run=dry_run) if proj else None


def _control_home() -> Optional[str]:
    try:
        from .. import paths
        return str(paths.control_home())
    except Exception:  # noqa: BLE001 — no control-home is a fine answer
        return None


AO_DB_ENV = "TIDE_AO_DB"


def ao_db_path() -> Path:
    """AO's sqlite database (``$TIDE_AO_DB`` overrides; tests point it at tmp)."""
    raw = (os.environ.get(AO_DB_ENV) or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".ao" / "data" / "ao.db"


def _db_agent_session_id(handle: str) -> str:
    """``sessions.agent_session_id`` of *handle*, read-only; '' on any trouble."""
    db = ao_db_path()
    if not db.is_file():
        return ""
    import sqlite3
    try:
        con = sqlite3.connect("file:{0}?mode=ro".format(db), uri=True, timeout=2)
        try:
            row = con.execute("SELECT agent_session_id FROM sessions WHERE id = ?",
                              (handle,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return ""
    return str(row[0] or "").strip() if row else ""


def bring_app_up() -> None:
    """Best-effort: bring the AO desktop app to the front (no per-session focus verb)."""
    try:
        subprocess.run(["open", "-a", AO_APP_NAME], capture_output=True, timeout=15)
    except Exception:  # noqa: BLE001
        pass


# --- adapter -----------------------------------------------------------------

class AoAdapter(TerminalAdapter):
    """Spawn/focus/say through the desktop AO's CLI (see module doc)."""

    name = "ao"

    def build_commands(self, *, command: List[str], cwd: str, title: str = "",
                       project_id: str = "<project>", main: Optional[str] = None
                       ) -> List[List[str]]:
        """The ``ao spawn`` argv — pure; raises :class:`AoTranslateError`."""
        spec = translate(command, cwd, main_tree=main)
        return [self._spawn_argv(spec, project_id, title)]

    @staticmethod
    def _spawn_argv(spec: AoLaunch, project_id: str, title: str) -> List[str]:
        argv = [ao_bin(), "spawn", "--project", project_id, "--harness", HARNESS,
                "--name", (title or "tide")[:NAME_LIMIT], "--prompt", spec.prompt]
        if spec.model:
            argv[-2:-2] = ["--model", spec.model]
        return argv

    def spawn(self, *, command: List[str], cwd: str, title: str = "tide",
              dry_run: bool = False) -> SpawnResult:
        try:
            spec = translate(command, cwd)
        except AoTranslateError as exc:
            return SpawnResult(ok=False, detail="ao adapter: {0}".format(exc))
        if dry_run:
            spec.prompt = build_prompt(spec, main_tree=cwd)
            return SpawnResult(ok=True, ref=None, detail="dry-run (ao not executed)",
                               commands=[self._spawn_argv(spec, "<project>", title)])
        if not ao_available():
            return SpawnResult(ok=False, detail="Agent Orchestrator CLI not found at {0} — "
                               "install the desktop app or pick another adapter "
                               "(tide terminal-adapter --set orca)".format(ao_bin()))
        try:
            tree = main_tree(cwd)
            pid, notes = ensure_project(tree, control_home=_control_home(),
                                        role=spec.role,
                                        skip_permissions=spec.skip_permissions)
        except AoError as exc:
            return SpawnResult(ok=False, detail="ao adapter: {0}".format(exc))
        spec.prompt = build_prompt(spec, main_tree=str(tree))
        argv = self._spawn_argv(spec, pid, title)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as exc:
            return SpawnResult(ok=False, commands=[argv],
                               detail="ao spawn failed: {0}".format(exc))
        m = _SPAWNED.search(proc.stdout or "")
        if proc.returncode != 0 or not m:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return SpawnResult(ok=False, commands=[argv], detail="ao spawn failed: {0}".format(
                tail[-1] if tail else "exit {0}".format(proc.returncode)))
        sid = m.group(1)
        bring_app_up()
        detail = "spawned AO session {0} in project {1}".format(sid, pid)
        extra = notes + spec.notes
        if extra:
            detail += " (" + "; ".join(extra) + ")"
        return SpawnResult(ok=True, ref=sid, commands=[argv], detail=detail)

    # -- liveness / focus / say -------------------------------------------------

    def session(self, handle: str) -> Optional[dict]:
        """``ao session get`` → the session dict, or None when unknown/unreachable."""
        h = (handle or "").strip()
        if not h or not ao_available():
            return None
        try:
            return _json(["session", "get", h, "--json"], timeout=20).get("session") or None
        except (AoError, subprocess.SubprocessError, OSError):
            return None

    def alive(self, handle: str) -> bool:
        """Read-only: the session exists, is not terminated, and its agent has not
        exited (``activity.state != "exited"``)."""
        s = self.session(handle)
        return bool(s) and not s.get("isTerminated") and not _agent_exited(s)

    def agent_session_id(self, handle: str) -> str:
        """claude's own id inside AO session *handle* — the FIRST SIGNAL of the agent
        (SOC-101 F25). AO fills it once claude actually started; a fresh session that
        hangs on the folder-trust question has none. ``ao session get --json`` does
        not show it, so the session JSON is tried first (a future AO may) and the AO
        database is read READ-ONLY after. '' when unknown."""
        h = (handle or "").strip()
        if not h:
            return ""
        s = self.session(h) or {}
        for key in ("agentSessionId", "agent_session_id"):
            v = str(s.get(key) or "").strip()
            if v:
                return v
        return _db_agent_session_id(h)

    def first_signal(self, handle: str) -> bool:
        """The agent of *handle* is up for real: AO session live, agent not exited,
        and claude's id already known (:meth:`agent_session_id`)."""
        return self.alive(handle) and bool(self.agent_session_id(handle))

    def resume_agent(self, handle: str) -> bool:
        """``ao session resume-agent <id>`` — one revive of a stuck/exited agent."""
        h = (handle or "").strip()
        if not h or not ao_available():
            return False
        try:
            _call(["session", "resume-agent", h], timeout=120)
        except (AoError, subprocess.SubprocessError, OSError):
            return False
        return True

    def focusable(self, handle: str) -> bool:
        """Read-only prediction of :meth:`focus`: AO knows the session (live OR
        terminated-but-restorable, or agent exited-but-resumable). Used by
        ``tide return --dry-run``."""
        return self.session(handle) is not None

    def focus(self, handle: str) -> bool:
        """Live → bring AO up; terminated → ``ao session restore`` first; session up
        but agent exited → ``ao session resume-agent`` first; unknown → False."""
        s = self.session(handle)
        if not s:
            return False
        revive = None
        if s.get("isTerminated"):
            revive = ["session", "restore", handle.strip()]
        elif _agent_exited(s):
            revive = ["session", "resume-agent", handle.strip()]
        if revive:
            try:
                _call(revive, timeout=120)
            except (AoError, subprocess.SubprocessError, OSError):
                return False
        bring_app_up()
        return True

    def rename(self, handle: str, name: str) -> bool:
        """``ao session rename <id> <name>`` — True when AO took the new name."""
        h, n = (handle or "").strip(), " ".join((name or "").split())[:NAME_LIMIT]
        if not h or not n or not ao_available():
            return False
        try:
            _call(["session", "rename", h, n], timeout=20)
        except (AoError, subprocess.SubprocessError, OSError):
            return False
        return True

    def say(self, handle: str, text: str) -> bool:
        """``ao send --session <id> --message <text>``."""
        h = (handle or "").strip()
        t = (text or "").strip()
        if not h or not t or not ao_available():
            return False
        from ..manager_guard import suppress_if_manager

        if suppress_if_manager(h, t, source="ao.say"):  # SOC-232
            return False
        try:
            _call(["send", "--session", h, "--message", t], timeout=60)
        except (AoError, subprocess.SubprocessError, OSError):
            return False
        return True

