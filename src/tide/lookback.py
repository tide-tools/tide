"""tide.lookback — the reading watermark (отлив: cand 10, watermark primitive).

The ``lookback`` routine reads traces BACKWARD by law (fixed bins swept to
zero, exception-list out). This module owns that law's one git-native
primitive: the watermark ref

    refs/lookback/<reader>/<scope>

recording "read up to HERE". ``status`` shows the mark + how many trace
commits hang behind it (a run's catch-up is ``git log <ref>..HEAD``);
``mark`` moves it — compare-and-swap, meant to be called ONLY after a full
pass, so an interrupted run honestly re-reads the same delta next time;
``log`` is the audit trail of every move (the ref's reflog, created on first
mark). ON-DEMAND ONLY — no daemon, no hook, no LLM: plain git plumbing,
milliseconds. That is the отлив cost frame: a working session pays zero, the
entry pays one status line, the full pass is its own run.

Two layers as everywhere else: pure-ish functions (git in/out, argparse-free,
unit-testable) + a thin ``register``/handler CLI skin.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Tuple

from . import paths, slug
from .arc.stream import StreamError

REF_PREFIX = "refs/lookback"
DEFAULT_READER = "agent"
TRACE_PATH = ".tide"  # bearing traces live here; chore noise outside doesn't count


class LookbackError(StreamError):
    """A user-facing lookback error (no git, bad ref part, CAS mismatch)."""


# --- git plumbing (pure-ish) -------------------------------------------------

def _git(root: Path, *argv: str) -> Tuple[int, str, str]:
    """Run ``git *argv`` in *root*; return ``(code, stdout, stderr)`` stripped."""
    try:
        proc = subprocess.run(
            ["git", *argv], cwd=str(root), capture_output=True, text=True,
        )
    except OSError as exc:
        raise LookbackError("lookback: cannot run git — {0}".format(exc)) from exc
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _require_repo(root: Path) -> None:
    code, _out, _err = _git(root, "rev-parse", "--git-dir")
    if code != 0:
        raise LookbackError(
            "lookback: {0} is not a git repository — the watermark needs the "
            "trace log (git) to exist".format(root)
        )


def ref_name(reader: str, scope: str) -> str:
    """The watermark ref for *reader*/*scope* (both slugified; error on empty)."""
    r, s = slug.slugify(reader), slug.slugify(scope)
    if not r or not s:
        raise LookbackError(
            "lookback: empty reader/scope after slugify ({0!r}/{1!r})".format(reader, scope)
        )
    return "{0}/{1}/{2}".format(REF_PREFIX, r, s)


def current(root: Path, ref: str) -> Optional[str]:
    """The commit the watermark *ref* points at, or None when never set."""
    _require_repo(root)
    code, out, _err = _git(root, "rev-parse", "--verify", "--quiet", ref)
    return out if code == 0 and out else None


def gap(root: Path, ref: str, *, trace_path: str = TRACE_PATH) -> Optional[int]:
    """Trace commits behind the watermark (``ref..HEAD -- trace_path``).

    None when the ref was never set (first run: the whole history is the
    delta). This is the number the entry-line светофор shows.
    """
    if current(root, ref) is None:
        return None
    code, out, err = _git(root, "rev-list", "--count", "{0}..HEAD".format(ref),
                          "--", trace_path)
    if code != 0:
        raise LookbackError("lookback: rev-list failed — {0}".format(err))
    return int(out or "0")


def mark(root: Path, ref: str, *, at: str = "HEAD") -> Tuple[Optional[str], str]:
    """Move the watermark to *at* (CAS against the current value) → ``(old, new)``.

    Call ONLY after a full pass — the CAS (old value passed to ``update-ref``)
    means a concurrent mover loses loudly instead of silently clobbering the
    mark. ``--create-reflog`` makes every move auditable via :func:`log`.
    """
    _require_repo(root)
    code, new, err = _git(root, "rev-parse", "--verify", "{0}^{{commit}}".format(at))
    if code != 0:
        raise LookbackError("lookback: cannot resolve {0!r} — {1}".format(at, err))
    old = current(root, ref)
    argv = ["update-ref", "--create-reflog", "-m",
            "lookback mark (full pass)", ref, new]
    if old:
        argv.append(old)  # CAS: fail if someone moved the ref meanwhile
    code, _out, err = _git(root, *argv)
    if code != 0:
        raise LookbackError(
            "lookback: mark refused (ref moved under you?) — {0}".format(err)
        )
    return old, new


def reflog(root: Path, ref: str) -> str:
    """The ref's reflog — the free audit trail of every mark ('' when none)."""
    _require_repo(root)
    code, out, _err = _git(root, "reflog", "show", "--date=iso", ref)
    return out if code == 0 else ""


# --- render ------------------------------------------------------------------

def render_status(root: Path, ref: str) -> str:
    """One human line: the mark + how far behind it the traces are."""
    cur = current(root, ref)
    if cur is None:
        return ("lookback: {0} — no watermark yet (first run reads from the "
                "root; set it with 'tide lookback mark' after a full pass)"
                .format(ref))
    behind = gap(root, ref)
    tail = "up to date" if behind == 0 else "{0} trace commit(s) behind".format(behind)
    return "lookback: {0} @ {1} — {2}".format(ref, cur[:12], tail)


# --- CLI ---------------------------------------------------------------------

def _root() -> Path:
    return paths.require_tide_root()


def _ref_from_args(root: Path, args) -> str:
    scope = getattr(args, "scope", None) or Path(root).name
    reader = getattr(args, "reader", None) or DEFAULT_READER
    return ref_name(reader, scope)


def _cmd_status(args) -> int:
    root = _root()
    print(render_status(root, _ref_from_args(root, args)))
    return 0


def _cmd_mark(args) -> int:
    root = _root()
    ref = _ref_from_args(root, args)
    old, new = mark(root, ref, at=getattr(args, "at", None) or "HEAD")
    frm = old[:12] if old else "(unset)"
    print("tide: lookback mark {0}: {1} → {2}".format(ref, frm, new[:12]))
    return 0


def _cmd_log(args) -> int:
    root = _root()
    out = reflog(root, _ref_from_args(root, args))
    print(out if out else "(no marks yet)")
    return 0


def _add_common(p) -> None:
    p.add_argument("--reader", default=DEFAULT_READER,
                   help="who is reading (default: {0})".format(DEFAULT_READER))
    p.add_argument("--scope", help="what is being read (default: project dir name)")


def register(subparsers) -> None:
    """Add the top-level ``lookback`` command group (called by cli.py)."""
    p = subparsers.add_parser(
        "lookback",
        help="reading watermark (отлив): status / mark after a full pass / audit log",
    )
    lsub = p.add_subparsers(dest="lookback_cmd")

    sp = lsub.add_parser("status", help="the mark + trace commits behind it")
    _add_common(sp)
    sp.set_defaults(func=_cmd_status, _cmd="lookback status")

    mp = lsub.add_parser("mark", help="move the watermark (CAS) — only after a FULL pass")
    _add_common(mp)
    mp.add_argument("--at", default="HEAD", help="commit to mark (default: HEAD)")
    mp.set_defaults(func=_cmd_mark, _cmd="lookback mark")

    gp = lsub.add_parser("log", help="audit trail of marks (the ref's reflog)")
    _add_common(gp)
    gp.set_defaults(func=_cmd_log, _cmd="lookback log")

    # bare `tide lookback` behaves like `tide lookback status`
    _add_common(p)
    p.set_defaults(func=_cmd_status, _cmd="lookback")
