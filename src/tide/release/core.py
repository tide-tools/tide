"""tide.release.core — preflight → gate → build → publish → tap, as one plan.

The shape is deliberate: every mutating action is a :class:`Step` carrying the
exact argv it would run. So ``--dry-run`` is not a parallel implementation that
can drift from the real one — it is the SAME plan, printed instead of executed.
Whatever the dry run shows is, literally, what the real run will call.

Order matters and is not negotiable:

1. **preflight** — read-only refusals. A dirty tree, the wrong branch, a tag that
   already exists, a missing/unauthed ``gh``: each is a NO before anything moves.
2. **portability scan** — the artifact is scanned for the RELEASING MACHINE's own
   identity (home path, username). We are handing this to other people; it must
   not carry the author's home address. This is the release-time twin of
   ``tide verify --portable``, widened from ``src/tide`` to every text file in
   the tarball.
3. **regression gate** — reused verbatim from :mod:`tide.update.core`: the same
   ``verify --portable`` + suite that refuses to self-update onto a broken tide
   refuses to publish one.
4. **build** — ``git archive`` the ref into ``dist/tide-<version>.tar.gz``.
5. **publish** — tag, push the tag, ``gh release create`` with the artifact. THIS
   is the release. The main install door is ``git clone`` + ``./install.sh``, so a
   pushed tag is already everything a person needs to install or update.
6. **tap** — SECONDARY. Rewrite the formula's url + sha256 + smoke version, commit,
   push. Homebrew is an update channel for the binary, not the front door, so a
   missing or skipped tap (``--no-tap``) never blocks a release.

The sha256 the formula pins is computed from the artifact BUILT HERE, so it can
never be the "I pasted the wrong digest" failure the manual ritual invited.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .. import verify
from ..update.core import GateResult, run_regression_gate
from ..update.source import LocalSourceCheckout, read_pyproject_version

# Same runner shape as tide.update.core: (argv, cwd, env) -> (rc, combined output).
Runner = Callable[[List[str], Optional[Path], Optional[dict]], Tuple[int, str]]

DEFAULT_BRANCH = "main"
TAP_FORMULA_RELPATH = Path("Formula") / "tide.rb"
TEMPLATE_RELPATH = Path("packaging") / "tide.rb"


def _default_runner(
    cmd: List[str], cwd: Optional[Path] = None, env: Optional[dict] = None
) -> Tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# --- the pieces of a plan ----------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One read-only preflight verdict. ``ok`` False refuses the release."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Step:
    """One MUTATING action: the argv, where it runs, and what it is for.

    A step is data, never a closure — that is what lets the dry run print the
    real thing rather than a description of it. ``writes`` names a file the step
    rewrites in place (the tap formula), which has no argv of its own.
    """

    name: str
    detail: str
    argv: Optional[List[str]] = None
    cwd: Optional[Path] = None
    writes: Optional[Path] = None
    content: Optional[str] = None

    def render(self) -> str:
        if self.argv:
            where = " (in {0})".format(self.cwd) if self.cwd else ""
            return "{0}{1}".format(" ".join(self.argv), where)
        if self.writes:
            return "write {0}".format(self.writes)
        return self.detail


@dataclass
class ReleasePlan:
    """A fully-resolved release: what was checked, what was built, what would run."""

    version: str
    repo: Path
    ref: str
    github_repo: str
    artifact: Optional[Path] = None
    sha256: Optional[str] = None
    asset_url: str = ""
    tap_dir: Optional[Path] = None
    checks: List[Check] = field(default_factory=list)
    gate: Optional[GateResult] = None
    steps: List[Step] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Green iff every preflight check passed AND the gate (if run) is green."""
        if any(not c.ok for c in self.checks):
            return False
        return self.gate is None or self.gate.ok

    @property
    def blockers(self) -> List[Check]:
        return [c for c in self.checks if not c.ok]


# --- version ----------------------------------------------------------------

