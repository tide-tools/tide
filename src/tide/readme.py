"""tide.readme — generate a project's user-facing README.md as a canon projection.

The two-entry principle (ratified canon): a project has TWO doors. ``CANON.md``
is the **agent door** — the technical living-IS truth an agent re-hydrates from.
``README.md`` is the **user door** — what the project is and how a human enters
it. README is a **derived material**: it must NEVER be hand-maintained (hand =
drift / doc-rot), it is GENERATED from canon, STAMPED with the cannon-rev it was
projected from, and GATED so drift is detectable and self-healing.

This is tide's own code↔canon machinery recursed one level UP (canon↔materials):
just as an arc stamps the ``cannon-rev`` it opened against and the gate trips when
that rev drifts (:mod:`tide.gate`), a README stamps the ``cannon-rev`` it was
generated from and :func:`check` trips when canon moves ahead OR the README was
hand-edited. One machinery, complexity does not grow.

KISS gate: the README is a deterministic pure function of CANON.md + the current
cannon-rev. So "is this README current?" reduces to "does the on-disk file equal
what we would generate right now?" — one byte comparison catches both drift modes
(canon moved ahead → stamp + body differ; hand-edited → body differs). The stamp
is still embedded so the diagnostic can name *which* mode tripped, and so a human
reading the raw file sees it is generated, not authored.

Logic is plain text functions (argparse-free, unit-testable); :func:`register`
wires the thin handler ``cli.py`` calls.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from . import paths
from .cannon import rev, store

# The provenance stamp: an HTML comment (invisible in rendered markdown) on the
# last line. It records the source canon-rev so drift is detectable and so the
# raw file announces itself as generated. The cannon-rev token is parsed back out
# by :func:`parse_stamp` — keep the ``cannon-rev <rev>`` shape stable.
STAMP_PREFIX = "<!-- tide-readme"
_STAMP_RE = re.compile(r"<!-- tide-readme .*cannon-rev ([0-9a-f]+)")

# H1 of CANON.md is ``# CANON.md — <name>`` (store.canon_template). We project the
# bare project name out of it for the README title.
_CANON_H1_RE = re.compile(r"^#\s*CANON\.md\s*[—-]\s*(.+?)\s*$")

# Canonical sections we project into the user door. "What it is" = the intent;
# "Interfaces / how used" = how a human engages. "State & components" is the
# agent-facing where-we-are detail — we deliberately do NOT dump it; we POINT to
# CANON.md for the living state (reference > duplication → less drift).
WHAT_SECTION = "What it is"
HOWUSED_SECTION = "Interfaces / how used"


def project_name(canon_text: str, fallback: str = "project") -> str:
    """Return the project name parsed from CANON.md's ``# CANON.md — <name>`` H1.

    Falls back to *fallback* when the header is missing or malformed so rendering
    never raises on a half-seeded canon.
    """
    for line in canon_text.splitlines():
        m = _CANON_H1_RE.match(line.strip())
        if m:
            return m.group(1).strip()
        if line.strip().startswith("## "):
            break  # past the preamble; no usable H1
    return fallback


def render(canon_text: str, cannon_rev: str, fallback_name: str = "project") -> str:
    """Return the README.md text projected from *canon_text*, stamped with *cannon_rev*.

    Pure + deterministic: identical (canon_text, cannon_rev) ⇒ identical bytes,
    which is what makes :func:`check` a single byte comparison. The body projects
    only the user-facing sections; living technical state is referenced, not
    duplicated. The provenance stamp is always the final line.
    """
    sections = store.scan_text(canon_text)
    name = project_name(canon_text, fallback=fallback_name)
    what = sections.get(WHAT_SECTION, "").strip()
    how = sections.get(HOWUSED_SECTION, "").strip()

    parts: List[str] = ["# {0}".format(name), ""]
    if what:
        parts.append(what)
        parts.append("")
    if how:
        parts.append("## How to use")
        parts.append("")
        parts.append(how)
        parts.append("")

    parts.append("---")
    parts.append("")
    parts.append(
        "*For the living technical state of this project (where it is right now), "
        "see [`.tide/cannon/CANON.md`](.tide/cannon/CANON.md) — the agent-facing "
        "source of truth this page is generated from.*"
    )
    parts.append("")
    parts.append(
        "{0} generated from CANON.md @ cannon-rev {1} — do NOT hand-edit; "
        "regenerate via 'tide readme' (drift gate: 'tide readme --check'). -->".format(
            STAMP_PREFIX, cannon_rev
        )
    )
    return "\n".join(parts) + "\n"


def parse_stamp(readme_text: str) -> Optional[str]:
    """Return the cannon-rev recorded in *readme_text*'s stamp, or None if absent.

    None means the file carries no tide-readme stamp — i.e. it was hand-written
    (or pre-dates the generator), which :func:`check` treats as stale.
    """
    for line in readme_text.splitlines():
        m = _STAMP_RE.search(line)
        if m:
            return m.group(1)
    return None


def readme_file(root: Path) -> Path:
    """Path to the project's user-door ``README.md`` (project root, like init)."""
    return Path(root) / "README.md"


