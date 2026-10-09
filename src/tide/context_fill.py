"""tide.context_fill — how full a claude session's context window is.

One answer, two readers:

- the **transcript** (``~/.claude/projects/<dir>/<sid>.jsonl``): every main-chain
  assistant reply carries ``message.usage``; ``input_tokens +
  cache_creation_input_tokens + cache_read_input_tokens`` is everything the model
  saw on that turn — the fill. A ``system/compact_boundary`` row marks a
  compaction (with ``compactMetadata.preTokens/postTokens``).
- AO's **ACP journal** (``$AO_DATA_DIR/chat-hosts/<ao-id>/acp-prompt.journal``):
  the host's own ``usage_update {used, size}`` — the live number and the real
  window, preferred when present (``ao_journal_usage``).

The window is 200k unless the transcript proves 1M: a model whose default
window is 1M (``model_window`` — opus/sonnet ≥ 4.6, haiku ≥ 5, fable), a model
id with the ``[1m]`` suffix (``cost-state.modelUsage`` keys carry it), or a
fill/compaction that already went past 200k.

Read-only, stdlib only. The board's context graph and the auto-handoff hook call
the same ``context_fill`` — a hook already holds ``transcript_path`` in its
payload and can pass it straight in.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Union

WINDOW_DEFAULT = 200_000
WINDOW_1M = 1_000_000

# the journal is append-only and grows to megabytes; the last usage_update is
# always near the end, so only the tail is read
_JOURNAL_TAIL = 512 * 1024


def fill_of(usage: Optional[dict]) -> int:
    """Tokens the model saw on one turn: fresh input + cache written + cache read."""
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        try:
            total += int(usage.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def projects_dir() -> Path:
    """claude's transcript root (``$CLAUDE_CONFIG_DIR/projects``, else ``~/.claude/projects``)."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "projects"


def transcript_for(sid: str, root: Optional[Path] = None) -> Optional[Path]:
    """The transcript of claude session *sid*, or None when no chat exists on disk.

    Several project dirs can hold the same sid (a resume from another cwd); the
    most recently written one is the live tail."""
    sid = (sid or "").strip()
    if not sid or "/" in sid:
        return None
    try:
        hits = list((root or projects_dir()).glob("*/{0}.jsonl".format(sid)))
    except OSError:
        return None
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def _resolve(target: Union[str, Path], root: Optional[Path]) -> Optional[Path]:
    p = Path(str(target))
    if p.suffix == ".jsonl" and p.is_file():
        return p
    return transcript_for(str(target), root)


# claude-<family>-<major>[-<minor>][-<date>] — the transcript's message.model
_MODEL_RE = re.compile(r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d{1,2}))?(?!\d)")


def model_window(model: str) -> Optional[int]:
    """The DEFAULT window of a model id, or None when the id is unknown.

    The transcript carries the bare id (``claude-opus-5-5``) with no ``[1m]``
    suffix even when the session runs on 1M — AO's journal proved it (opus-5-5:
    ``size 1000000``). By the model table: Fable/Mythos — 1M; Opus and Sonnet
    from 4.6 on — 1M; Haiku from 5 on — 1M; older (Haiku 4.5, *-4-5, 3.x) — 200k.
    """
    m = _MODEL_RE.search(model or "")
    if not m:
        return None
    family, major, minor = m.group(1), int(m.group(2)), int(m.group(3) or 0)
    if family in ("fable", "mythos"):
        return WINDOW_1M
    if family in ("opus", "sonnet"):
        return WINDOW_1M if (major, minor) >= (4, 6) else WINDOW_DEFAULT
    return WINDOW_1M if major >= 5 else WINDOW_DEFAULT


def window_for(model: str, peak: int, compact_pre: int = 0) -> int:
    """1M when the model id says ``[1m]``, the model's default window is 1M, or the
    session already went past 200k; else 200k."""
    if "[1m]" in (model or "") or peak > WINDOW_DEFAULT or compact_pre > WINDOW_DEFAULT:
        return WINDOW_1M
    return model_window(model) or WINDOW_DEFAULT


def read_transcript(path: Path) -> Dict:
    """Walk one transcript: fill per assistant turn and the compactions between them.

    ``points`` is ``[(at, tokens), ...]`` in turn order — one per API reply (a reply
    split across several rows shares ``message.id`` and counts once). Sidechain rows
    (subagents) and synthetic replies are not the session's own context and are
    skipped. ``compacts[i]["point"]`` is the index of the first point AFTER that
    compaction."""
    points: List[List] = []
    seen: Dict[str, int] = {}
    compacts: List[Dict] = []
    model, wide = "", False
    sid = path.stem
    with open(path, "rb") as fh:
        for raw in fh:
            # cheap prefilter: most rows are tool output with neither marker
            if b'"usage"' not in raw and b"compact_boundary" not in raw \
                    and b'"cost-state"' not in raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("isSidechain"):
                continue
            kind = row.get("type")
            if kind == "system" and row.get("subtype") == "compact_boundary":
                meta = row.get("compactMetadata") or {}
                compacts.append({"point": len(points), "at": row.get("timestamp") or "",
                                 "pre": int(meta.get("preTokens") or 0),
                                 "post": int(meta.get("postTokens") or 0),
                                 "trigger": meta.get("trigger") or ""})
            elif kind == "cost-state":
                wide = wide or any("[1m]" in k for k in (row.get("modelUsage") or {}))
            elif kind == "assistant":
                msg = row.get("message") or {}
                m = msg.get("model") or ""
                if m.startswith("<"):  # <synthetic> — no API call behind it
                    continue
                tokens = fill_of(msg.get("usage"))
                if tokens <= 0:
                    continue
                model = m or model
                key = msg.get("id") or row.get("requestId") or row.get("uuid") or ""
                if key and key in seen:
                    points[seen[key]][1] = tokens
                    continue
                if key:
                    seen[key] = len(points)
                points.append([row.get("timestamp") or "", tokens])
    peak = max((t for _, t in points), default=0)
    pre = max((c["pre"] for c in compacts), default=0)
    window = window_for(model + ("[1m]" if wide else ""), peak, pre)
    return {"sid": sid, "path": str(path), "model": model, "window": window,
            "peak": peak, "points": [tuple(p) for p in points], "compacts": compacts}


