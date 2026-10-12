"""tide.release_report — ``tide report``: one word from a stuck user reaches the maintainer.

The hole this closes: tide can now go OUT (``tide release``) and stay current
(``tide self-update``), but nothing came BACK. Someone we handed tide to hits a
wall and the only channel is "message the author and try to remember what you
were doing" — which loses the version, the install shape and the environment,
i.e. everything that actually locates the bug.

**Channel.** No server is invented and no account is demanded. Two rungs, in this
order:

1. ``gh issue create`` against the release repo — used ONLY when ``gh`` is already
   on PATH and already authenticated. It costs the user nothing (the stack
   already leans on ``gh`` for releases) and lands in the maintainer's normal
   inbox with a URL both sides can point at.
2. otherwise a **file** under ``$TIDE_HOME/reports/``, copied to the clipboard
   when the platform offers one, which the person sends however they already talk
   to the maintainer. Zero accounts, works offline.

**Privacy is the hard part.** A diagnostic bundle is the classic way private work
leaks: someone reports a tide bug and ships the names of their employer's repos
with it. So the rule here is inverted from the usual "collect everything, redact
later" — we collect a FIXED, small set of facts that diagnose a tide problem, and
nothing that describes the user's projects. What goes and what deliberately does
not is spelled out in :func:`collect` and shown to the human, in full, before
anything is sent.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import __version__
from .update.source import (
    default_marker_path,
    is_editable_install,
    read_marker,
    resolve_source,
    tide_home_dir,
)

REPORT_REPO = "tide-tools/tide"
MAX_LOG_LINES = 60

# What the report deliberately leaves behind. Printed verbatim to the human, so it
# is a promise they can check rather than a claim they must trust.
WITHHELD = [
    "the names and paths of your projects (only 'how many' is counted)",
    "anything inside your arcs, works, candidates or canon",
    "the contents of any file you did not point at with --log",
    "environment variables, tokens and keychain items",
    "your home directory path and username (rewritten to ~ and <user>)",
]


# --- redaction ---------------------------------------------------------------


def redaction_patterns() -> List[tuple]:
    """(regex, replacement) pairs that strip THIS machine's identity from text.

    Absolute home paths collapse to a bare ``~/…`` — the whole tail, not just the
    prefix. Half-redacting a path like <home>/work/acme-client/x.py into
    ``~/work/acme-client/x.py`` still ships the client's name, which is exactly
    the leak this exists to prevent. A tide bug is diagnosed from the version and
    the traceback, not from where on disk the user keeps their work.
    """
    home = str(Path.home())
    user = Path.home().name
    pats: List[tuple] = []
    if home and home not in ("/", ""):
        pats.append((re.compile(re.escape(home) + r"[^\s\"'`,;:)\]]*"), "~/…"))
    pats.append((re.compile(r"/(?:Users|home)/[^/\s\"'`,;:)\]]+[^\s\"'`,;:)\]]*"), "~/…"))
    if user and len(user) > 2:
        pats.append((re.compile(r"\b" + re.escape(user) + r"\b"), "<user>"))
    return pats


def redact(text: str) -> str:
    """Rewrite *text* so it carries no home path and no username."""
    for pattern, replacement in redaction_patterns():
        text = pattern.sub(replacement, text)
    return text


# --- collection ---------------------------------------------------------------


@dataclass
class Report:
    """One report: the user's words plus the fixed diagnostic facts."""

    what_happened: str
    facts: List[tuple] = field(default_factory=list)
    doctor: List[tuple] = field(default_factory=list)
    log_tail: Optional[str] = None

    def title(self) -> str:
        first = (self.what_happened or "tide report").strip().splitlines()[0]
        return "tide report: {0}".format(first[:80])

    def render(self) -> str:
        out: List[str] = ["## what happened", "", self.what_happened.strip() or "(not described)", ""]
        out += ["## install", ""]
        for k, v in self.facts:
            out.append("- **{0}**: {1}".format(k, v))
        if self.doctor:
            out += ["", "## tide doctor", ""]
            for name, status in self.doctor:
                out.append("- {0}: {1}".format(name, status))
        if self.log_tail:
            out += ["", "## log tail (last {0} lines, redacted)".format(MAX_LOG_LINES), "", "```", self.log_tail, "```"]
        out += ["", "## deliberately not included", ""]
        out += ["- " + line for line in WITHHELD]
        return "\n".join(out) + "\n"


