"""tide.doctor — ``tide doctor``: an ON-DEMAND health/diagnostic report.

A single explicit command that answers "is this tide install healthy?" by running
a handful of independent checks and printing one ok/warn/fail line each:

* **python**          the interpreter is new enough to run tide (>= 3.12).
* **structure**       the project ``.tide/`` skeleton is intact (canon/arcs/state).
* **canon**           ``.tide/canon/CANON.md`` is present + readable (+ its sections).
* **hooks**           the Claude Code hooks are wired in ``.claude/settings.json``.
* **install-marker**  the self-update install marker is absent-or-valid.
* **channel**         the self-update source is configured (and, by default,
                      reachable — an offline-tolerant network probe).

DESIGN RAZOR — this is on-demand ONLY. It is NEVER wired into a hook, a daemon, or
any periodic checker (that would violate tide's no-autonomy razor; SessionStart
already surfaces drift/unmerged/readme/update). The one check that can touch the
network (``channel``) runs ONLY here, under an explicit ``tide doctor``, is bounded
by a short timeout, is offline-tolerant (a failure reports *unreachable*, never
crashes), and is skippable with ``--no-network``.

EXIT CODE (scriptable, mirrors the spirit of ``tide readme --check``): ``0`` when
no check FAILs (warns are advisory and do NOT trip it), nonzero when any check
fails — so ``tide doctor`` is a usable CI/health gate. Logic is argparse-free and
unit-testable; :func:`register` wires the thin CLI handler.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import paths
from .canon import store
from .hooks import install
from .update.source import (
    LocalSourceCheckout,
    PublishedChannelSource,
    VersionSource,
    default_marker_path,
    resolve_source,
)

# Sentinel: tell "caller omitted source → resolve it" apart from "caller passed
# source=None → there is genuinely no source" (the latter warns, no resolution).
_UNSET = object()

# --- result model ----------------------------------------------------------

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

# The minimum interpreter tide supports (pyproject ``requires-python = ">=3.12"``).
MIN_PYTHON: Tuple[int, int] = (3, 12)


@dataclass(frozen=True)
class CheckResult:
    """One diagnostic line: a *name*, a *status* (ok|warn|fail), and a human *detail*."""

    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    """The aggregate of every check, with the scriptable exit-code contract."""

    results: List[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no check FAILed (warns are advisory, not failures)."""
        return not any(r.status == STATUS_FAIL for r in self.results)

    @property
    def exit_code(self) -> int:
        """0 when healthy (no fail), 1 when at least one check failed."""
        return 0 if self.ok else 1


# --- individual checks -----------------------------------------------------


def check_python(version_info: Optional[Tuple[int, ...]] = None) -> CheckResult:
    """Pass when the running interpreter is >= :data:`MIN_PYTHON`, else fail.

    Pure: takes a *version_info* tuple (defaults to ``sys.version_info``) so both
    the supported and the too-old case are testable without a second interpreter.
    """
    vi = tuple((version_info or sys.version_info)[:3])
    shown = ".".join(str(p) for p in vi)
    need = ".".join(str(p) for p in MIN_PYTHON)
    if vi[:2] >= MIN_PYTHON:
        return CheckResult("python", STATUS_OK, "python {0} (>= {1})".format(shown, need))
    return CheckResult(
        "python", STATUS_FAIL, "python {0} is too old — tide needs >= {1}".format(shown, need)
    )


# The per-project dirs a healthy ``.tide/`` carries (canon resolves the legacy
# spelling too; arcs/candidates and state are the other load-bearing dirs).
def check_structure(root: Optional[Path]) -> CheckResult:
    """Pass when *root*'s ``.tide/`` skeleton is intact; fail on a missing dir.

    *root* is None when no ``.tide/`` was found in the cwd or any parent — that is
    itself a failure (we are not inside a tide project).
    """
    if root is None:
        return CheckResult(
            "structure", STATUS_FAIL,
            "no .tide/ found here or in any parent — run 'tide init'",
        )
    root = Path(root)
    expected = {
        ".tide": paths.tide_dir(root),
        "canon": paths.canon_dir(root),
        "arcs": paths.arcs_dir(root),
        "arcs/candidates": paths.candidates_dir(root),
        "state": paths.state_dir(root),
    }
    missing = [label for label, path in expected.items() if not path.is_dir()]
    if missing:
        return CheckResult(
            "structure", STATUS_FAIL,
            ".tide structure incomplete — missing: {0}".format(", ".join(sorted(missing))),
        )
    return CheckResult("structure", STATUS_OK, ".tide/ skeleton intact ({0})".format(root))


