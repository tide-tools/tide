"""U12 unit — the shipped global prompts / rules / handoff skill are on disk."""

from __future__ import annotations

from tide import paths
from tide.launcher import seed


def test_global_prompts_shipped():
    pdir = paths.global_prompts_dir()
    for role in ("orchestrator", "worker", "user-playbook"):
        f = pdir / "{0}.md".format(role)
        assert f.is_file(), "missing prompt {0}".format(f)
        assert f.read_text(encoding="utf-8").strip()


def test_global_rules_shipped():
    rdir = paths.global_rules_dir()
    for name in ("subagents", "canon-sync", "contract"):
        f = rdir / "{0}.md".format(name)
        assert f.is_file(), "missing rule {0}".format(f)
        assert f.read_text(encoding="utf-8").strip()


def test_seed_now_embeds_shipped_orchestrator_prompt():
    # U12 ships prompts/orchestrator.md → read_role_prompt resolves it (was None before).
    # Minimal-mode rewrite: a tide session bound to a thread/session, no
    # contract/canon ceremony.
    text = seed.read_role_prompt("orchestrator")
    assert text is not None
    assert "tide" in text and "session" in text.lower()
    assert "thread" in text.lower()
