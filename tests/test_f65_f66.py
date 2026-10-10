"""SOC-101 acceptance-07 §9: F65 и F66 (+ шаг F61 в хуке порога).

F65 — ``tide thread layer --where`` ЗАМЕНЯЕТ раздел «где мы», прежние строки
уходят в ``## журнал слоёв`` goal-файла с датой замены (сборщик сида их не читает).
F66 — хук порога просит ``--expect`` и тогда, когда слой ожидания правлен раньше
пульса с «Ожидание Гриши:»; F61 — ``--main`` в том же шаге, если «главное» пусто
или старше ПЕРЕДАЧИ.

Всё на временных каталогах.
"""

from __future__ import annotations

import os
import time
from datetime import datetime

from tide import offload
from tide.arc import stream, thread_layers as tl
from tide.hooks import context_watch as cw, role_gate
from tests.test_autohandoff import _payload, world  # noqa: F401 — фикстура
from tests.test_plan_main import fill_main


def _goal(world):
    return stream.passport_path(world["thread"])


# --- F65 ------------------------------------------------------------------------

def test_where_replaces_and_journals(world):
    goal = _goal(world)
    tl.write_layers(goal, {"where": "SOC-106 In Review · счёт 0/1"},
                    now=datetime(2026, 10, 9, 8, 36))
    tl.write_layers(goal, {"where": "SOC-106 Done · счёт 3/5", "expectation": "«к пятнице»"},
                    now=datetime(2026, 10, 9, 17, 27))
    text = goal.read_text(encoding="utf-8")
    where = text[text.index("## where we are"):text.index(tl.JOURNAL_HEAD)]
    assert "SOC-106 Done · счёт 3/5" in where
    assert "In Review" not in where  # противоречие не копится
    journal = text[text.index(tl.JOURNAL_HEAD):]
    assert "### где мы — заменено 09.10 17:27" in journal
    assert "- 09.10 08:36 — SOC-106 In Review · счёт 0/1" in journal  # не потерялось
    assert tl.empty_layers(world["thread"]) == ["опыт"]


def test_expect_and_exp_still_append(world):
    goal = _goal(world)
    tl.write_layers(goal, {"expectation": "a", "experience": "x"},
                    now=datetime(2026, 10, 9, 8, 0))
    tl.write_layers(goal, {"expectation": "b", "experience": "y"},
                    now=datetime(2026, 10, 9, 9, 0))
    text = goal.read_text(encoding="utf-8")
    for s in ("08:00 — a", "09:00 — b", "08:00 — x", "09:00 — y"):
        assert s in text
    assert tl.JOURNAL_HEAD not in text


def test_journal_newest_on_top(world):
    goal = _goal(world)
    for h, w in ((8, "один"), (9, "два"), (10, "три")):
        tl.write_layers(goal, {"where": w}, now=datetime(2026, 10, 9, h, 0))
    j = goal.read_text(encoding="utf-8").split(tl.JOURNAL_HEAD, 1)[1]
    assert j.index("заменено 09.10 10:00") < j.index("заменено 09.10 09:00")
    assert "— два" in j and "— один" in j and "— три" not in j


def test_seed_does_not_read_journal(world):
    goal = _goal(world)
    tl.write_layers(goal, {"where": "старое"}, now=datetime(2026, 10, 9, 8, 0))
    tl.write_layers(goal, {"where": "новое"}, now=datetime(2026, 10, 9, 9, 0))
    from tide.launcher.seed_draft import section_body, sections
    body = "\n".join(section_body(sections(goal.read_text(encoding="utf-8")),
                                  "where we are"))
    assert "новое" in body and "старое" not in body


# --- F66 / F61 в хуке -----------------------------------------------------------

def _pulse(world, note, at):
    offload.write_pulse(world["sess"], note=note, now=at)


def _age(path, when):
    ts = time.mktime(when.timetuple())
    os.utime(path, (ts, ts))


def test_hook_asks_expect_when_layer_older_than_pass(world):
    fill_main(world["thread"])
    goal = _goal(world)
    tl.write_layers(goal, {"expectation": "«старое слово» (tg 1)"},
                    now=datetime(2026, 10, 9, 6, 10))
    _pulse(world, "ПЕРЕДАЧА. Где мы: шаг 3 · Ожидание Гриши: «план через менеджера» "
           "(tg 3372) · Хвосты: нет", datetime(2026, 10, 9, 16, 50))
    _age(world["thread"] / "plan.md", datetime(2026, 10, 9, 5, 0))
    fr = tl.freshness(world["thread"])
    assert fr["expect"] == "stale" and fr["main"] == "stale"
    r = cw.watch_reason(world["proj"], _payload(world, 253_000))
    assert "ожидание Гриши правлено 09.10 06:10, а пульс с «Ожидание Гриши:» — 09.10 16:50" in r
    assert "перепиши --expect" in r
    assert "tide thread layer seam --expect" in r and " --main " in r
    assert "--main обязателен" in r


def test_hook_fresh_layer_still_names_rule(world):
    fill_main(world["thread"])
    _pulse(world, "ПЕРЕДАЧА. Где мы: шаг 3 · Ожидание Гриши: «x» (tg 1) · Хвосты: нет",
           datetime(2026, 10, 9, 16, 50))
    tl.write_layers(_goal(world), {"expectation": "«x» (tg 1)"},
                    now=datetime(2026, 10, 9, 16, 50))
    fr = tl.freshness(world["thread"])
    assert fr["expect"] == "fresh" and fr["main"] == "fresh"
    r = cw.watch_reason(world["proj"], _payload(world, 253_000))
    assert "если ожидание в ПЕРЕДАЧЕ (шаг 1) новее слоя" in r
    assert "«главное» свежее" in r
    assert "--where заменяет «где мы» целиком" in r


def test_hook_empty_main_and_expect(world):
    fr = tl.freshness(world["thread"])
    assert fr["expect"] == "empty" and fr["main"] == "empty"
    r = cw.watch_reason(world["proj"], _payload(world, 300_000))
    assert "ожидание Гриши пусто" in r and "«главное» нити пусто" in r
    cmds = [ln.strip().lstrip("0123456789. ") for ln in r.splitlines()
            if ln.strip().lstrip("0123456789. ").startswith("tide ")]
    assert len(cmds) == 3  # ПЕРЕДАЧА · слои+главное одной командой · автопередача
    assert any("--main" in c and "--expect" in c for c in cmds)
    for c in cmds:
        assert role_gate._bash_refusal(c) is None, c
