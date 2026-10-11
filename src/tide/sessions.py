"""tide.sessions — session-life domain ops that span arcs and the registry.

Today: :func:`reconcile_registry`, the SessionStart sweeper. The launcher records
``sid → terminal`` at spawn (the happy path); this covers every OTHER way a session
can exist — a bare ``claude`` run by hand, a spawn whose registry write failed, a
registry file lost — so "return to this session" keeps working on ALL paths of
ascent (principle №1), not only the launched ones.

And :func:`ao_relink`: inside the desktop Agent Orchestrator claude's id is minted by
AO, not by tide — the real id is learned after start and replaces tide's pre-pick.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import io as _io, paths, registry


def reconcile_registry(
    control_home: Path,
    project_root: Path,
    sid: str,
    *,
    terminals: List[Dict[str, str]],
    own_handle: str = "",
) -> Optional[str]:
    """Bind ``sid → handle``, preferring the session's OWN terminal over any guess.

    Rules, in order:
    - *own_handle* (from ``$ORCA_TERMINAL_HANDLE``, see :func:`registry.self_pair`) —
      the session telling us which pane it lives in. Exact, so it OVERWRITES an
      existing record: a session resumed after a reboot runs in a NEW tab while the
      registry still points at the corpse of the old one;
    - no own handle, but a record exists → do nothing (absence from ``orca terminal
      list`` is NOT death — background-adopted terminals hide from list yet focus
      fine, cand 101 — so overwriting a record against the list would re-open that trap);
    - no record → match live terminals by cwd (``worktreePath == project_root``), and
      ONLY when exactly one candidate — with several sessions in one project a cwd
      guess would bind the wrong tab (never guess between heads).

    The cwd match is the weakest rung and used to be the ONLY one, which is how the
    duplicate-tab loop sustained itself (cand 144): the first duplicate gave the
    project a second terminal, from then on the count was never 1, so the sweeper bailed
    on every session start and every press of ⟳ spawned another tab. *own_handle* does
    not count anything, so it cannot be defeated by a project having many sessions.

    Returns a short note of what happened (for the hook's stderr), or None.
    """
    s = (sid or "").strip()
    if not s:
        return None
    want = str(Path(project_root))
    own = (own_handle or "").strip()
    if own:
        if registry.recorded_handle(control_home, s) == own:
            return None  # already exact — no write
        registry.record(control_home, s, own, want)
        return "bound: {0} → {1} (own terminal)".format(s[:8], own)
    if registry.recorded_handle(control_home, s):
        return None
    candidates = [
        t for t in (terminals or [])
        if (t.get("worktreePath") or "").rstrip("/") == want.rstrip("/")
        and (t.get("handle") or "").strip()
    ]
    if len(candidates) != 1:
        return None
    handle = candidates[0]["handle"].strip()
    registry.record(control_home, s, handle, want)
    return "reconciled: {0} → {1}".format(s[:8], handle)


def self_register(
    control_home: Path,
    *,
    arc: str = "",
    env: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Bind the session THIS process runs inside to its terminal. Returns the handle written.

    The registry's second writer, next to the launcher. Every tide command — every hook,
    every offload — runs inside the session's own pane and therefore carries the exact
    pair in its environment, so binding costs one comparison and a write only when the
    pairing actually changed. That is what closes the last hole in «вернуться в сессию»:
    a session the launcher never saw (started by hand, or whose tab changed under it)
    registers itself the moment the agent does anything at all, instead of waiting for a
    SessionStart that already happened.

    Silent no-op outside a session terminal (board server, plain shell) — both env vars
    must be present, and nothing is ever guessed from their absence.
    """
    sid, handle = registry.self_pair(env)
    if not sid or not handle:
        return None
    if registry.recorded_handle(control_home, sid) == handle:
        return None
    registry.record(control_home, sid, handle, arc)
    return handle


# --- Agent Orchestrator: learn claude's REAL id after start -------------------
# The launcher mints a claude id before every spawn and records it everywhere (passport
# ``claude-session:``, handoff ``pickup-session``/``taken-by``, the registry). AO cannot
# be handed that id — it starts ``claude --session-id=<its own uuid>``. So under AO the
# minted id is a PRE-PICK no chat answers to, and everything keyed on the claude id
# (pulse, Stop nudges, multiples, ⟳) points at nothing. The real id only exists inside
# the session: the SessionStart payload carries it (stashed here per AO session), and
# ``$CLAUDE_CODE_SESSION_ID`` carries it to every process the session runs. Once it is
# known, :func:`ao_relink` swaps the pre-pick for it in every record. The same holds
# for a RE-ENTRY: the launcher pins the session's old (real) id, AO again starts a new
# chat — so the passport follows the new chat there too (live 09.10, 02-ao).

AO_STASH_FILENAME = "ao-sessions.json"


def ao_session_env(env: Optional[Dict[str, str]] = None) -> str:
    """The AO session id this process runs inside (``$AO_SESSION_ID``), or ``""``."""
    e = os.environ if env is None else env
    return (e.get(registry.ENV_AO_SESSION) or "").strip()


def _ao_stash_file(control_home: Path) -> Path:
    return Path(control_home) / AO_STASH_FILENAME


def _read_ao_stash(control_home: Path) -> Dict[str, Dict[str, str]]:
    try:
        data = json.loads(_ao_stash_file(control_home).read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remember_ao_sid(control_home: Path, ao_id: str, sid: str) -> bool:
    """Stash ``ao_id → real claude sid`` (SessionStart's payload). True when written."""
    a, s = (ao_id or "").strip(), (sid or "").strip()
    if not a or not s or is_side_sid(s) or _unproven_newcomer(control_home, a, s):
        return False
    data = _read_ao_stash(control_home)
    if (data.get(a) or {}).get("sid") == s:
        return False
    data[a] = {"sid": s, "ts": datetime.now().isoformat(timespec="seconds")}
    _io.atomic_write(_ao_stash_file(control_home),
                     json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return True


def ao_real_sid(control_home: Path, ao_id: str,
                env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """claude's real id for AO session *ao_id*: the process env first, then the stash.

    None when neither knows it — the caller must then NOT relink (a hook payload alone
    may be the echoed pre-pick, and relinking onto it would bury the real id)."""
    e = os.environ if env is None else env
    live = (e.get(registry.ENV_SESSION_ID) or "").strip()
    if live and not is_side_sid(live, env=e):
        return live
    stashed = ((_read_ao_stash(control_home).get((ao_id or "").strip()) or {})
               .get("sid") or "").strip()
    if stashed and not is_side_sid(stashed, env=e):
        return stashed
    if live or stashed:
        # only a service side chat is known (an older tide stashed it): the working
        # chat is the newest real transcript in this AO session's own worktree
        return _ao_main_sid(ao_id, e)
    return None


def is_side_sid(sid: str, env: Optional[Dict[str, str]] = None) -> bool:
    """True when *sid* is a service side chat of the SDK host («Summarize this agent
    result…»), never the session itself — the one filter is
    :func:`tide.context_fill.is_side_transcript`."""
    from . import context_fill  # lazy: the hooks import sessions on every prompt

    e = os.environ if env is None else env
    base = Path(e.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "projects"
    return context_fill.is_side_sid(sid, root=base)


def _ao_main_sid(ao_id: str, env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """sid of the newest working chat in AO session *ao_id*'s worktree, or None."""
    from . import context_fill

    e = os.environ if env is None else env
    base = Path(e.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "projects"
    dirs = [base / d for d in _ao_chat_dirs((ao_id or "").strip())]
    found = context_fill.main_transcript_in([d for d in dirs if d.is_dir()])
    return found.stem if found is not None else None


def _chat_dirs(sid: str, env: Optional[Dict[str, str]] = None) -> Set[str]:
    """Names of the claude project dirs holding a chat under *sid* (``projects/*/<sid>.jsonl``).

    Empty = no chat anywhere: a pure pre-pick nobody ever answered to."""
    e = os.environ if env is None else env
    base = Path(e.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "projects"
    try:
        return {p.parent.name for p in base.glob("*/{0}.jsonl".format(sid))}
    except OSError:
        return set()


def _claude_dir_name(path: Path) -> str:
    """claude's project-dir name for a cwd: every non-alphanumeric char becomes ``-``."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _ao_chat_dirs(ao_id: str) -> Set[str]:
    """claude project-dir names of AO session *ao_id*'s own worktree(s).

    AO runs a session in ``$AO_DATA_DIR/worktrees/<project>/<ao_id>``; a chat stored
    under that dir ran IN this AO session. Empty when the worktree can't be found."""
    try:
        root = paths.ao_worktrees_root()
        found = [p for p in root.glob("*/{0}".format(ao_id)) if p.is_dir()]
    except OSError:
        return set()
    out: Set[str] = set()
    for p in found:
        out.add(_claude_dir_name(p))
        try:
            out.add(_claude_dir_name(p.resolve()))
        except OSError:
            pass
    return out


def _unproven_newcomer(control_home: Path, ao_id: str, sid: str,
                       env: Optional[Dict[str, str]] = None) -> bool:
    """*sid* has no chat on disk yet, while AO session *ao_id* already has a proven one.

    The SDK host's service chat fires SessionStart before its transcript (and the
    «Summarize…» prompt that unmasks it) is written — live 09.10 music-3 stashed
    bafb0781 that way. Such a newcomer is not trusted over a working chat: a real new
    chat (AO restarted the agent) proves itself on its first prompt, when
    ``handoff-confirm`` relinks with the transcript already on disk."""
    if _chat_dirs(sid, env):
        return False
    prev = ((_read_ao_stash(control_home).get(ao_id) or {}).get("sid") or "").strip()
    return bool(prev) and prev != sid and bool(_chat_dirs(prev, env)) \
        and not is_side_sid(prev, env)


# The AO launch turn (:func:`tide.adapters.ao.build_prompt`) names the arc's seed and
# the pre-pick it was launched under — the chat's own proof of which passport it owns.
# SOC-181: the turn now says «Твой старт — сид <path> (роль: …): …»; chats launched
# before it carry «Прочитай файл <path> целиком» — both are read back.
_LAUNCH_SEED_RE = re.compile(
    r"(?:Прочитай файл|Твой старт — сид) (\S+?)(?= целиком|:?\s|:$|$)")
_LAUNCH_PIN_RE = re.compile(r'"session_id":\s*"([0-9A-Fa-f-]{36})"')


def _launch_claim(sid: str, env: Optional[Dict[str, str]] = None
                  ) -> Tuple[Optional[Path], str]:
    """``(passport, pre-pick)`` named by the first prompt of chat *sid*; ``(None, "")``
    when its launch turn names no seed (not a tide launch, or no chat on disk)."""
    from . import context_fill

    e = os.environ if env is None else env
    base = Path(e.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "projects"
    path = context_fill.transcript_for(sid, base)
    if path is None:
        return None, ""
    prompt = context_fill.transcript_head(path)["prompt"] or ""
    m = _LAUNCH_SEED_RE.search(prompt)
    if not m:
        return None, ""
    pp = Path(m.group(1)).parent.parent / "arc.md"
    pin = _LAUNCH_PIN_RE.search(prompt)
    return (pp if pp.is_file() else None), (pin.group(1) if pin else "")


def _passport_of(arc: str) -> Optional[Path]:
    p = Path(arc or "")
    if not str(arc or "").strip():
        return None
    pp = p if p.name.endswith(".md") else p / "arc.md"
    return pp if pp.is_file() else None


def is_closed_passport(pp: Optional[Path]) -> bool:
    """True when passport *pp* belongs to a CLOSED arc — never re-bound to a new chat.

    Closed is tide's seal (:func:`tide.arc.stream.close`): the session dir or its
    thread dir wrapped in ``__…__``, and ``status: done`` in the passport. An AO id
    is reused across months (F15: tide-stack-12 relinked the archived
    ``__25-@edinyy-sloy__/__04-pult__`` and AO took its title) — a sealed arc is
    history, its ``claude-session:``/``title:`` stay as they were.
    """
    if pp is None:
        return False
    from . import fields, slug  # lazy: keep sessions light for the hooks

    p = Path(pp)
    entry = p.parent if p.name.endswith(".md") else p
    if any(slug.is_closed_entry(seg) for seg in entry.parts):
        return True
    pf = p if p.name.endswith(".md") else p / "arc.md"
    return (fields.read_field(pf, "status") or "").strip().lower() in ("done", "closed")


def ao_relink(
    control_home: Path,
    ao_id: str,
    real: str,
    *,
    project_root: Optional[Path] = None,
    also: Iterable[str] = (),
    env: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Swap the stale claude ids tide recorded for AO session *ao_id* to the *real* one.

    Candidates are the registry sids on this AO handle (plus any in *also*, e.g. the id
    echoed to ``handoff-confirm``) that differ from *real*. Each is one of three:

    * **pre-pick** — no claude chat on disk at all: tide minted it at launch and no chat
      ever answered. Rewritten everywhere: handoff ``pickup-session``/``taken-by``/
      ``from-session``, the passport's ``claude-session:``, the registry key.
    * **continuation** — a REGISTRY sid whose chat lives elsewhere (another AO worktree,
      the main tree): the launcher re-entered that session in this AO session, and AO,
      unable to resume it, started a new chat. The session now lives in the new chat:
      the passport's ``claude-session:`` moves to *real* and its ``ended:`` stamp (the
      old chat's) is dropped. Handoff records are history and stay as they are.
    * **ran here** — its chat lives in THIS AO session's own worktree (a reused AO id,
      an earlier chat of this session) or the worktree can't be located: never touched.

    Passports found: the registry arc, the handoff seed's session, and — with
    *project_root* — any session pinning the stale id. Idempotent; silent no-op when
    nothing matches. Returns the ids rewritten (pre-picks first).

    **Launch claim** — the registry is not the only witness: a double raise of one
    offer (live 09.10, music-3 + music-4) leaves the pre-pick on the OTHER AO handle,
    where the loop above never looks. The real chat's first prompt names the seed of
    its arc (:func:`_launch_claim`), so that passport is claimed for *real* when it
    pins nothing, a pre-pick (no chat), or a service chat — never another working chat.
    A service chat never relinks at all, nor does a chat not yet on disk while this
    AO session already has a proven one (:func:`_unproven_newcomer`).

    A CLOSED arc's passport (:func:`is_closed_passport`) is never rewritten (F15).
    """
    from . import fields, handoff_queue  # lazy: keep sessions light for the hooks

    a, r = (ao_id or "").strip(), (real or "").strip()
    if not a or not r or is_side_sid(r, env) or _unproven_newcomer(control_home, a, r, env):
        return []
    reg = registry.read(control_home)
    on_handle = [s.strip() for s, e in reg.items()
                 if registry.is_sid_key(s) and ((e or {}).get("handle") or "").strip() == a]
    here: Optional[Set[str]] = None  # this AO session's chat dirs, looked up on demand
    pre: List[str] = []
    cont: List[str] = []
    for s in on_handle + [x.strip() for x in also if x]:
        if not s or s == r or s in pre or s in cont:
            continue
        dirs = _chat_dirs(s, env)
        if not dirs:
            pre.append(s)
            continue
        if s not in on_handle:
            continue  # an echoed id with a real chat: not ours to move
        if here is None:
            here = _ao_chat_dirs(a)
        if here and not (dirs & here):
            cont.append(s)

    claim_pp, _pin = _launch_claim(r, env)
    if is_closed_passport(claim_pp):
        claim_pp = None
    claimed_by: Dict[str, Path] = {}  # stale pin → the passport the launch turn names
    if claim_pp is not None:
        cur = (fields.read_field(claim_pp, "claude-session") or "").strip()
        if cur != r and (not cur or not _chat_dirs(cur, env) or is_side_sid(cur, env)):
            if not cur:
                fields.set_field(claim_pp, "claude-session", r)
                fields.remove_field(claim_pp, "ended")
            else:
                claimed_by[cur] = claim_pp
                if cur not in pre and cur not in cont:
                    pre.append(cur)
    if not pre and not cont:
        return []

    offers = handoff_queue.list_offers(control_home) if pre else []
    arc_for_real = ""
    for p in pre + cont:
        passports = []
        entry_arc = str((reg.get(p) or {}).get("arc") or "")
        if _passport_of(entry_arc) is not None:
            passports.append(_passport_of(entry_arc))
            if not is_closed_passport(_passport_of(entry_arc)):
                arc_for_real = arc_for_real or entry_arc
        for rec in (offers if p in pre else []):
            hit = False
            for key, field in (("pickup_session", "pickup-session"),
                               ("taken_by", "taken-by"), ("from_session", "from-session")):
                if str(rec.get(key) or "").strip() == p:
                    handoff_queue._set_field(Path(rec["path"]), field, r)
                    hit = True
            seed = str(rec.get("seed") or "")
            if hit and seed and seed != "-":
                pp = Path(seed).parent.parent / "arc.md"
                if pp.is_file():
                    passports.append(pp)
        if p in claimed_by:
            passports.append(claimed_by[p])
            arc_for_real = arc_for_real or str(claimed_by[p].parent)
        if project_root is not None:
            from .offload import find_session_by_claude_id
            found = find_session_by_claude_id(Path(project_root), p)
            if found is not None:
                passports.append(found if found.name.endswith(".md") else found / "arc.md")
        for pp in passports:
            if is_closed_passport(pp):
                continue  # F15: a sealed arc is history, a reused AO id must not wake it
            if (fields.read_field(pp, "claude-session") or "").strip() == p:
                fields.set_field(pp, "claude-session", r)
                # the new chat is alive: an ``ended:`` stamp belongs to the old one
                fields.remove_field(pp, "ended")

    # registry: drop the stale ids, keep ONE entry for the real id on this handle —
    # with the most specific arc (a session dir beats the project root the sweeper wrote)
    stale = pre + cont
    data = registry.read(control_home)
    old_arcs = [str((data.get(p) or {}).get("arc") or "") for p in stale]
    for p in stale:
        data.pop(p, None)
    cur = data.get(r) or {}
    arc = (arc_for_real or str(cur.get("arc") or "")
           or next((x for x in old_arcs if x and not is_closed_passport(_passport_of(x))), ""))
    data[r] = {"handle": a, "arc": arc,
               "ts": datetime.now().isoformat(timespec="seconds")}
    _io.atomic_write(registry.registry_file(control_home),
                     json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return stale
