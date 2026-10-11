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
    """Срезы окна п.2–3 (tg 1223) + SOC-181 п.8: правила нити — ОДИН файл
    (prompts/thread-rules.md); роли, правило диспетчера и сид запуска ссылаются на него."""
    from tide import escalate_rules as e

    rules = e.rules_file().read_text(encoding="utf-8")
    orch = seed.read_role_prompt("orchestrator") or ""
    worker = seed.read_role_prompt("worker") or ""
    dispatch = (paths.global_rules_dir() / "subagents.md").read_text(encoding="utf-8")
    for rule in (e.SUBAGENT_RESULT_RULE, e.MAIL_RULE, e.THROTTLE_RULE, e.MODEL_RULE,
                 e.TEST_WAIT_RULE, e.EXP_RULE, e.ESCALATE_RULE, e.WORK_RULE):
        assert rule and rule in rules
        # текст правила живёт только в файле — не в промптах ролей
        assert rule not in orch and rule not in worker and rule not in dispatch
    for doc in (orch, worker, dispatch):
        assert e.RULES_FILE_NAME in doc
    assert "~2000 знаков" in e.SUBAGENT_RESULT_RULE and "workspace/" in e.SUBAGENT_RESULT_RULE
    assert "model: sonnet" in e.MODEL_RULE
    s = seed.build_seed(project_name="proj", arc_ref="worker-1", thread_name="worker",
                        arc_text="x")
    assert e.rules_line() in s and e.MAIL_RULE not in s


def test_test_wait_rule_rides_in_worker_template_and_seed():
    """Проба management-25: сабагент не ждёт тесты дольше ~4,5 мин — в фоне."""
    from tide import escalate_rules as e
    assert "~4,5 мин" in e.TEST_WAIT_RULE and "run_in_background" in e.TEST_WAIT_RULE
    assert e.TEST_WAIT_SEC < 300  # кэш живёт 5 мин
    assert e.TEST_WAIT_RULE in e.window_rules().splitlines()
    assert e.TEST_WAIT_RULE in e.rules_file().read_text(encoding="utf-8")


def test_rules_file_thresholds_match_code():
    """Пороги tide escalate в коде и текст файла правил нити — не расходятся."""
    from tide import escalate_rules as e
    assert "{0} неудачные попытки".format(e.FAILED_ATTEMPTS) in e.ESCALATE_RULE
    assert "{0} мин без прогресса".format(e.STALL_MIN) in e.ESCALATE_RULE
    assert "без квитанции {0} мин".format(e.ACK_TIMEOUT_MIN) in e.ESCALATE_RULE
    assert "~{0} знаков".format(e.SUBAGENT_RESULT_CHARS) in e.SUBAGENT_RESULT_RULE
    assert e.read_rules(e.rules_file()) == e._RULES
    assert e.rules_line() == "правила нити: {0} (читать на старте)".format(e.rules_file())
