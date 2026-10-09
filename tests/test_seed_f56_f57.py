"""SOC-101 пакет Ж F56/F57 — холодный аудит context-keeper.

F56: «открытые хвосты» сверяются с «в работе прямо сейчас» — «— нет» при
незакрытых поручениях было ложью. F57: устаревший адрес менеджера в «главном»
(plan.md нити) не помечался, а происхождение «через менеджера management-9» —
помечалось.
"""

from __future__ import annotations

from tide.handoff_contract import TAILS_NONE
from tide.launcher import seed_draft

ORDER = ("- поручено мне — от management-16 в 14:19: management-16 → context-keeper · 03. "
         "Слово Гриши tg 3372: «То, что рекомендуешь, давай возьмём»")
WORKER = ("- воркер «шаг 2 truth» — жив в старой сессии (с 14:40), итог перешлёт старая; "
          "адрес: SendMessage a1 из старой, task-id a1")


# --- F56 -------------------------------------------------------------------------

def test_open_orders_and_workers_become_tails():
    got = seed_draft.tails_from_inflight(
        [ORDER, WORKER, "- (полные тексты входящих — транскрипт /t.jsonl)"])
    assert len(got) == 2
    assert got[0].startswith("- поручение от management-16 (14:19) не закрыто: Слово Гриши")
    assert "см. «в работе прямо сейчас»" in got[0]
    assert got[1] == ("- воркер «шаг 2 truth» жив в старой сессии — "
                      "см. «в работе прямо сейчас»")


def test_tails_dash_only_when_empty_everywhere():
    none = [TAILS_NONE, "(хвостов нет — так сказано в пульсе 14:00)"]
    merged = seed_draft.merge_tails(none, [ORDER])
    assert TAILS_NONE not in merged and len(merged) == 1
    assert seed_draft.merge_tails([], [ORDER]) == seed_draft.tails_from_inflight([ORDER])
    named = ["- красный тест X (маркер в пульсе)"]
    assert seed_draft.merge_tails(named, [ORDER])[0] == named[0]
    assert seed_draft.merge_tails(none, []) == none
    assert seed_draft.merge_tails([], ["- ничего не в полёте"]) == []


def test_rendered_seed_has_no_dash_with_open_orders():
    bodies = {seed_draft.TAILS: seed_draft.merge_tails([], [ORDER]),
              seed_draft.INFLIGHT: [ORDER]}
    text = seed_draft.render_draft(bodies)
    tails = text.split("## " + seed_draft.TAILS, 1)[1].split("\n## ", 1)[0]
    assert TAILS_NONE not in tails and "поручение от management-16" in tails


# --- F57 -------------------------------------------------------------------------

def test_main_layer_handover_line_is_marked():
    line = ("- Кому сдавать: текущий менеджер — адрес в ~/.tide/manager.json (на 09.10 — "
            "management-12, «management — main · 02»). Замершим (management-9) не писать.")
    got = seed_draft.mark_stale_manager([line], current="management-17")
    assert got == [line + " (устарело: менеджер сейчас management-17)"]
    # «management-12 (main · 02)» и «менеджер — management-12» — тоже адрес
    for ln in ("- менеджер — management-12", "- сдаём management-12 (main · 02)"):
        assert seed_draft.mark_stale_manager([ln], current="management-17")[0].endswith(
            "(устарело: менеджер сейчас management-17)")
    # повторно не помечается
    assert seed_draft.mark_stale_manager(got, current="management-18") == got


def test_provenance_forms_stay_unmarked():
    lines = ["- 09.10 (через менеджера management-9, постановка нити): соткать документ",
             "- 09.10 (TG, через management-9): «пусть начнёт с памяти»",
             "- 09.10 ~05:05 (окно менеджера management-9, цитатой): «там где меня достать»",
             "- слово Гриши от management-16: «да»"]
    assert seed_draft.mark_stale_manager(lines, current="management-17") == lines

