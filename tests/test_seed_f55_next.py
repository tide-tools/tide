"""SOC-101: «имя · NN» не рвётся в «следующем шаге»; F55 — топик в карте входа."""
from __future__ import annotations

from tide.launcher import seed_draft


# --- «роль · NN» + любое продолжение — имя, не разделитель (F58) ---------------------

def test_name_dot_before_date_is_kept():
    out = seed_draft.block_next(["(а) SOC-105 после F55–F57 · сверка SOC-105 с main · 04 12.10"])
    assert out == ["- (а) SOC-105 после F55–F57", "- сверка SOC-105 с main · 04 12.10"]


def test_name_dot_before_conjunction_is_kept():
    line = ("принять вердикт пары 13 (workspace/acceptance-06.md) → коммент SOC-101 "
            "«Итог:» + main · 04 и management-18 · (а) SOC-105 после F55–F57")
    assert seed_draft.block_next([line]) == [
        "- принять вердикт пары 13 (workspace/acceptance-06.md) → коммент SOC-101 "
        "«Итог:» + main · 04 и management-18",
        "- (а) SOC-105 после F55–F57"]


def test_f58_name_dot_with_word_after_number_is_kept():
    # acceptance-06 §9 F58, S:88–89
    out = seed_draft.block_next(["ждать · да designer · 08 на ready-v3 → management-18 · ручка"])
    assert out == ["- ждать", "- да designer · 08 на ready-v3 → management-18", "- ручка"]


def test_verb_number_or_date_before_number_still_splits():
    assert seed_draft.block_next(["шаг 1 · шаг 2"]) == ["- шаг 1", "- шаг 2"]
    assert seed_draft.block_next(["проверить · 10 тестов"]) == ["- проверить", "- 10 тестов"]
    assert seed_draft.block_next(["сдать F57 · 12.10 сверка"]) == \
        ["- сдать F57", "- 12.10 сверка"]


def test_old_name_cases_unchanged():
    assert seed_draft.block_next(["написать designer · 02 (tide-stack-20) · проверить"]) == \
        ["- написать designer · 02 (tide-stack-20)", "- проверить"]
    assert seed_draft.block_next(["спросить designer · 06 = app-development-22 · ждать"]) == \
        ["- спросить designer · 06 = app-development-22", "- ждать"]

