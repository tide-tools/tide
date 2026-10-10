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
    # Decision 11 (cold-start): ONE truth of the role — the head holds the CLI and
    # DISPATCHES build-work to workers; the human leads by "what" and signs the gates;
    # no ceremony (contract/canon) minted unasked. (The 06-29 minimal-mode "do NOT
    # dispatch" was a stale half-pivot, reverted here.)
    text = seed.read_role_prompt("orchestrator")
    assert text is not None
    assert "tide" in text and "session" in text.lower()
    assert "thread" in text.lower()
    assert "dispatch" in text.lower()  # dispatch is the norm, not a ban


def test_window_rules_ride_in_role_prompts_and_seed():
    """Срезы окна п.2–3 (tg 1223): итог сабагента ≤ ~2000 знаков, ao send — сигнал."""
    from tide import escalate_rules as e

    orch = seed.read_role_prompt("orchestrator") or ""
    worker = seed.read_role_prompt("worker") or ""
    dispatch = (paths.global_rules_dir() / "subagents.md").read_text(encoding="utf-8")
    assert e.SUBAGENT_RESULT_RULE in orch and e.MAIL_RULE in orch
    assert e.SUBAGENT_RESULT_RULE in worker
    assert e.SUBAGENT_RESULT_RULE in dispatch
    assert "~2000 знаков" in e.SUBAGENT_RESULT_RULE and "workspace/" in e.SUBAGENT_RESULT_RULE
    s = seed.build_seed(project_name="proj", arc_ref="worker-1", thread_name="worker",
                        arc_text="x")
    assert e.SUBAGENT_RESULT_RULE in s and e.MAIL_RULE in s
    # SOC-166: правило троттлинга — в сиде и у ведущей (Agent хук не видит)
    assert e.THROTTLE_RULE in s and e.THROTTLE_RULE in orch
