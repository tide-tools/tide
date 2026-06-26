"""tide.update.core — detect → REGRESSION GATE → (re)install → stamp.

The nightmare a self-update must prevent is *shipping a broken tide*. So the flow
is gate-FIRST: before any install we run the REGRESSION GATE against the SOURCE
we are about to install —

  1. ``tide verify --portable``  (tool ⊥ instance — reuses :mod:`tide.verify`)
  2. the test suite (``python -m pytest``)

— and only a FULLY GREEN gate lets the (re)install proceed. A red gate REFUSES
and reports; nothing is installed. After install we run a post-install smoke
(``tide version`` from the freshly installed code); if that fails we report loudly
(true wheel-level rollback needs versioned artifacts — the crit E seam).

The gate runs the checks as SUBPROCESSES against the source checkout (cwd=source,
``PYTHONPATH=<source>/src``) so it verifies the code about to be installed, not
the currently-loaded (possibly older) in-process package.

Fail-loud, never fail-silent (mirrors :mod:`tide.gate`): if the suite cannot run
at all (pytest not importable), that is a gate FAILURE, not a skip — an unverified
update is not an accepted update. ``--no-suite`` is the only way to drop to a
portable-only gate, and it says so.

:func:`session_note` is the SURFACE-don't-apply probe the SessionStart hook calls.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .source import Revision, VersionSource, resolve_source

# A runner abstraction so tests can drive the flow without real installs/suites.
# Returns (returncode, combined_output).
Runner = Callable[[List[str], Optional[Path], Optional[dict]], Tuple[int, str]]


def _default_runner(
    cmd: List[str], cwd: Optional[Path] = None, env: Optional[dict] = None
) -> Tuple[int, str]:
    """Run *cmd*, capturing combined stdout+stderr; return ``(rc, output)``."""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _source_env(source_dir: Path) -> dict:
    """Env with ``PYTHONPATH`` front-loaded with the source ``src/`` (gate the source)."""
    env = dict(os.environ)
    src = str(Path(source_dir) / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src + (os.pathsep + existing if existing else "")
    return env


# --- the regression gate ----------------------------------------------------


@dataclass
class GateResult:
    """Outcome of the regression gate; ``ok`` is the overall accept/refuse."""

    portable_ok: bool
    suite_ok: bool
    suite_ran: bool
    messages: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Green iff portable passed AND the suite ran green (unless suite was waived)."""
        return self.portable_ok and self.suite_ok


def run_regression_gate(
    source: VersionSource,
    *,
    run_suite: bool = True,
    runner: Runner = _default_runner,
) -> GateResult:
    """Run the portable check + test suite against *source*; return a :class:`GateResult`.

    Both checks run as subprocesses against the source checkout. When *run_suite*
    is False we drop to a portable-only gate (weaker — say so). If pytest cannot
    be invoked at all, that is recorded as a suite FAILURE (fail-loud).
    """
    source_dir = Path(getattr(source, "source_dir"))
    python_exe = getattr(source, "python_exe")
    env = _source_env(source_dir)
    result = GateResult(portable_ok=False, suite_ok=False, suite_ran=False)

    # 1) portable invariant (tool ⊥ instance)
    rc, out = runner(
        [python_exe, "-m", "tide", "verify", "--portable"], source_dir, env
    )
    result.portable_ok = rc == 0
    result.messages.append(
        "verify --portable: {0}".format("PASS" if result.portable_ok else "FAIL")
    )
    if not result.portable_ok:
        result.messages.append(_indent(out))

    # 2) the test suite
    if not run_suite:
        result.suite_ok = True  # explicitly waived → not counted against the gate
        result.suite_ran = False
        result.messages.append(
            "suite: SKIPPED (--no-suite — portable-only gate, weaker)"
        )
        return result

    rc, out = runner([python_exe, "-m", "pytest", "-q"], source_dir, env)
    result.suite_ran = True
    if _pytest_unavailable(rc, out):
        result.suite_ok = False
        result.messages.append(
            "suite: CANNOT RUN — pytest not importable by {0} (install 'tide[test]'); "
            "an unverified update is REFUSED, not accepted".format(python_exe)
        )
        result.messages.append(_indent(out))
        return result
    result.suite_ok = rc == 0
    result.messages.append("suite: {0}".format("PASS" if result.suite_ok else "FAIL"))
    if not result.suite_ok:
        result.messages.append(_indent(_tail(out)))
    return result


def _pytest_unavailable(rc: int, out: str) -> bool:
    """True when the failure is "pytest isn't installed", not "tests failed"."""
    return rc != 0 and ("No module named pytest" in out or "No module named 'pytest'" in out)


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + ln for ln in text.strip().splitlines())


