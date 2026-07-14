"""The layering rule of the tide package, enforced by AST — no import-linter dependency.

    cli / hooks  →  launch (launcher/, adapters/)  →  api  →  domain  →  store

A module may import sideways or DOWN, never UP. ``KNOWN_DEBT`` lists the pre-existing
violations (burn-down list — remove entries as they are fixed, never add new ones).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "tide"

# Layer of a module, by its path relative to src/tide (first segment or stem).
STORE = {"fields", "io", "paths", "slug", "numbering", "placeholders"}
DOMAIN = {
    "arc", "handoff_queue", "offload", "registry", "sessions", "sync", "health",
    "harness", "lookback", "ledger", "roster",
    # domain data that lives in .tide/ even though the module sits top-level:
    "canon", "contract", "gate", "readme", "strictness",
}
API = {"api"}
LAUNCH = {"launcher", "adapters"}
# Everything else (cli, hooks, canon, contract, onboarding, update, mcp, adopt,
# doctor, …) is an edge and may import anything.

RANK = {"store": 0, "domain": 1, "api": 2, "launch": 3, "edge": 4}

# Pre-existing violations: (module, imported). Burn down; do not extend.
KNOWN_DEBT = {
    ("arc/candidate.py", "cli"),
    ("canon/commands.py", "cli"),
    ("contract/lifecycle.py", "cli"),
    ("arc/land.py", "adapters"),
    ("arc/land.py", "cli"),
    ("arc/stream.py", "adapters"),
    ("arc/worktree.py", "adapters"),
    ("launcher/seed.py", "hooks"),  # ROLE_REMINDERS constant — move down, then drop
}


def layer_of(rel: str) -> str:
    head = rel.split("/", 1)[0].removesuffix(".py")
    if head in STORE:
        return "store"
    if head in DOMAIN:
        return "domain"
    if head in API:
        return "api"
    if head in LAUNCH:
        return "launch"
    return "edge"


def tide_imports(path: Path) -> set[str]:
    """Top-level tide modules imported by *path* (relative imports resolved)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg = list(path.relative_to(SRC).parent.parts)  # package parts, e.g. ["arc"]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: resolve against this module's package
                base = pkg[: len(pkg) - (node.level - 1)]
                if node.module:
                    full = base + node.module.split(".")
                    found.add(full[0])
                else:  # from . import x, y
                    for alias in node.names:
                        full = base + [alias.name]
                        found.add(full[0])
            elif node.module and node.module.split(".")[0] == "tide":
                parts = node.module.split(".")
                if len(parts) > 1:
                    found.add(parts[1])
                else:  # from tide import x, y
                    for alias in node.names:
                        found.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "tide" and len(parts) > 1:
                    found.add(parts[1])
    return found


def test_no_upward_imports():
    violations = []
    for path in sorted(SRC.rglob("*.py")):
        rel = str(path.relative_to(SRC))
        my_layer = layer_of(rel)
        if my_layer == "edge":
            continue  # edges may import anything
        for target in tide_imports(path):
            target_layer = layer_of(target)
            if RANK[target_layer] > RANK[my_layer]:
                if (rel, target) in KNOWN_DEBT:
                    continue
                violations.append(f"{rel} ({my_layer}) imports {target} ({target_layer})")
    assert not violations, "upward imports violate the layer rule:\n" + "\n".join(violations)


def test_known_debt_is_still_real():
    """Entries in KNOWN_DEBT must still exist — else remove them (ratchet down)."""
    stale = []
    for rel, target in sorted(KNOWN_DEBT):
        path = SRC / rel
        if not path.exists() or target not in tide_imports(path):
            stale.append(f"{rel} -> {target}")
    assert not stale, "KNOWN_DEBT entries no longer real — delete them:\n" + "\n".join(stale)


def test_api_door_exists_and_is_flat():
    """The facade imports cleanly and re-exports only domain names (no logic)."""
    tree = ast.parse((SRC / "api.py").read_text(encoding="utf-8"))
    for node in tree.body:
        assert isinstance(
            node, (ast.ImportFrom, ast.Import, ast.Assign, ast.Expr, ast.AnnAssign)
        ), f"api.py must stay a flat facade — found {type(node).__name__}"