def check_canon(root: Optional[Path]) -> CheckResult:
    """Pass when CANON.md is present + readable; warn on missing sections; fail otherwise."""
    if root is None:
        return CheckResult("canon", STATUS_FAIL, "no project root — cannot read canon")
    try:
        text = store.read(Path(root))  # raises FileNotFoundError when absent
    except FileNotFoundError:
        return CheckResult(
            "canon", STATUS_FAIL, "CANON.md missing — run 'tide canon init'"
        )
    except (OSError, UnicodeDecodeError) as exc:
        return CheckResult("canon", STATUS_FAIL, "CANON.md unreadable: {0}".format(exc))

    sections = store.scan_text(text)
    missing = [s for s in store.SECTIONS if s not in sections]
    if missing:
        return CheckResult(
            "canon", STATUS_WARN,
            "CANON.md readable but missing sections: {0}".format(", ".join(missing)),
        )
    return CheckResult("canon", STATUS_OK, "CANON.md readable ({0} sections)".format(len(sections)))


def check_hooks(root: Optional[Path]) -> CheckResult:
    """Pass when both tide hooks are wired; warn when not installed; fail on bad JSON."""
    if root is None:
        return CheckResult("hooks", STATUS_WARN, "no project root — hooks not checked")
    settings = install.settings_path(Path(root))
    if not settings.is_file():
        return CheckResult(
            "hooks", STATUS_WARN,
            "Claude Code hooks not installed — run 'tide install-hooks'",
        )
    try:
        data = install._load(settings)
    except install.InstallError as exc:
        return CheckResult("hooks", STATUS_FAIL, str(exc))

    hooks = data.get(install.HOOKS_KEY, {})
    if not isinstance(hooks, dict):
        return CheckResult("hooks", STATUS_FAIL, "'hooks' key in settings.json is not an object")
    session_groups = hooks.get(install.SESSION_START_EVENT, []) or []
    pre_groups = hooks.get(install.PRE_TOOL_USE_EVENT, []) or []
    have_session = install._command_present(session_groups, install.SESSION_START_CMD)
    have_edit = install._command_present(pre_groups, install.EDIT_GATE_CMD)
    if have_session and have_edit:
        return CheckResult("hooks", STATUS_OK, "Claude Code hooks wired ({0})".format(settings))
    missing = []
    if not have_session:
        missing.append(install.SESSION_START_CMD)
    if not have_edit:
        missing.append(install.EDIT_GATE_CMD)
    return CheckResult(
        "hooks", STATUS_WARN,
        "hooks partially wired — missing: {0} (run 'tide install-hooks')".format(
            ", ".join(missing)
        ),
    )


