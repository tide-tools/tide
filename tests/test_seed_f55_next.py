"""SOC-101: «имя · NN» не рвётся в «следующем шаге»; F55 — топик в карте входа."""
from __future__ import annotations

from pathlib import Path

from tide.arc import dialog, stream
from tide.launcher import autohandoff as ah, seed_draft


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


# --- F55: под «топик:» — дословно «Гриша ждёт» и «дальше» страницы -----------------

PAGE = """# 02-peredacha-raboty

kind: topic
вопрос:     чтобы работа передавалась
дом:        tide-stack, нить {thread}
linear:     SOC-117 https://l.example/SOC-117 (родитель топика)

## сейчас — чего ждёт Гриша
Гриша ждёт: передача работы чёткая — без потерь.
Гриша ждёт: передача работы с памятью — подгружается нужное.
Гриша ждёт: обкатано руками на живой работе.
Источник — слова Гриши 09.10.

где мы:     пакет Д
дальше:     пакет Д → вторая пара промера → SOC-101 в In Review
            вторая строка поля не нужна
сверено:    2026-10-09
"""


def _page(root, name, body):
    d = root / ".tide" / "topics" / name
    d.mkdir(parents=True)
    (d / "topic.md").write_text(body, encoding="utf-8")
    return d / "topic.md"


def test_digest_carries_waits_next_and_mtime(tmp_path):
    f = _page(tmp_path, "02-peredacha-raboty", PAGE.format(thread="31-@main"))
    out = seed_draft.topic_digest(f)
    assert out[0].startswith("  - топик: 02-peredacha-raboty — страница правлена 20")
    assert out[0].endswith(", сверено: 2026-10-09")
    assert out[1:] == [
        "  - топик: Гриша ждёт — передача работы чёткая — без потерь.",
        "  - топик: Гриша ждёт — передача работы с памятью — подгружается нужное.",
        "  - топик: Гриша ждёт — обкатано руками на живой работе.",
        "  - топик: дальше — пакет Д → вторая пара промера → SOC-101 в In Review"]


def test_digest_section_bullets_and_budget(tmp_path):
    body = "# t\n\n## что ждёт Гриша\n" + "".join(
        "- пункт {0}\n".format(i) for i in range(12)) + "\n## что дальше\n- шаг первый\n"
    out = seed_draft.topic_digest(_page(tmp_path, "05-t", body))
    assert len(out) == seed_draft.TOPIC_DIGEST_LINES
    assert out[1] == "  - топик: Гриша ждёт — пункт 0"
    assert out[-2].startswith("  - топик: … ещё 7 — в /")
    assert out[-1] == "  - топик: дальше — шаг первый"


def test_digest_without_waits_says_so_and_shows_question(tmp_path):
    out = seed_draft.topic_digest(_page(tmp_path, "03-d", "вопрос: чтобы видел\n"))
    assert out[1:] == ["  - топик: Гриша ждёт — на странице не записано; "
                       "вопрос топика: чтобы видел"]


def test_entry_map_drops_inherited_digest():
    kept = seed_draft.block_entry_map(
        ["- /a/b — читать", "- топик: /x/topic.md · …",
         "  - топик: Гриша ждёт — старое"])
    assert not any("топик" in ln for ln in kept)
    assert kept[0] == "- /a/b — читать"



FIX = Path(__file__).parent / "fixtures" / "handoff_seed"


def test_auto_seed_prints_digest_right_under_pointer(tmp_project, monkeypatch):
    """Холодная сессия видит «Гриша ждёт» и «дальше» топика в самом сиде, сразу под
    строкой «топик:» (autohandoff уносит «топик:» в конец карты — выжимка с ним)."""
    monkeypatch.delenv(dialog.LOG_ENV, raising=False)
    thread = stream.new_thread(tmp_project, "continuity-audit", goal="аудит")
    sess = stream.new_session(tmp_project, "continuity-audit", "s1", goal="план")
    (sess / "arc.md").write_text(
        (FIX / "continuity-audit-01-arc.md").read_text(encoding="utf-8"), encoding="utf-8")
    _page(tmp_project, "02-peredacha-raboty", PAGE.format(thread=thread.name))
    seed = ah.build_auto_seed(sess)
    emap = seed.partition("## карта входа\n\n")[2].partition("\n## ")[0].splitlines()
    at = [i for i, ln in enumerate(emap) if ln.startswith("- топик:")]
    assert len(at) == 1
    tail = emap[at[0] + 1:at[0] + 6]
    assert tail[0].startswith("  - топик: 02-peredacha-raboty — страница правлена ")
    assert tail[1] == "  - топик: Гриша ждёт — передача работы чёткая — без потерь."
    assert tail[4] == "  - топик: дальше — пакет Д → вторая пара промера → SOC-101 в In Review"