def generate(
    root: Path, dry_run: bool = False, fallback_name: Optional[str] = None
) -> Tuple[str, str]:
    """Project CANON.md → README.md for *root*; return ``(text, status)``.

    *status* is one of ``"dry-run"`` (nothing written), ``"current"`` (already
    byte-identical — idempotent no-op), ``"generated"`` (file did not exist), or
    ``"regenerated"`` (overwrote a drifted/stale file). Raises ``FileNotFoundError``
    when CANON.md is missing (mirrors :func:`tide.cannon.store.read`).
    """
    canon_text = store.read(root)  # raises FileNotFoundError if missing
    name = fallback_name or Path(root).resolve().name
    # Stamp from the text we already read (not a second rev.compute disk read) —
    # one read, no canon↔stamp TOCTOU. compute_text(canon_text) == compute(root).
    text = render(canon_text, rev.compute_text(canon_text), fallback_name=name)

    if dry_run:
        return text, "dry-run"

    target = readme_file(root)
    existed = target.is_file()
    if existed and target.read_text(encoding="utf-8") == text:
        return text, "current"
    target.write_text(text, encoding="utf-8")
    return text, ("regenerated" if existed else "generated")


def check(root: Path) -> Tuple[int, List[str]]:
    """Tri-state drift gate for the derived README — mirrors :func:`tide.gate.decide`.

    Exit codes:
        0 = current      — README equals the current canon projection.
        1 = stale        — README missing, unstamped, canon moved ahead, or
                           hand-edited away from the projection.
        2 = oracle-error — CANON.md missing/unreadable (FAIL-LOUD: callers MUST
                           treat 2 as an alert, never a silent pass).
    """
    try:
        canon = paths.canon_file(Path(root))
        if not canon.is_file():
            return 2, [
                "oracle-error: CANON.md missing at {0}"
                " (run 'tide cannon init')".format(canon)
            ]
        canon_text = canon.read_text(encoding="utf-8")  # probe readability

        target = readme_file(root)
        if not target.is_file():
            return 1, ["README.md missing — run 'tide readme' to generate it"]

        # Derive the rev from the canon_text already read above (no second disk
        # read; closes the canon↔stamp TOCTOU). compute_text == compute on content.
        current_rev = rev.compute_text(canon_text)
        on_disk = target.read_text(encoding="utf-8")
        expected = render(canon_text, current_rev, fallback_name=Path(root).resolve().name)
        if on_disk == expected:
            return 0, []

        stamped = parse_stamp(on_disk)
        if stamped is None:
            return 1, [
                "README.md carries no tide-readme stamp (hand-written?) — "
                "run 'tide readme' so it derives from canon"
            ]
        if stamped != current_rev:
            return 1, [
                "README stale: generated from cannon-rev {0}, canon now {1} "
                "(canon moved ahead) — run 'tide readme'".format(stamped, current_rev)
            ]
        return 1, [
            "README drifted from the canon projection (hand-edited?) — "
            "run 'tide readme' to re-derive it"
        ]
    except (OSError, UnicodeDecodeError) as exc:
        return 2, ["oracle-error: {0}".format(exc)]
    except Exception as exc:  # pragma: no cover  # never silently pass
        return 2, ["oracle-error (unexpected): {0}".format(exc)]


# --- CLI wiring ------------------------------------------------------------

def _cmd_readme(args) -> int:
    root = paths.require_tide_root()

    if getattr(args, "check", False):
        code, reasons = check(root)
        if code == 0:
            print("readme: current")
        elif code == 1:
            print("readme: stale ({0} issue(s))".format(len(reasons)))
            for r in reasons:
                print("  - {0}".format(r))
        else:  # code == 2
            print("readme: oracle-error (code 2)", file=sys.stderr)
            for r in reasons:
                print("  {0}".format(r), file=sys.stderr)
        return code

    try:
        text, status = generate(root, dry_run=getattr(args, "dry_run", False))
    except FileNotFoundError as exc:
        # Missing CANON.md is infrastructure-broken (oracle-error), not "stale":
        # surface code 2 here too, so generate mode agrees with --check's
        # FAIL-LOUD code-2 contract instead of conflating it with main()'s code-1.
        print("readme: oracle-error: {0}".format(exc), file=sys.stderr)
        return 2
    if status == "dry-run":
        # Print the projection only; write nothing (composable / reviewable).
        sys.stdout.write(text)
        return 0
    if status == "current":
        print("readme: already current (no change)")
    else:
        print("readme: {0} {1}".format(status, readme_file(root)))
    return 0


def register(subparsers) -> None:
    """Add the top-level ``readme`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "readme",
        help="generate the user-door README.md as a projection of CANON.md",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="drift gate: 0=current 1=stale 2=oracle-error (POSIX exit code); writes nothing",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the projected README to stdout, write nothing",
    )
    p.set_defaults(func=_cmd_readme, _cmd="readme")
