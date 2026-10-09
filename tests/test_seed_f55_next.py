"""SOC-101: «имя · NN» не рвётся в «следующем шаге»; F55 — топик в карте входа."""
from __future__ import annotations

from tide.launcher import seed_draft


# --- «слово · NN» + дата/союз — имя, не разделитель -------------------------------

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


def test_new_action_after_number_still_splits():
    assert seed_draft.block_next(["прогнать тесты · 3 шага дописать"]) == \
        ["- прогнать тесты", "- 3 шага дописать"]
    assert seed_draft.block_next(["шаг 1 · шаг 2"]) == ["- шаг 1", "- шаг 2"]


def test_old_name_cases_unchanged():
    assert seed_draft.block_next(["написать designer · 02 (tide-stack-20) · проверить"]) == \
        ["- написать designer · 02 (tide-stack-20)", "- проверить"]
    assert seed_draft.block_next(["спросить designer · 06 = app-development-22 · ждать"]) == \
        ["- спросить designer · 06 = app-development-22", "- ждать"]