def check_install_marker(marker_path: Optional[Path] = None) -> CheckResult:
    """Pass when the marker is valid; warn when absent (fresh install); fail when corrupt.

    An ABSENT marker is normal on a fresh install (the first ``tide self-update``
    lays it down) — a warn, not a failure. A PRESENT-but-unparseable / version-less
    marker is genuine corruption — a fail.
    """
    path = Path(marker_path) if marker_path is not None else default_marker_path()
    if not path.is_file():
        return CheckResult(
            "install-marker", STATUS_WARN,
            "no install marker yet ({0}) — written on first 'tide self-update'".format(path),
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return CheckResult("install-marker", STATUS_FAIL, "install marker unreadable: {0}".format(exc))
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        return CheckResult(
            "install-marker", STATUS_FAIL,
            "install marker malformed (no 'version'): {0}".format(path),
        )
    return CheckResult("install-marker", STATUS_OK, "install marker valid (version {0})".format(data["version"]))


def _probe_reachable(source: VersionSource) -> Tuple[bool, str]:
    """Best-effort reachability for *source* — offline-tolerant, never raises.

    A :class:`LocalSourceCheckout` is "reachable" when its checkout dir still
    exists (no network). A :class:`PublishedChannelSource` is probed via its own
    network-defensive ``_latest_tag`` (cache-first, short timeout, swallows every
    error → None); None reads as *unreachable or no releases*, a known tag as
    *reachable*. Reusing the source's existing probe means doctor adds NO new
    network code path.
    """
    if isinstance(source, LocalSourceCheckout):
        src_dir = Path(source.source_dir)
        if src_dir.is_dir():
            return True, "local checkout at {0}".format(src_dir)
        return False, "local checkout missing: {0}".format(src_dir)
    if isinstance(source, PublishedChannelSource):
        try:
            tag = source._latest_tag()
        except Exception:  # pragma: no cover — _latest_tag is already defensive
            tag = None
        if tag:
            return True, "latest release {0}".format(tag)
        return False, "no release tag resolvable"
    # Unknown source type: report configured, do not pretend to reach it.
    return True, "configured"


def check_channel(*, source=_UNSET, network: bool = True) -> CheckResult:
    """Report the self-update channel: configured (and, with *network*, reachable).

    When *source* is omitted it is resolved via
    :func:`tide.update.source.resolve_source`; an explicit ``source=None`` means
    there is genuinely no source (a warn). With *network* False (the
    ``--no-network`` path) we report only that a source is CONFIGURED — no probe,
    no network. With *network* True we additionally probe reachability
    (offline-tolerant): unreachable → warn, never fail/crash.
    """
    src = resolve_source() if source is _UNSET else source
    if src is None:
        return CheckResult(
            "channel", STATUS_WARN, "no self-update source resolvable (nothing to update against)"
        )
    name = src.name()
    if not network:
        return CheckResult(
            "channel", STATUS_OK, "self-update source: {0} (network probe skipped)".format(name)
        )
    reachable, detail = _probe_reachable(src)
    if reachable:
        return CheckResult("channel", STATUS_OK, "self-update channel reachable: {0} — {1}".format(name, detail))
    return CheckResult("channel", STATUS_WARN, "self-update channel unreachable: {0} — {1}".format(name, detail))


# --- aggregate -------------------------------------------------------------


def run_doctor(
    root: Optional[Path],
    *,
    marker_path: Optional[Path] = None,
    source=_UNSET,
    network: bool = True,
) -> DoctorReport:
    """Run every check over *root* and return the aggregate :class:`DoctorReport`.

    *source* / *marker_path* are injectable so the aggregate is testable without
    the real install state or the network. *source* follows
    :func:`check_channel`'s sentinel: omit it to resolve the real source, pass
    ``None`` for "no source". *network* gates only the channel probe.
    """
    results = [
        check_python(),
        check_structure(root),
        check_canon(root),
        check_hooks(root),
        check_install_marker(marker_path=marker_path),
        check_channel(source=source, network=network),
    ]
    return DoctorReport(results)


# --- CLI wiring ------------------------------------------------------------

_GLYPH = {STATUS_OK: "ok  ", STATUS_WARN: "warn", STATUS_FAIL: "FAIL"}


def _cmd_doctor(args) -> int:
    # find_tide_root (not require_) so doctor REPORTS "no .tide" as a failing check
    # instead of raising — a diagnostic must always produce a report.
    root = paths.find_tide_root()
    network = not getattr(args, "no_network", False)
    # source omitted → run_doctor resolves the real self-update source.
    report = run_doctor(root, network=network)

    print("tide doctor")
    for r in report.results:
        print("  [{0}] {1}: {2}".format(_GLYPH.get(r.status, r.status), r.name, r.detail))
    if report.ok:
        print("doctor: healthy")
    else:
        n = sum(1 for r in report.results if r.status == STATUS_FAIL)
        print("doctor: {0} check(s) FAILED".format(n))
    return report.exit_code


def register(subparsers) -> None:
    """Add the top-level ``doctor`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "doctor",
        help="on-demand health check (python/structure/canon/hooks/marker/channel)",
    )
    p.add_argument(
        "--no-network",
        action="store_true",
        dest="no_network",
        help="skip the self-update channel network probe (offline / hermetic)",
    )
    p.set_defaults(func=_cmd_doctor, _cmd="doctor")
