"""tide.gate — M1 cannon-gate: the single two-axis tri-state oracle.

``decide(root) -> (code, reasons)`` is THE definition of "canon is current."

Tri-state exit codes (POSIX-safe, compose verbatim in shells, hooks, CI):
    0 = current       — all checks pass; no action needed
    1 = stale         — one or more freshness / health checks failed
    2 = oracle-error  — the oracle could not evaluate (CANON.md missing /
                        unreadable, project structure broken, unexpected IO
                        error). **FAIL-LOUD**: callers MUST treat 2 as an
                        alert, never as a skip-or-pass. A silently-dead oracle
                        cannot disable enforcement by returning 0.

Checks (M1 clauses a/b/c; M3 clauses d/e/f are a later build unit):

    (a) No unmerged deltas — any arc (active or closed) still owes a
        ``tide cannon merge`` before work can continue.

    (b) No open arc drifted on cannon-rev OR reality-rev:
        * cannon-rev drift: arc's stamped rev ≠ sha256(CANON.md) now.
        * reality-rev drift: arc's stamped rev ≠ current content hash over
          canon-covered paths (only when a ``canon-covers:`` manifest exists).
          This is the M2 tripwire: "code shipped, canon didn't."

    (c) cannon lint — structural health of CANON.md:
        * no ``<…>`` template placeholders
        * no duplicate ``## `` headings
        * no duplicate ``### date · slug`` journal stamps
        * for a *maintained* project (≥1 journal entry): the three canonical
          sections (``## What it is``, ``## State & components``,
          ``## Interfaces / how used``) must be non-empty

Oracle-error triggers (code 2):
    * CANON.md missing or unreadable (infrastructure broken — this is not a
      "stale" state that the project can stay in; it is a structural failure).
    * Any unexpected OS / IO error during evaluation.

Lint issues → stale (1), not oracle-error, because they are assessable
(the file exists, we can read it, we can describe the problem).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from . import fields, paths, placeholders, slug, sync
from .arc.stream import passport_path
from .cannon import merge as _merge
from .cannon import reality, rev, store


# ---------------------------------------------------------------------------
# cannon lint
# ---------------------------------------------------------------------------

def cannon_lint(root: Path) -> List[str]:
    """Return structural lint issues for *root*'s CANON.md.

    An empty list means the canon is structurally healthy. Raises
    ``FileNotFoundError`` when CANON.md is missing; :func:`decide` converts
    that to an oracle-error (code 2).

    Lint clauses (c1–c4):

    c1: no ``<…>`` template placeholders.
    c2: no duplicate ``## `` headings (checked on the raw split, before
        ``_load`` folds them away, so the rot is visible).
    c3: no duplicate ``### date · slug`` journal stamps.
    c4: for a maintained project (≥1 journal entry): canonical sections
        non-empty.
    """
    text = store.read(root)  # raises FileNotFoundError if missing
    issues: List[str] = []

    # (c1) Template placeholders anywhere in CANON.md — but a ``<…>`` example inside
    # a code span or fenced block is legitimate prose, not an unfilled field, so the
    # scan runs over a code-masked copy. The pattern is the shared
    # ``placeholders._ANGLE`` symbol so the gate and the close-guard can never diverge.
    masked = placeholders.mask_code(text)
    for match in placeholders._ANGLE.finditer(masked):
        issues.append(
            "template placeholder in CANON.md: {0!r}".format(match.group(0))
        )

    # (c2) Duplicate ## headings (raw split preserves duplicates).
    _pre, raw_sections = _merge._split(text)
    seen_titles: set = set()
    for title, _body in raw_sections:
        if title in seen_titles:
            issues.append("duplicate heading in CANON.md: ## {0}".format(title))
        seen_titles.add(title)

    # (c3) Duplicate ### journal stamps.
    _pre2, _secs, journal_body = _merge._load(text)
    stamp_counts: dict = {}
    for line in journal_body.splitlines():
        if line.startswith("### "):
            stamp = line.strip()
            stamp_counts[stamp] = stamp_counts.get(stamp, 0) + 1
    for stamp, count in stamp_counts.items():
        if count > 1:
            issues.append("duplicate journal stamp in CANON.md: {0}".format(stamp))

    # (c4) Maintained project: canonical sections non-empty.
    if journal_body.strip():
        sections_map = {t: b for t, b in _secs}
        for title in store.SECTIONS[:-1]:  # all except "Cannon journal"
            body = sections_map.get(title, "")
            if not body.strip():
                issues.append(
                    "empty canonical section in maintained CANON.md:"
                    " ## {0}".format(title)
                )

    # (c5) Prose-staleness: a maintained project with a canon-covers manifest but
    # NO reality-rev baseline has never reconciled its prose against reality —
    # surface the un-baselined state so a merge stamps it (a distinct signal from
    # the standing reality↔canon drift the gate raises once a baseline exists).
    if journal_body.strip() and reality.parse_manifest(root) is not None:
        if reality.parse_baseline(root) is None:
            issues.append(
                "canon missing reality-rev baseline "
                "(run 'tide cannon merge' to stamp the reality↔canon baseline)"
            )

    return issues


# ---------------------------------------------------------------------------
# Open-arc enumeration
# ---------------------------------------------------------------------------

def _open_arc_dirs(root: Path) -> List[Path]:
    """Return all OPEN arc dirs under ``.tide/arcs/`` (top-level + goal sub-arcs).

    An entry is "open" when its dir name is NOT wrapped in ``__…__``. Goals'
    nested sub-arcs are also scanned so the drift check reaches arcs inside
    an open goal.
    """
    arcs = paths.arcs_dir(Path(root))
    result: List[Path] = []
    if not arcs.is_dir():
        return result

    for entry in sorted(arcs.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if not slug.is_entry(name) or slug.is_closed_entry(name):
            continue
        result.append(entry)
        if slug.is_goal_entry(name):
            sub_arcs = entry / paths.ARCS_DIRNAME
            if sub_arcs.is_dir():
                for sub in sorted(sub_arcs.iterdir()):
                    if (
                        sub.is_dir()
                        and slug.is_entry(sub.name)
                        and not slug.is_closed_entry(sub.name)
                    ):
                        result.append(sub)

    return result


# ---------------------------------------------------------------------------
# Stale checks (returns reason list; non-empty → code 1)
# ---------------------------------------------------------------------------

def _stale_checks(root: Path) -> List[str]:
    """Return all stale-reason strings for *root* (empty list → current).

    Raises on structural / IO errors so :func:`decide` can convert them to
    oracle-error (code 2).
    """
    reasons: List[str] = []

    # (a) Unmerged deltas — both closed arcs AND active arcs.
    offenders = sync.unmerged_deltas(root, include_active=True)
    for o in offenders:
        reasons.append("unmerged delta: arc {0}".format(o.name))

    # (b) Open-arc drift on cannon-rev and reality-rev.
    current_cr = rev.compute(root)
    current_rr = reality.reality_rev(root)  # None when no manifest

    for entry in _open_arc_dirs(root):
        pp = passport_path(entry)

        # cannon-rev drift
        stamped_cr = fields.read_field(pp, "cannon-rev")
        if stamped_cr is not None and stamped_cr != current_cr:
            reasons.append(
                "arc {0} drifted on cannon-rev"
                " (stamped {1}, current {2})".format(
                    entry.name, stamped_cr, current_cr
                )
            )

        # reality-rev drift (only when a manifest is present)
        if current_rr is not None:
            stamped_rr = fields.read_field(pp, "reality-rev")
            if stamped_rr is not None and stamped_rr != current_rr:
                reasons.append(
                    "arc {0} drifted on reality-rev"
                    " (covered files moved without a canonical-section update)".format(
                        entry.name
                    )
                )

    # (b2) Standing reality↔canon baseline — independent of open arcs. The baseline
    # is the reality-rev CANON.md was last reconciled with (stamped at merge). When
    # it lags the current reality-rev, the covered code moved since the prose was
    # last merged ⇒ the re-entry prose is presumed stale. Degrades silently when no
    # manifest (current_rr is None) or no baseline (legacy / un-stamped canon).
    if current_rr is not None:
        baseline = reality.parse_baseline(root)
        if baseline is not None and baseline != current_rr:
            reasons.append(
                "canon prose may be stale: covered code moved since last merge "
                "(canon reality-rev {0}, current {1}) — re-read CANON.md re-entry "
                "prose, reconcile via an arc + cannon merge".format(baseline, current_rr)
            )

    # (c) Cannon lint.
    lint_issues = cannon_lint(root)
    reasons.extend(lint_issues)

    return reasons


# ---------------------------------------------------------------------------
# Public oracle
# ---------------------------------------------------------------------------

def decide(root: Path) -> Tuple[int, List[str]]:
    """Return ``(code, reasons)`` for the tri-state cannon-gate oracle.

    ``code`` is 0 (current), 1 (stale), or 2 (oracle-error). ``reasons`` is
    empty for code 0; a list of human-readable diagnostic strings for codes 1
    and 2.

    **FAIL-LOUD contract**: code 2 is NEVER returned as a false 0. A broken
    oracle surfaces as 2, which callers MUST treat as an alert. The guard:

    * Missing or unreadable CANON.md → 2 (not 0, not 1).
    * Any unexpected OS / IO error → 2.
    * Lint failures and drift → 1 (assessable, not infrastructure failures).
    """
    try:
        canon = paths.canon_file(Path(root))
        if not canon.is_file():
            return 2, [
                "oracle-error: CANON.md missing at {0}"
                " (run 'tide cannon init')".format(canon)
            ]
        # Probe readability; raises OSError / UnicodeDecodeError if broken.
        canon.read_text(encoding="utf-8")

        reasons = _stale_checks(root)
        return (1 if reasons else 0), reasons

    except (OSError, UnicodeDecodeError) as exc:
        return 2, ["oracle-error: {0}".format(exc)]
    except Exception as exc:  # pragma: no cover  # catch-all: never silently 0
        return 2, ["oracle-error (unexpected): {0}".format(exc)]
