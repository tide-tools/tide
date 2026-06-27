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
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .source import (
    LocalSourceCheckout,
    PublishedChannelSource,
    Revision,
    VersionSource,
    clear_broken,
    prefers_newer_only,
    read_broken,
    read_rollback,
    resolve_source,
    revision_is_stale,
    write_broken,
    write_rollback,
)

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
    source_dir = getattr(source, "source_dir", None)
    if source_dir is None:
        # No in-place checkout to gate (a raw published source). The gate cannot
        # run against it — refuse RED (never crash, never silently pass). The
        # supported path materializes the artifact into a checkout first (see
        # self_update_published).
        return GateResult(
            portable_ok=False,
            suite_ok=False,
            suite_ran=False,
            messages=[
                "cannot gate {0}: no local source checkout — a published source "
                "must be materialized first (see self_update_published)".format(
                    source.name()
                )
            ],
        )
    source_dir = Path(source_dir)
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
        stale=revision_is_stale(
            installed, available, newer_only=prefers_newer_only(source)
        ),
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

    return _gate_then_install(
        source, status, res, gate_source=source, run_suite=run_suite, runner=runner
    )


def self_update_published(
    source: PublishedChannelSource,
    *,
    force: bool = False,
    run_suite: bool = True,
    runner: Runner = _default_runner,
    workdir_factory: Callable[[], str] = tempfile.mkdtemp,
) -> SelfUpdateResult:
    """Self-update for a PUBLISHED install (brew / pip-from-git) — gate the artifact.

    A published channel has no in-place checkout to run the suite against. The
    faithful gated path: DOWNLOAD + extract the release tarball, run the EXISTING
    :func:`run_regression_gate` against the extracted source, and only on GREEN
    apply the channel install (``brew upgrade`` / ``pip install git+…``) + smoke +
    stamp. A failed fetch or red gate REFUSES — nothing is installed. The temp
    checkout is always cleaned up.
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
        res.accepted = True
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

    workdir = Path(workdir_factory())
    try:
        try:
            gate_root = source.materialize_source(workdir)
        except Exception as exc:
            res.messages.append(
                "REFUSED — could not fetch/extract the release artifact to gate it: "
                "{0}".format(exc)
            )
            return res
        res.messages.append("fetched release artifact → {0}".format(gate_root))
        # A throwaway checkout over the extracted source: the gate runs against
        # THIS (it has pyproject + tests), but the INSTALL + stamp stay on the
        # published source (its channel command, its version-keyed marker).
        gate_source = LocalSourceCheckout(
            source_dir=gate_root,
            python_exe=source.python_exe,
            editable=False,
            marker_path=workdir / "gate-marker.json",
        )
        return _gate_then_install(
            source, status, res, gate_source=gate_source, run_suite=run_suite, runner=runner
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _gate_then_install(
    source: VersionSource,
    status: UpdateStatus,
    res: SelfUpdateResult,
    *,
    gate_source: VersionSource,
    run_suite: bool,
    runner: Runner,
) -> SelfUpdateResult:
    """Shared tail: run the gate against *gate_source* → install *source* → smoke → stamp.

    The gate target and the install target differ for a published update (gate the
    extracted tarball; install via the channel) but are the same for a local one —
    so both flows funnel through here, keeping the nightmare-guard (no red gate
    ever ships) in ONE place.
    """
    gate = run_regression_gate(gate_source, run_suite=run_suite, runner=runner)
    res.gate = gate
    res.messages.append("regression gate: {0}".format("GREEN" if gate.ok else "RED"))
    res.messages.extend("  " + ln for ln in gate.messages)
    if not gate.ok:
        res.messages.append(
            "REFUSED — gate is red; nothing installed (a self-update must not ship a broken tide)"
        )
        return res

    # Gate green → record the rollback point (best-effort), THEN install. `applied`
    # flips the moment we invoke the installer — it may mutate site-packages, so the
    # caller must know it ran even if it errored.
    _record_rollback(source, res)
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
        # The install PHYSICALLY happened (pip succeeded) — the new version IS on
        # disk, it just fails to smoke. STAMP the marker anyway so installed() reads
        # the new version: otherwise the marker stays at the OLD version and every
        # session re-nudges "update available" to a version already installed (a
        # perpetual loop). Stamping only silences the re-nudge for THIS version; a
        # genuinely newer release still nudges. The loud WARNING + rollback guidance
        # keep the failure visible — accepted stays False (not a clean success).
        if hasattr(source, "record_install"):
            source.record_install()
        # Keep the failure LOUD beyond this one message: a separate broken-install
        # marker makes session_note warn EVERY subsequent session (the stamped
        # version marker no longer reads stale, so the nudge alone would go silent).
        broken = _broken_path_for(source)
        if broken is not None:
            try:
                write_broken(broken, status.available.version, "post-install smoke check failed")
            except Exception:
                pass  # surfacing the failure must never itself block the flow
        res.messages.append(
            "WARNING — install applied but post-install smoke FAILED "
            "(roll back with 'tide self-update --rollback')"
        )
        res.messages.append(_indent(_tail(smoke_out)))
        return res

    recorded = source.record_install() if hasattr(source, "record_install") else status.available
    # A clean install recovered health → clear any prior broken-install marker.
    broken = _broken_path_for(source)
    if broken is not None:
        clear_broken(broken)
    res.accepted = True
    res.messages.append("accepted — installed + stamped at {0}".format(recorded))
    return res


def _broken_path_for(source: VersionSource) -> Optional[Path]:
    """The broken-install marker path for *source* (sibling of its install marker).

    Derived from the source's ``marker_path`` so it lands in the same ``$TIDE_HOME``
    that :func:`tide.update.source.default_broken_path` (used by session_note)
    resolves to. None when the source carries no marker_path (a bare test fake)."""
    marker = getattr(source, "marker_path", None)
    if marker is None:
        return None
    return Path(marker).parent / "broken-install-marker.json"


def _record_rollback(source: VersionSource, res: SelfUpdateResult) -> None:
    """Record how to reinstall the CURRENTLY-installed version (best-effort, never blocks).

    Only sources that expose a ``rollback_path`` + ``rollback_command()`` (the
    published channel — a pinned pip-from-git@<tag>) get a rollback marker; a local
    editable checkout has no past artifact to pin, so it is skipped silently.
    """
    path = getattr(source, "rollback_path", None)
    cmd_fn = getattr(source, "rollback_command", None)
    if path is None or cmd_fn is None:
        return
    try:
        write_rollback(Path(path), source.installed().version, cmd_fn())
        res.messages.append("rollback point recorded ({0})".format(source.installed()))
    except Exception:
        pass  # recording a rollback must never block the update itself


# --- rollback (reinstall the previously-pinned version) ---------------------


@dataclass
class RollbackResult:
    """Outcome of a :func:`rollback` run; ``ok`` is the overall success."""

    ok: bool
    target: Optional[str] = None
    messages: List[str] = field(default_factory=list)


def rollback(marker_path: Path, *, runner: Runner = _default_runner) -> RollbackResult:
    """Reinstall the previous version recorded by the rollback marker → smoke-check.

    Replays the pinned reinstall command captured BEFORE the last update (a
    version-pinned ``pip install git+…@<tag>``). No marker / no command → a clean
    refusal (nothing to roll back to). Reuses the same post-install smoke as the
    forward path.
    """
    data = read_rollback(marker_path)
    if not data or not data.get("command"):
        return RollbackResult(False, messages=["no rollback marker — nothing to roll back to"])

    cmd = list(data["command"])
    target = data.get("version")
    res = RollbackResult(False, target=target)
    res.messages.append("rolling back to {0}: {1}".format(target or "?", " ".join(cmd)))

    rc, out = runner(cmd, None, None)
    if rc != 0:
        res.messages.append("FAILED — rollback install errored")
        res.messages.append(_indent(_tail(out)))
        return res

    python_exe = cmd[0] if cmd else "python"
    smoke_rc, smoke_out = runner([python_exe, "-m", "tide", "version"], None, None)
    if smoke_rc != 0 or "tide" not in smoke_out:
        res.messages.append("FAILED — rolled-back install does not run (post-install smoke)")
        res.messages.append(_indent(_tail(smoke_out)))
        return res

    res.ok = True
    # A working rollback recovered health → clear the broken-install marker (sibling
    # of the rollback marker, same $TIDE_HOME) so session_note stops warning.
    clear_broken(Path(marker_path).parent / "broken-install-marker.json")
    res.messages.append("rolled back — reinstalled {0}".format(target or "previous version"))
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
        # A broken-install marker takes precedence and PERSISTS: a smoke-failed
        # install must keep nagging every session (the stale nudge alone goes
        # silent once the version marker is stamped) until rollback/reinstall.
        broken = _broken_path_for(source)
        if broken is not None:
            data = read_broken(broken)
            if data:
                return (
                    "  ✗ tide install is BROKEN: version {0} failed its post-install "
                    "smoke check — run 'tide self-update --rollback'".format(
                        data.get("version", "?")
                    )
                )
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