def install_shape(source=None) -> str:
    """A one-phrase answer to "how is this tide installed?" — the first thing to know.

    Nearly every "it does not work on my machine" splits on this axis: an editable
    dev checkout, a Homebrew keg, a uv-tool sandbox and a plain venv fail in
    completely different ways, and the user cannot be expected to know which they
    have.
    """
    exe = sys.executable or ""
    if "/Cellar/tide/" in exe:
        return "homebrew keg"
    if source is not None and is_editable_install(source):
        return "editable checkout (runs from source)"
    if getattr(source, "uv_tool", False):
        return "uv tool sandbox"
    if os.sep + "pipx" + os.sep in exe:
        return "pipx"
    if source is not None and source.name() == "published-channel":
        return "published channel (release install)"
    return "venv / other"


def _project_count(control_home: Optional[Path] = None) -> str:
    """How MANY projects are in the roster — never which ones.

    The count separates "tide has never been pointed at anything" from "tide is in
    daily use and this broke", which is a real diagnostic split. The names are the
    user's business.
    """
    home = Path(control_home) if control_home else tide_home_dir()
    roster = home / "roster.md"
    try:
        lines = [
            ln for ln in roster.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        return str(len(lines))
    except OSError:
        return "0 (no roster)"


def _doctor_statuses(network: bool = False) -> List[tuple]:
    """Doctor check NAMES and STATUSES only — never the details.

    Doctor's ``detail`` strings quote roster paths and project names by design;
    they are the right thing on the user's own terminal and the wrong thing in a
    bundle leaving their machine. The status alone still tells the maintainer
    which check is unhappy.
    """
    try:
        from . import doctor as _doctor
        from . import paths as _paths

        report = _doctor.run_doctor(_paths.find_tide_root(), network=network)
        return [(r.name, r.status) for r in report.results]
    except Exception as exc:  # a diagnostic that crashes helps nobody
        return [("doctor", "could not run: {0}".format(type(exc).__name__))]


def read_log_tail(path: Path, lines: int = MAX_LOG_LINES) -> str:
    """The last *lines* of *path*, redacted. The user chose this file explicitly."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return redact("\n".join(text.splitlines()[-lines:]))


def collect(
    what_happened: str,
    *,
    log: Optional[Path] = None,
    network: bool = False,
) -> Report:
    """Build the report: the user's words + a FIXED set of diagnostic facts.

    The list of facts is closed on purpose. Adding "just one more thing that might
    help" is how a diagnostic bundle turns into an exfiltration channel; anything
    new belongs here, in the open, where :attr:`WITHHELD` and the pre-send preview
    can be checked against it.
    """
    try:
        source = resolve_source()
    except Exception:
        source = None

    marker = read_marker(default_marker_path()) or {}
    rep = Report(what_happened=what_happened)
    rep.facts = [
        ("tide version", __version__),
        ("install shape", install_shape(source)),
        ("update channel", source.name() if source is not None else "none resolvable"),
        ("install marker", marker.get("version", "none yet")),
        ("python", platform.python_version()),
        ("platform", "{0} {1} ({2})".format(platform.system(), platform.release(), platform.machine())),
        ("projects in roster", _project_count()),
        ("gh available", "yes" if shutil.which("gh") else "no"),
    ]
    rep.doctor = _doctor_statuses(network=network)
    if log is not None:
        try:
            rep.log_tail = read_log_tail(Path(log))
        except OSError as exc:
            rep.log_tail = "(could not read the log: {0})".format(exc)
    # Belt and braces: the user's own words and every collected fact go through
    # redaction too — people paste paths into their description all the time.
    rep.what_happened = redact(rep.what_happened)
    rep.facts = [(k, redact(str(v))) for k, v in rep.facts]
    return rep


# --- delivery ------------------------------------------------------------------


def gh_ready() -> bool:
    """True when ``gh`` is on PATH AND already authenticated (never prompts)."""
    if shutil.which("gh") is None:
        return False
    try:
        proc = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, timeout=15, check=False
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def send_via_gh(report: Report, *, repo: str = REPORT_REPO) -> tuple:
    """Open a GitHub issue with the report body; return ``(ok, detail)``."""
    try:
        proc = subprocess.run(
            ["gh", "issue", "create", "--repo", repo,
             "--title", report.title(), "--body-file", "-"],
            input=report.render(), capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "gh failed to run: {0}".format(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "gh issue create failed").strip()
    return True, (proc.stdout or "").strip()


def reports_dir() -> Path:
    """``$TIDE_HOME/reports`` — where an unsent report waits."""
    return tide_home_dir() / "reports"


def save_to_file(report: Report) -> Path:
    """Write the report next to the install and return its path."""
    d = reports_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / "report-{0}.md".format(time.strftime("%Y%m%d-%H%M%S"))
    path.write_text(report.render(), encoding="utf-8")
    return path


def copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard copy so the fallback is one paste, not a file hunt."""
    for argv in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(argv[0]) is None:
            continue
        try:
            subprocess.run(argv, input=text, text=True, timeout=10, check=False)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


# --- CLI ------------------------------------------------------------------------


def _cmd_report(args) -> int:
    what = " ".join(args.what or []).strip()
    if not what and not args.dry_run:
        print("tide report: say what happened, e.g.")
        print('  tide report "tide menu hangs after I pick a project"')
        return 2

    report = collect(
        what or "(not described)",
        log=Path(args.log).expanduser() if args.log else None,
        network=not args.no_network,
    )
    body = report.render()

    print("tide report — this is EXACTLY what would be sent:")
    print()
    for line in body.splitlines():
        print("  " + line)
    print()

    if args.dry_run:
        print("  dry run — nothing was sent or written.")
        return 0

    if args.file or not gh_ready():
        path = save_to_file(report)
        copied = copy_to_clipboard(body)
        print("  saved: {0}".format(path))
        if copied:
            print("  copied to your clipboard — paste it wherever you talk to the maintainer")
        if not args.file:
            print("  (no authenticated `gh` on this machine, so nothing was opened for you;")
            print("   `gh auth login` would let `tide report` file the issue itself)")
        return 0

    if not args.yes:
        try:
            answer = input("  send this as a GitHub issue on {0}? [y/N] ".format(args.repo)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            path = save_to_file(report)
            print("  not sent. Saved instead: {0}".format(path))
            return 0

    ok, detail = send_via_gh(report, repo=args.repo)
    if ok:
        print("  sent: {0}".format(detail))
        return 0
    path = save_to_file(report)
    print("  could not open the issue: {0}".format(detail))
    print("  saved instead: {0}".format(path))
    return 1


def register(subparsers) -> None:
    """Add the top-level ``report`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "report",
        help="send the maintainer a bug report: your words + version, install shape, environment",
    )
    p.add_argument("what", nargs="*", help="what you were doing and what broke")
    p.add_argument(
        "--log", default=None,
        help="a log/output file to attach (last {0} lines, redacted)".format(MAX_LOG_LINES),
    )
    p.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="print the report and stop — send nothing, write nothing",
    )
    p.add_argument(
        "--file", action="store_true",
        help="always write a file instead of opening an issue",
    )
    p.add_argument(
        "--repo", default=REPORT_REPO, help="repo the issue is opened on",
    )
    p.add_argument(
        "--no-network", action="store_true", dest="no_network",
        help="skip doctor's network probe while collecting",
    )
    p.add_argument("--yes", action="store_true", help="skip the send confirmation")
    p.set_defaults(func=_cmd_report, _cmd="report")