def _summary(data: Dict, window: Optional[int] = None) -> Dict:
    pts = data["points"]
    tokens = pts[-1][1] if pts else 0
    # a compaction after the last reply means the next turn starts from its post size
    if data["compacts"] and data["compacts"][-1]["point"] >= len(pts):
        tokens = data["compacts"][-1]["post"] or tokens
    win = window or data["window"]
    return {"sid": data["sid"], "path": data["path"], "model": data["model"],
            "tokens": tokens, "window": win,
            "percent": round(100.0 * tokens / win, 1) if win else 0.0,
            "peak": data["peak"], "turns": len(pts), "compacts": len(data["compacts"]),
            "at": pts[-1][0] if pts else ""}


def context_fill(target: Union[str, Path], root: Optional[Path] = None,
                 window: Optional[int] = None) -> Optional[Dict]:
    """How full session *target* is right now — a claude sid or a transcript path.

    Returns ``{"sid", "path", "model", "tokens", "window", "percent", "peak",
    "turns", "compacts", "at"}`` (``tokens`` = fill on the last turn, ``percent`` of
    ``window``), or None when there is no transcript. *window* overrides the
    guess (e.g. AO's journal knows the real size)."""
    path = _resolve(target, root)
    if path is None:
        return None
    try:
        return _summary(read_transcript(path), window)
    except OSError:
        return None


def _thin(n: int, keep: List[int], limit: int) -> List[int]:
    """Indices 0..n-1 thinned to about *limit*, always keeping *keep* and the ends."""
    if n <= limit:
        return list(range(n))
    step = (n - 1) / float(max(1, limit - 1))
    chosen = {int(round(i * step)) for i in range(limit)}
    chosen.update(k for k in keep if 0 <= k < n)
    return sorted(chosen)


def context_series(target: Union[str, Path], root: Optional[Path] = None,
                   window: Optional[int] = None, max_points: int = 120) -> Optional[Dict]:
    """``context_fill`` plus the curve for a graph.

    Adds ``points: [[turn, tokens], ...]`` (turn is 1-based, thinned to about
    *max_points* — the turns on both sides of a compaction always survive, so the
    drop stays visible) and ``compact_marks: [{"turn", "pre", "post", "trigger",
    "at"}]`` where ``turn`` is the first turn after the compaction."""
    path = _resolve(target, root)
    if path is None:
        return None
    try:
        data = read_transcript(path)
    except OSError:
        return None
    out = _summary(data, window)
    pts = data["points"]
    keep = []
    for c in data["compacts"]:
        keep += [c["point"] - 1, c["point"]]
    out["points"] = [[i + 1, pts[i][1]] for i in _thin(len(pts), keep, max_points)]
    out["compact_marks"] = [{"turn": c["point"] + 1, "pre": c["pre"], "post": c["post"],
                             "trigger": c["trigger"], "at": c["at"]}
                            for c in data["compacts"]]
    return out


def ao_journal_usage(ao_id: str, data_dir: Optional[Path] = None) -> Optional[Dict]:
    """The last ``usage_update`` AO's chat host saw for session *ao_id*.

    ``{"sid": <claude session id>, "used": int, "size": int}`` or None when the
    session has no journal (terminal sessions don't) or no usage yet."""
    ao_id = (ao_id or "").strip()
    if not ao_id or "/" in ao_id:
        return None
    base = data_dir or Path(os.environ.get("AO_DATA_DIR") or (Path.home() / ".ao" / "data"))
    path = Path(base) / "chat-hosts" / ao_id / "acp-prompt.journal"
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - _JOURNAL_TAIL))
            tail = fh.read()
    except OSError:
        return None
    for raw in reversed(tail.splitlines()):
        if b"usage_update" not in raw:
            continue
        try:
            params = json.loads(raw).get("params") or {}
        except ValueError:
            continue  # the cut first line of the tail, or a torn write
        upd = params.get("update") or {}
        if upd.get("sessionUpdate") != "usage_update":
            continue
        try:
            return {"sid": params.get("sessionId") or "", "used": int(upd.get("used") or 0),
                    "size": int(upd.get("size") or 0)}
        except (TypeError, ValueError):
            return None
    return None


def main(argv: Optional[List[str]] = None) -> int:
    """``python -m tide.context_fill <sid|transcript.jsonl> [--series]`` — JSON to stdout."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    series = "--series" in args
    args = [a for a in args if a != "--series"]
    if not args:
        print("usage: python -m tide.context_fill <sid|transcript.jsonl> [--series]",
              file=sys.stderr)
        return 2
    res = (context_series if series else context_fill)(args[0])
    if res is None:
        print("no transcript for {0}".format(args[0]), file=sys.stderr)
        return 1
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
