"""SOC-101 пакет Ж — передача по порогу контекста (acceptance-05 §9).

F44 — хук порога даёт готовый шаблон пульса ПЕРЕДАЧА, ``handoff --auto`` без
свежей ПЕРЕДАЧИ предупреждает (``--strict`` — отказывает).

Всё на временных каталогах (фикстура ``world`` из test_autohandoff): ни настоящих
AO-сессий, ни ao send, ни Telegram.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tide import cli, fields, handoff_contract, handoff_queue as hq, offload
from tide.hooks import context_watch as cw, role_gate
from tide.launcher import autohandoff as ah
from tests.test_autohandoff import SID, _payload, world  # noqa: F401 — фикстура


# --- F44: шаблон ПЕРЕДАЧИ -----------------------------------------------------

def test_pass_template_labels_all_parse_into_seed_blocks():
    """Метки шаблона через « · » разбираются сборщиком в свои блоки."""
    note = ("ПЕРЕДАЧА. Где мы: шаг 3 · Ожидание Гриши: «доска с телефона» (tg 3365) · "
            "Ждёт Гришу: SOC-112 на приёмке · В полёте: воркер v2.4 · Хвосты: нет · "
            "Решения: вкладка РЕШЕНИЯ · Опыт: role-gate режет curl · "
            "Карта: /abs/plan.md — план")
    got = handoff_contract.pulse_labels(note)
    assert got == {"expectation": ["«доска с телефона» (tg 3365)"],
                   "awaiting": ["SOC-112 на приёмке"], "inflight": ["воркер v2.4"],
                   "tails": ["нет"], "decisions": ["вкладка РЕШЕНИЯ"],
                   "experience": ["role-gate режет curl"],
                   "entry_map": ["/abs/plan.md — план"]}
    assert handoff_contract.is_pass_note(note)
    # шаблон-заготовка — тоже ПЕРЕДАЧА со всеми метками
    keys = set(handoff_contract.pulse_labels(ah.PASS_TEMPLATE_NOTE))
    assert {"expectation", "awaiting", "inflight", "tails", "decisions", "experience",
            "entry_map"} <= keys


def test_soft_hook_gives_pass_template_then_layers_then_auto(world):
    r = cw.watch_reason(world["proj"], _payload(world, 203_000))
    assert r
    i_pass = r.index('tide offload seam/s1 "ПЕРЕДАЧА. Где мы:')
    i_auto = r.index(cw.AUTO_CMD)
    assert i_pass < i_auto
    for label in ("Ожидание Гриши: «…» (tg N)", "Ждёт Гришу:", "В полёте:", "Хвосты:",
                  "Решения:", "Опыт:", "Карта: <путь> — зачем"):
        assert label in r
    assert "<что сделал>" not in r


@pytest.mark.parametrize("tokens,word", [(300_000, "жёсткий порог"), (510_000, "ТРЕВОГА")])
def test_hard_and_alarm_hooks_give_the_same_template(world, tokens, word):
    r = cw.watch_reason(world["proj"], _payload(world, tokens))
    assert word in r and ah.pass_template(world["sess"]) in r
    assert cw.AUTO_CMD in r


def test_hook_commands_pass_the_role_gate(world):
    r = cw.watch_reason(world["proj"], _payload(world, 300_000))
    cmds = [ln.strip().lstrip("0123456789. ") for ln in r.splitlines()
            if ln.strip().lstrip("0123456789. ").startswith("tide ")]
    assert len(cmds) == 2
    for c in cmds:
        assert role_gate._bash_refusal(c) is None, c


def _pulse(sess, note, at):
    offload.write_pulse(sess, note=note, now=at)


def test_fresh_pass_note_window(world, monkeypatch):
    now = datetime(2026, 10, 9, 14, 30)
    assert ah.fresh_pass_note(world["sess"], now=now) is None
    _pulse(world["sess"], "ПЕРЕДАЧА. Ожидание Гриши: «x»", now - timedelta(minutes=40))
    assert ah.fresh_pass_note(world["sess"], now=now) is None  # старше 30 мин
    monkeypatch.setenv(ah.PASS_FRESH_ENV, "60")
    assert ah.fresh_pass_note(world["sess"], now=now).startswith("ПЕРЕДАЧА")
    monkeypatch.delenv(ah.PASS_FRESH_ENV)
    _pulse(world["sess"], "сделано: передача работы 117 фактов", now - timedelta(minutes=1))
    assert ah.fresh_pass_note(world["sess"], now=now) is None  # не метка — слово
    _pulse(world["sess"], "ПЕРЕДАЧА. Где мы: тут", now - timedelta(minutes=5))
    assert "Где мы: тут" in ah.fresh_pass_note(world["sess"], now=now)
    assert ah.pass_warning(world["sess"], now=now) is None


def test_auto_dry_run_without_pass_warns_and_shows_template(world, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    assert cli.main(["handoff", "continue", "--auto", "--dry-run"]) == 0
    cap = capsys.readouterr()
    assert "нет пульса ПЕРЕДАЧА за последние 30 мин" in cap.err
    assert ah.pass_template(world["sess"]) in cap.err
    assert "пульса ПЕРЕДАЧА нет — шаблон" in cap.out
    # --strict на dry-run — только предупреждение, ничего не пишет и не падает
    assert cli.main(["handoff", "continue", "--auto", "--dry-run", "--strict"]) == 0
    assert "с --strict передачу не делаю" in capsys.readouterr().err


def test_auto_strict_refuses_without_pass(world, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    monkeypatch.setenv("TIDE_HANDOFF_NO_SPAWN", "1")
    with pytest.raises(ah.AutoHandoffError, match="--strict"):
        ah.run_auto(world["proj"], session_ref=None, strict=True, spawn=False)
    assert hq.list_offers(world["home"]) == []
    assert not (fields.read_field(world["sess"] / "arc.md", ah.HANDED_OFF_FIELD) or "")


def test_auto_default_warns_but_hands_over(world, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    res = ah.run_auto(world["proj"], spawn=False)
    assert res.pass_warning and res.handoff is not None
    assert "нет пульса ПЕРЕДАЧА" in capsys.readouterr().err


def test_auto_with_fresh_pass_is_silent(world, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    offload.write_pulse(world["sess"], note=ah.PASS_TEMPLATE_NOTE.replace("…", "x"))
    assert cli.main(["handoff", "continue", "--auto", "--dry-run", "--strict"]) == 0
    cap = capsys.readouterr()
    assert "ПЕРЕДАЧА за последние" not in cap.err and "шаблон:" not in cap.out