_VERSION_LINE_RE = re.compile(r'^(\s*version\s*=\s*)(["\'])([^"\']+)\2', re.MULTILINE)


def current_version(repo: Path) -> Optional[str]:
    """The version declared in *repo*'s pyproject (the single source of truth)."""
    return read_pyproject_version(Path(repo))


def set_pyproject_version(repo: Path, version: str) -> str:
    """Rewrite the FIRST ``version = "…"`` in pyproject.toml to *version*.

    The first such line is ``[project].version``: ``[build-system]`` above it
    declares ``requires``/``build-backend``, never a bare ``version``. Returns the
    previous value. Raises when no version line exists — a release must never
    guess where the version lives.
    """
    path = Path(repo) / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    m = _VERSION_LINE_RE.search(text)
    if not m:
        raise RuntimeError("no `version = \"…\"` line in {0}".format(path))
    previous = m.group(3)
    start, end = m.span()
    path.write_text(
        text[:start] + "{0}{1}{2}{1}".format(m.group(1), m.group(2), version) + text[end:],
        encoding="utf-8",
    )
    return previous


# --- preflight ---------------------------------------------------------------


def _git(repo: Path, *args: str, runner: Runner = _default_runner) -> Tuple[int, str]:
    return runner(["git", "-C", str(repo), *args], None, None)


def preflight(
    repo: Path,
    version: str,
    *,
    branch: str = DEFAULT_BRANCH,
    allow_dirty: bool = False,
    runner: Runner = _default_runner,
) -> List[Check]:
    """Read-only refusals, all of them, before anything moves.

    Every check runs even after one fails: a release blocked by three things
    should say all three, not make the human re-run to discover the next one.
    """
    repo = Path(repo)
    checks: List[Check] = []

    rc, out = _git(repo, "rev-parse", "--git-dir", runner=runner)
    if rc != 0:
        return [Check("git-repo", False, "{0} is not a git checkout".format(repo))]
    checks.append(Check("git-repo", True, str(repo)))

    rc, out = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", runner=runner)
    head = out.strip()
    checks.append(
        Check(
            "branch",
            head == branch,
            "on {0}".format(head) if head == branch else "on {0}, expected {1}".format(head, branch),
        )
    )

    rc, out = _git(repo, "status", "--porcelain", runner=runner)
    dirty = [ln for ln in out.splitlines() if ln.strip()]
    if dirty and allow_dirty:
        checks.append(
            Check("clean-tree", True, "{0} uncommitted path(s) — allowed by --allow-dirty; "
                  "the artifact is built from the COMMIT, not the worktree".format(len(dirty)))
        )
    else:
        checks.append(
            Check(
                "clean-tree",
                not dirty,
                "clean" if not dirty else
                "{0} uncommitted path(s) — commit them or pass --allow-dirty "
                "(the artifact is built from the commit, so uncommitted work would "
                "silently NOT ship)".format(len(dirty)),
            )
        )

    tag = "v" + version
    rc, out = _git(repo, "tag", "--list", tag, runner=runner)
    local_tag = bool(out.strip())
    rc, out = _git(repo, "ls-remote", "--tags", "origin", "refs/tags/" + tag, runner=runner)
    remote_tag = bool(out.strip())
    if local_tag or remote_tag:
        where = " and ".join(
            [w for w, hit in (("locally", local_tag), ("on origin", remote_tag)) if hit]
        )
        checks.append(
            Check("tag-free", False, "{0} already exists {1} — bump the version".format(tag, where))
        )
    else:
        checks.append(Check("tag-free", True, "{0} is free".format(tag)))

    if shutil.which("gh") is None:
        checks.append(Check("gh", False, "the GitHub CLI `gh` is not on PATH — brew install gh"))
    else:
        rc, out = runner(["gh", "auth", "status"], None, None)
        checks.append(
            Check("gh", rc == 0, "authenticated" if rc == 0 else "gh is not authenticated: gh auth login")
        )

    return checks


# --- the artifact ------------------------------------------------------------