def _tail(text: str, n: int = 20) -> str:
    lines = text.strip().splitlines()
    return "\n".join(lines[-n:])


# --- staleness status (used by --check + session-start) ---------------------


@dataclass(frozen=True)
class UpdateStatus:
    """A read-only snapshot: is the installed tide stale vs the source?"""

    source_name: str
    installed: Revision
    available: Revision
    stale: bool


def check_for_update(source: VersionSource) -> UpdateStatus:
    """Compute the :class:`UpdateStatus` for *source* (no gate, no install)."""
    installed = source.installed()
    available = source.available()
    return UpdateStatus(
        source_name=source.name(),
        installed=installed,
        available=available,
        stale=installed.identity != available.identity,
    )


# --- the self-update flow ---------------------------------------------------


@dataclass
class SelfUpdateResult:
    """Outcome of a :func:`self_update` run."""

    source_name: str
    installed: Revision
    available: Revision
    stale: bool
    accepted: bool
    applied: bool
    gate: Optional[GateResult] = None
    messages: List[str] = field(default_factory=list)


def self_update(
    source: VersionSource,
    *,
    force: bool = False,
    run_suite: bool = True,
    runner: Runner = _default_runner,
) -> SelfUpdateResult:
    """Detect staleness → gate → (re)install → stamp. The full supervised update.

    No-op when already current (unless *force*). On a stale (or forced) install
    the REGRESSION GATE runs first: only a green gate proceeds to ``pip install``
    + a post-install smoke + stamping the marker. A red gate refuses with the
    detail attached — nothing is installed.
    """
    status = check_for_update(source)
    res = SelfUpdateResult(
        source_name=status.source_name,
        installed=status.installed,
        available=status.available,
        stale=status.stale,
        accepted=False,
        applied=False,
    )

    if not status.stale and not force:
        res.accepted = True  # nothing to do = the desired state
        res.messages.append(
            "already current ({0}) — nothing to update".format(status.installed)
        )
        return res

    res.messages.append(
        "{0}: {1} → {2}".format(
            "forced reinstall" if (not status.stale and force) else "update available",
            status.installed,
            status.available,
        )
    )

    gate = run_regression_gate(source, run_suite=run_suite, runner=runner)
    res.gate = gate
    res.messages.append("regression gate: {0}".format("GREEN" if gate.ok else "RED"))
    res.messages.extend("  " + ln for ln in gate.messages)
    if not gate.ok:
        res.messages.append(
            "REFUSED — gate is red; nothing installed (a self-update must not ship a broken tide)"
        )
        return res

    # Gate green → install. `applied` flips the moment we invoke pip — the install
    # may mutate site-packages, so the caller must know it ran even if it errored.
    cmd = source.install_command()
    res.messages.append("installing: {0}".format(" ".join(cmd)))
    res.applied = True
    rc, out = runner(cmd, getattr(source, "source_dir", None), None)
    if rc != 0:
        res.messages.append("REFUSED — install command failed")
        res.messages.append(_indent(_tail(out)))
        return res

    # Post-install smoke: the freshly installed code must at least run.
    smoke_rc, smoke_out = runner(
        [getattr(source, "python_exe"), "-m", "tide", "version"], None, None
    )
    if smoke_rc != 0 or "tide" not in smoke_out:
        res.messages.append(
            "WARNING — install applied but post-install smoke FAILED "
            "(reinstall the previous source manually; versioned rollback needs the "
            "published channel, crit E)"
        )
        res.messages.append(_indent(_tail(smoke_out)))
        return res

    recorded = source.record_install() if hasattr(source, "record_install") else status.available
    res.accepted = True
    res.messages.append("accepted — installed + stamped at {0}".format(recorded))
    return res


# --- SessionStart surface (never auto-apply) --------------------------------


def session_note(resolver: Callable[[], Optional[VersionSource]] = resolve_source) -> Optional[str]:
    """One non-blocking line for SessionStart when an update is available, else None.

    SURFACE-don't-apply: per the supervised-canon principle, the hook only tells
    the head an update exists — it never installs. Bulletproof: any error (no
    source, no git, odd metadata) yields None so a SessionStart hook never breaks
    a session.
    """
    try:
        source = resolver()
        if source is None:
            return None
        status = check_for_update(source)
        if not status.stale:
            return None
        return (
            "  ↑ tide update available: {0} → {1} "
            "(supervised — run 'tide self-update' to gate + apply)".format(
                status.installed, status.available
            )
        )
    except Exception:
        return None
