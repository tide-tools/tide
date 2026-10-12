"""Контракт сида (SOC-101): одно место — tide.handoff_contract; потребители читают оттуда."""

from __future__ import annotations

import re
from pathlib import Path

from tide import handoff_contract as hc
from tide.arc import templates
from tide.hooks import context_watch
from tide.launcher import handoff, handoff_audit, seed_draft

SKILL = Path(__file__).resolve().parent.parent / "skills" / "handoff" / "SKILL.md"

CONTRACT_ORDER = [
    "главное", "ожидание Гриши", "где мы", "опыт", "решения", "задачи Linear", "ждёт Гришу",
    "следующий шаг", "в работе прямо сейчас", "неотвеченные реплики Гриши",
    "карта входа", "окружение", "сырой журнал",
]


def test_thirteen_blocks_in_contract_order_none_droppable():
    """Двенадцать блоков контракта и «главное» нити первым (решение Гриши № 24)."""
    assert list(hc.NAMES) == CONTRACT_ORDER
    assert len({b.key for b in hc.BLOCKS}) == 13
    assert all(b.keep_empty and b.empty for b in hc.BLOCKS)
    assert all(b.kind in (hc.PROSE, hc.LIST) for b in hc.BLOCKS)
    assert hc.JOURNAL_SHARE == 0.30 and hc.HANDOFF_TOKENS == 150_000


def test_empty_block_says_why():
    assert hc.empty_line(hc.block("inflight")).startswith("— источник пуст: метки «В полёте:»")
    assert hc.empty_line(hc.block("linear"), "нет") == "— нет"


def test_old_names_are_synonyms():
    assert "карта" in hc.block("entry_map").aliases
    assert "курсор" in hc.block("where").aliases
    assert "дальше" in hc.block("next").aliases
    assert "финал" in hc.block("expectation").aliases
    secs = {"карта входа (по порядку)": ["- a"], "дальше": ["- b"]}
    assert hc.find_body(secs, "entry_map") == ["- a"]
    assert hc.find_body(secs, "next") == ["- b"]
    assert hc.find_body(secs, "environment") == []


def test_synonyms_do_not_steal_each_others_headings():
    """Каждое каноническое имя опознаётся ровно своим блоком (доска матчит подстрокой)."""
    for b in hc.BLOCKS:
        head = hc.norm(b.name)
        owners = [o.key for o in hc.BLOCKS if any(a in head for a in hc.names_of(o))]
        assert owners == [b.key], (b.name, owners)


def test_missing_lists_absent_blocks():
    assert hc.missing(hc.NAMES) == ()
    assert hc.missing(["курсор", "дальше"]) == tuple(
        n for n in hc.NAMES if n not in ("где мы", "следующий шаг"))


def test_board_contract_derives_from_blocks():
    assert [lbl for lbl, _k, _a in hc.SEED_CONTRACT] == list(hc.NAMES)
    assert all(lbl.lower() in aliases for lbl, _k, aliases in hc.SEED_CONTRACT)


def test_consumers_read_the_one_source():
    assert seed_draft.BLOCKS == hc.NAMES
    assert seed_draft.JOURNAL_SHARE == hc.JOURNAL_SHARE
    assert seed_draft.EMPTY == hc.EMPTY
    assert handoff_audit.AUDIT_SECTIONS == hc.AUDIT_SECTIONS
    assert context_watch.SOFT_TOKENS == hc.HANDOFF_TOKENS
    layers = [h for h, _ph in templates.WHERE_LAYERS]
    assert layers == [hc.name("expectation"), hc.name("where"), hc.name("experience")]


def test_audit_sections_name_their_seed_blocks():
    keys = [k for k, _t in hc.AUDIT_SECTIONS]
    assert keys == ["main", "cursor", "context", "next", "decisions", "experience",
                    "expectation", "dialog", "understood"]
    titles = dict(hc.AUDIT_SECTIONS)
    assert titles["dialog"].startswith(hc.name("unanswered"))
    assert titles["context"].startswith(hc.name("journal"))


def test_skill_table_matches_the_contract():
    text = SKILL.read_text(encoding="utf-8")
    rows = re.findall(r"(?m)^\| `## ([^`]+)` \|", text)
    assert rows == list(hc.NAMES)
    assert "Других секций в сиде нет" not in text
    assert "30%" in text and "150 тыс." in text and "200 тыс." in text


def test_pickup_goal_reads_the_next_step_block():
    seed = seed_draft.render_draft({seed_draft.NEXT: ["- прогнать тесты", "- выкатить"]})
    assert handoff._pickup_goal(Path("/nonexistent"), seed) == "прогнать тесты"
    # пустой блок «— почему» — не цель: падаем на цель нити (здесь её нет)
    assert handoff._pickup_goal(Path("/nonexistent"), seed_draft.render_draft({})) is None


def test_skill_does_not_promise_a_dissolved_stamp():
    """С 16.07 take() не пишет dissolved: — растворённость выводится из очереди
    (handoff_queue.is_dissolved). Скилл обещал штамп — чистка SOC-101."""
    text = SKILL.read_text(encoding="utf-8")
    assert "растворит предшественника — `dissolved:`" not in text
    assert "is_dissolved" in text