def artifact_name(version: str) -> str:
    """``tide-<version>.tar.gz`` — the name the formula's url ends in."""
    return "tide-{0}.tar.gz".format(version)


def build_artifact(
    repo: Path, version: str, dest_dir: Path, *, ref: str = "HEAD", runner: Runner = _default_runner
) -> Path:
    """``git archive`` *ref* into *dest_dir*/tide-<version>.tar.gz; return the path.

    Deliberately NOT ``python -m build``: that needs a build frontend installed on
    the shipping machine (tide has zero runtime deps and we will not grow a release
    one), and its output embeds build-time metadata. ``git archive`` is a pure
    function of the commit — the same ref always yields the same bytes, so the
    sha256 the formula pins is reproducible by anyone.

    The ``--prefix`` gives the tarball the single top-level ``tide-<version>/``
    directory pip and Homebrew both expect of a source distribution.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / artifact_name(version)
    rc, out = runner(
        [
            "git", "-C", str(repo), "archive",
            "--format=tar.gz",
            "--prefix=tide-{0}/".format(version),
            "--output=" + str(out_path),
            ref,
        ],
        None,
        None,
    )
    if rc != 0:
        raise RuntimeError("git archive failed: {0}".format(out.strip()))
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError("git archive produced no artifact at {0}".format(out_path))
    return out_path


def sha256_of(path: Path) -> str:
    """The hex digest Homebrew will check the download against."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_artifact_for_instance_tokens(
    tarball: Path, tokens: Optional[List[str]] = None
) -> List[verify.PortableLeak]:
    """Scan every text member of *tarball* for the releasing machine's identity.

    ``tide verify --portable`` guards ``src/tide``; a release ships far more —
    docs, prompts, rules, skills, tests, the installer. Those are the files that
    quietly carry ``/Users/<someone>`` into someone else's machine. We scan for
    INSTANCE TOKENS only (this machine's home path + username), not for every
    absolute home path: test fixtures legitimately contain invented ones (a made-up
    home dir for a made-up user), and failing on those would train people to
    ignore the check.
    """
    tokens = verify.default_instance_tokens() if tokens is None else tokens
    leaks: List[verify.PortableLeak] = []
    with tarfile.open(tarball, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile() or member.size > 2 * 1024 * 1024:
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            try:
                text = fh.read().decode("utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary / unreadable member: nothing textual to leak
            leaks.extend(
                lk
                for lk in verify.scan_text(text, member.name, tokens)
                if lk.kind == "instance-token"
            )
    return leaks


# --- the tap formula ---------------------------------------------------------

_URL_RE = re.compile(r'^(\s*url\s+)"[^"]*"', re.MULTILINE)
_SHA_RE = re.compile(r'^(\s*sha256\s+)"[^"]*"(.*)$', re.MULTILINE)
_ASSERT_RE = re.compile(r'(assert_match\s+")tide [^"]*(")')


def asset_url(github_repo: str, version: str) -> str:
    """The immutable release-asset URL the formula pins."""
    return "https://github.com/{0}/releases/download/v{1}/{2}".format(
        github_repo, version, artifact_name(version)
    )


def render_formula(template: str, *, github_repo: str, version: str, sha256: str) -> str:
    """Point *template*'s url, sha256 and smoke assertion at this release.

    Rewrites in place rather than generating from scratch: the formula carries
    hand-written prose and a ``depends_on`` line that are the packager's, not
    ours, and regenerating would quietly discard them. Raises when a field does
    not match — a silently un-rewritten sha256 is exactly the failure mode that
    hands every user a formula that refuses to install.
    """
    out, n_url = _URL_RE.subn(lambda m: '{0}"{1}"'.format(m.group(1), asset_url(github_repo, version)), template, count=1)
    if n_url != 1:
        raise RuntimeError("formula has no `url \"…\"` line to rewrite")
    out, n_sha = _SHA_RE.subn(lambda m: '{0}"{1}"'.format(m.group(1), sha256), out, count=1)
    if n_sha != 1:
        raise RuntimeError("formula has no `sha256 \"…\"` line to rewrite")
    out, n_assert = _ASSERT_RE.subn(
        lambda m: "{0}tide {1}{2}".format(m.group(1), version, m.group(2)), out
    )
    if n_assert < 1:
        raise RuntimeError("formula has no `assert_match \"tide …\"` smoke to rewrite")
    return out


def tap_url(tap: str) -> str:
    """The tap's GitHub URL (``owner/name`` → ``…/owner/homebrew-name``)."""
    owner, _, name = tap.partition("/")
    return "https://github.com/{0}/homebrew-{1}".format(owner, name)


def brew_tap_dir(tap: str = "tide-tools/tide") -> Tuple[Optional[Path], bool]:
    """Where Homebrew keeps *tap*, and whether it is already cloned there.

    Homebrew clones a tap to ``<repo>/Library/Taps/<owner>/homebrew-<name>``; that
    clone is a normal git checkout with a real remote, so committing and pushing
    from it is the honest way to update the formula. When brew knows the path but
    has not tapped yet we still return the path with ``False`` — a missing tap is
    one ``brew tap`` away, so it becomes a STEP rather than a dead end. ``(None,
    False)`` only when brew itself is absent.
    """
    if shutil.which("brew") is None:
        return None, False
    rc, out = _default_runner(["brew", "--repository"], None, None)
    if rc != 0:
        return None, False
    owner, _, name = tap.partition("/")
    path = Path(out.strip()) / "Library" / "Taps" / owner / ("homebrew-" + name)
    return path, (path / ".git").exists()


# --- the plan ----------------------------------------------------------------


def plan_release(
    repo: Path,
    *,
    version: Optional[str] = None,
    ref: str = "HEAD",
    branch: str = DEFAULT_BRANCH,
    github_repo: str = "tide-tools/tide",
    tap: str = "tide-tools/tide",
    tap_dir: Optional[Path] = None,
    update_tap: bool = True,
    dist_dir: Optional[Path] = None,
    allow_dirty: bool = False,
    run_gate: bool = True,
    run_suite: bool = True,
    runner: Runner = _default_runner,
) -> ReleasePlan:
    """Resolve a full release: run every read-only check, build the artifact, list the steps.

    Everything here is safe to run at any time — it checks, gates and builds a
    local tarball, and NOTHING it does touches git history, GitHub or the tap.
    Those live in :attr:`ReleasePlan.steps`, which only :func:`apply_release` runs.
    That split is the whole reason ``--dry-run`` can be trusted.
    """
    repo = Path(repo).resolve()
    version = version or current_version(repo) or "0.0.0"
    dist_dir = Path(dist_dir) if dist_dir else repo / "dist"

    plan = ReleasePlan(version=version, repo=repo, ref=ref, github_repo=github_repo)
    plan.asset_url = asset_url(github_repo, version)
    plan.checks = preflight(
        repo, version, branch=branch, allow_dirty=allow_dirty, runner=runner
    )

    declared = current_version(repo)
    if declared != version:
        plan.notes.append(
            "pyproject declares {0}; this release cuts {1} — the version line will be "
            "rewritten and committed before tagging".format(declared, version)
        )

    # Build the artifact even in a dry run: the sha256 the formula pins comes from
    # THESE bytes, so showing it is what makes the dry run a proof rather than a
    # promise. Only ever writes into dist/, which is gitignored.
    try:
        plan.artifact = build_artifact(repo, version, dist_dir, ref=ref, runner=runner)
        plan.sha256 = sha256_of(plan.artifact)
        plan.checks.append(
            Check(
                "artifact",
                True,
                "{0} ({1} bytes)".format(plan.artifact.name, plan.artifact.stat().st_size),
            )
        )
        leaks = scan_artifact_for_instance_tokens(plan.artifact)
        plan.checks.append(
            Check(
                "portable-artifact",
                not leaks,
                "no instance tokens in the tarball" if not leaks else
                "{0} leak(s) of this machine's identity, first: {1}:{2} ({3})".format(
                    len(leaks), leaks[0].source, leaks[0].line, leaks[0].detail
                ),
            )
        )
    except Exception as exc:
        plan.checks.append(Check("artifact", False, "could not build: {0}".format(exc)))

    if run_gate:
        gate_source = LocalSourceCheckout(
            source_dir=repo,
            python_exe=_gate_interpreter(),
            editable=False,
            marker_path=dist_dir / "release-gate-marker.json",
        )
        plan.gate = run_regression_gate(gate_source, run_suite=run_suite, runner=runner)
    else:
        plan.notes.append("regression gate SKIPPED (--no-gate) — weaker; say so out loud")

    tap_present = True
    if not update_tap:
        plan.tap_dir = None
        plan.notes.append(
            "brew formula NOT updated (--no-tap). The tag and the GitHub release are "
            "the release; Homebrew is a secondary channel and can be bumped later."
        )
    elif tap_dir:
        plan.tap_dir = Path(tap_dir)
        tap_present = (plan.tap_dir / ".git").exists()
    else:
        plan.tap_dir, tap_present = brew_tap_dir(tap)
    plan.steps = _release_steps(
        plan, declared=declared, tap=tap, tap_present=tap_present, update_tap=update_tap
    )
    return plan


def _gate_interpreter() -> str:
    """The interpreter to gate with (the process's own — it has the tide it ships)."""
    import sys

    return sys.executable


def _release_steps(
    plan: ReleasePlan, *, declared: Optional[str], tap: str,
    tap_present: bool = True, update_tap: bool = True,
) -> List[Step]:
    """The mutating half of the plan, in the only order that is safe."""
    version = plan.version
    tag = "v" + version
    repo = plan.repo
    steps: List[Step] = []

    if declared != version:
        steps.append(
            Step(
                "bump-version",
                "set pyproject version to {0}".format(version),
                writes=repo / "pyproject.toml",
            )
        )
        steps.append(
            Step(
                "commit-version",
                "commit the version bump",
                argv=["git", "commit", "-m", "release: tide {0}".format(version), "pyproject.toml"],
                cwd=repo,
            )
        )

    steps.append(
        Step(
            "tag",
            "annotate the release commit",
            argv=["git", "tag", "-a", tag, "-m", "tide {0}".format(version)],
            cwd=repo,
        )
    )
    steps.append(
        Step("push-tag", "publish the tag", argv=["git", "push", "origin", tag], cwd=repo)
    )
    steps.append(
        Step(
            "rebuild-artifact",
            "rebuild the tarball from the TAG (identical bytes; the tag is now the ref)",
            argv=[
                "git", "-C", str(repo), "archive", "--format=tar.gz",
                "--prefix=tide-{0}/".format(version),
                "--output=" + str(plan.artifact or (repo / "dist" / artifact_name(version))),
                tag,
            ],
        )
    )
    steps.append(
        Step(
            "gh-release",
            "create the GitHub release and attach the artifact",
            argv=[
                "gh", "release", "create", tag,
                str(plan.artifact or (repo / "dist" / artifact_name(version))),
                "--repo", plan.github_repo,
                "--title", "tide {0}".format(version),
                "--generate-notes",
            ],
        )
    )

    if not update_tap:
        return steps

    if plan.tap_dir is None:
        steps.append(
            Step(
                "tap",
                "no tap checkout here, so the brew formula stays at its old version "
                "— a SECONDARY channel, not a blocker. The tag above is the release. "
                "To bump brew later: clone {0} and re-run with --tap-dir".format(
                    tap_url(tap)
                ),
            )
        )
        return steps

    if not tap_present:
        # A missing tap is one command away, so it is a step, not a refusal —
        # brew clones it to exactly the path we already resolved.
        steps.append(
            Step(
                "tap-clone",
                "clone the tap (brew has not tapped it on this machine yet)",
                argv=["brew", "tap", tap, tap_url(tap)],
            )
        )

    formula = plan.tap_dir / TAP_FORMULA_RELPATH
    steps.append(
        Step(
            "formula",
            "point the formula at {0} (sha256 {1})".format(
                plan.asset_url, (plan.sha256 or "?")[:12] + "…"
            ),
            writes=formula,
            content=plan.sha256,
        )
    )
    steps.append(
        Step(
            "commit-formula",
            "commit the formula bump",
            argv=[
                "git", "commit", "-m", "tide {0}: pin the v{0} release asset".format(version),
                str(TAP_FORMULA_RELPATH),
            ],
            cwd=plan.tap_dir,
        )
    )
    steps.append(
        Step("push-formula", "publish the formula", argv=["git", "push"], cwd=plan.tap_dir)
    )
    return steps


# --- applying ----------------------------------------------------------------


@dataclass
class ApplyResult:
    """Outcome of running a plan's steps; ``ok`` False means it stopped part-way."""

    ok: bool = True
    done: List[str] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)


def apply_release(plan: ReleasePlan, *, runner: Runner = _default_runner) -> ApplyResult:
    """Run the plan's mutating steps in order, stopping at the first failure.

    Refuses outright unless the plan is green: a release is the one place where
    "push on regardless" costs other people's machines. Stopping mid-way is
    deliberate and reported — a half-applied release (tag pushed, formula not) is
    recoverable by re-running once the cause is fixed; blindly continuing past a
    failed tag push is not.
    """
    res = ApplyResult()
    if not plan.ok:
        res.ok = False
        res.messages.append("REFUSED — the plan is not green; nothing was applied")
        return res

    for step in plan.steps:
        if step.writes is not None and step.name == "bump-version":
            set_pyproject_version(plan.repo, plan.version)
            res.done.append(step.name)
            continue
        if step.writes is not None and step.name == "formula":
            try:
                _write_formula(plan, step.writes)
            except Exception as exc:
                res.ok = False
                res.messages.append("{0}: FAILED — {1}".format(step.name, exc))
                return res
            res.done.append(step.name)
            continue
        if step.argv is None:
            res.messages.append("{0}: {1}".format(step.name, step.detail))
            continue
        rc, out = runner(step.argv, step.cwd, None)
        if rc != 0:
            res.ok = False
            res.messages.append(
                "{0}: FAILED — {1}\n{2}".format(step.name, " ".join(step.argv), out.strip())
            )
            return res
        res.done.append(step.name)
    res.messages.append("released tide {0}".format(plan.version))
    return res


def _write_formula(plan: ReleasePlan, formula_path: Path) -> None:
    """Render the tap formula from the tap's own copy, falling back to the template."""
    formula_path = Path(formula_path)
    if formula_path.is_file():
        template = formula_path.read_text(encoding="utf-8")
    else:
        template = (plan.repo / TEMPLATE_RELPATH).read_text(encoding="utf-8")
        formula_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_formula(
        template,
        github_repo=plan.github_repo,
        version=plan.version,
        sha256=plan.sha256 or "",
    )
    formula_path.write_text(rendered, encoding="utf-8")


def formula_preview(plan: ReleasePlan) -> Optional[str]:
    """The formula as it WOULD be written — the dry run's most load-bearing output."""
    if plan.sha256 is None:
        return None
    source: Optional[Path] = None
    if plan.tap_dir is not None and (plan.tap_dir / TAP_FORMULA_RELPATH).is_file():
        source = plan.tap_dir / TAP_FORMULA_RELPATH
    elif (plan.repo / TEMPLATE_RELPATH).is_file():
        source = plan.repo / TEMPLATE_RELPATH
    if source is None:
        return None
    try:
        return render_formula(
            source.read_text(encoding="utf-8"),
            github_repo=plan.github_repo,
            version=plan.version,
            sha256=plan.sha256,
        )
    except Exception:
        return None
