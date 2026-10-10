"""SOC-143 F74 и F77 (acceptance-08 §9 пп. 5 и 8, пара 15 board · 05 → 06).

F74 — хвост «plan.md нити без «главное»» остался в сиде, хотя `thread layer --main`
того же шва вписал «главное» за 8 с до сборки. Хвост про пустой/старый слой
сверяется со слоем на момент сборки; закрытый — «снято: … — причина: …».

F77 — пункт «ждёт Гришу» входного сида («тап по tg-ссылке, tg 3409») пропал молча:
метка «Ждёт Гришу:» — состояние свежего пульса. Теперь пункты наследуются, как
опыт (F22) и решения (F63); снятые пульсом — с причиной.
Сеть не трогается.
"""

from __future__ import annotations

import os
import time

from tide.launcher import reception, seed_draft


# --- F74 --------------------------------------------------------------------

CTX = [
    "- 2026-10-09T16:57:00 — пульс",
    "- 2026-10-09T19:12:51 — ПЕРЕДАЧА. Хвосты: у 33 из 49 задач нет «Итог:»; "
    "plan.md нити без «главное»",
]


def _thread(tmp_path, main_lines=(), goal_where=""):
    t = tmp_path / "29-@board"
    t.mkdir()
    body = "\n".join("- " + ln for ln in main_lines) or "- <SOC-N — задача Linear нити>"
    (t / "plan.md").write_text("# план\n\n## главное — читать первым\n\n{0}\n\n## шаги\n"
                               .format(body), encoding="utf-8")
    (t / "board-goal.md").write_text(
        "# board\n\n## where we are\n\n### где мы\n\n{0}\n".format(
            goal_where or "<сделано · идёт · ждёт Гришу — по строке на каждое>"),
        encoding="utf-8")
    return t


def _stamp(path, iso):
    from datetime import datetime
    ts = time.mktime(datetime.fromisoformat(iso).timetuple())
    os.utime(path, (ts, ts))


TAILS = ["- у 33 из 49 задач нет «Итог:» (пульс 19:12)",
         "- plan.md нити без «главное» (пульс 19:12)"]


def test_f74_empty_main_tail_closed_when_layer_filled(tmp_path):
    t = _thread(tmp_path, ["SOC-112", "сдавать management-19", "не дублировать main"])
    _stamp(t / "plan.md", "2026-10-09T19:13:02")
    out = seed_draft.close_layer_tails(TAILS, t, CTX)
    assert out[0] == TAILS[0]  # не про слой — как есть
    assert out[1].startswith("- снято: plan.md нити без «главное» (пульс 19:12) — причина: "
                             "слой «главное» вписан")
    assert "19:13:02" in out[1] and str(t / "plan.md") in out[1]


def test_f74_empty_main_tail_kept_when_layer_still_empty(tmp_path):
    t = _thread(tmp_path)
    assert seed_draft.close_layer_tails(TAILS, t, CTX) == TAILS


def test_f74_unrelated_tail_with_bez_is_not_touched(tmp_path):
    t = _thread(tmp_path, ["a", "b", "c"], goal_where="- что-то")
    tails = ["- слой без штампов датируется по правке goal-файла (пульс 18:47)",
             "- 2 теста без PYTHONPATH падают (пульс 19:12)"]
    assert seed_draft.close_layer_tails(tails, t, CTX) == tails


def test_f74_stale_layer_closed_only_if_edited_after_pulse(tmp_path):
    t = _thread(tmp_path, goal_where="- сделано X")
    tails = ["- слой «где мы» устарел (пульс 19:12)"]
    _stamp(t / "board-goal.md", "2026-10-09T19:00:00")
    assert seed_draft.close_layer_tails(tails, t, CTX) == tails
    _stamp(t / "board-goal.md", "2026-10-09T19:13:02")
    out = seed_draft.close_layer_tails(tails, t, CTX)
    assert out[0] == "— нет"
    assert out[1].startswith("- снято: слой «где мы» устарел") and "правлен после пульса" in out[1]


def test_f74_all_closed_reads_none_and_reception_skips(tmp_path):
    t = _thread(tmp_path, ["a", "b", "c"])
    out = seed_draft.close_layer_tails(TAILS[1:], t, CTX)
    assert out[0] == "— нет" and out[1].startswith("- снято:")
    seed = "## где мы\n\n- x\n\n## открытые хвосты\n\n{0}\n\n## опыт\n\n- y\n".format(
        "\n".join(out))
    assert reception.open_tails(seed) == []
    # «в работе» (F56) не съедает снятую строку
    merged = seed_draft.merge_tails(out, ["- воркер «w» — жив"])
    assert merged[0].startswith("- снято:") and merged[1].startswith("- воркер «w»")


# --- F77 --------------------------------------------------------------------

PREV = [
    "- да на безопасность пишущей ручки (tg 3399), тап по тестовой ссылке "
    "tg://openmessage?user_id=1&message_id=3393 (tg 3409) — через management-18 (пульс 16:43)",
    "- ручка ответа после да Гриши на tg 3399 (из следующего шага)",
    "- SOC-127 на проверке (In Review) — предлагаю: посмотреть и сказать слово через "
    "менеджера; Done ставит проверяющий",
    "- нет (решения по SOC-143 — у менеджера management-19) (пульс 18:00)",
    "- выбрать шрифт заголовков доски (пульс 16:43)",
]
P_CTX = [
    "- 2026-10-09T17:55:01 — Грише ушли tg 3539 (канал один, окон два) и ❓ 3540 "
    "«включаем ответы с доски?» — заменяет 3399; ответ перешлёт management-19",
    "- 2026-10-09T19:12:51 — ПЕРЕДАЧА. Ждёт Гришу: ❓ 3540 включать ли ответы с доски",
]
CUR = ["- ❓ 3540 включать ли ответы с доски (пульс 19:12)"]


def test_f77_item_carries_over_partially_revoked():
    out = seed_draft.inherit_awaiting(PREV, CUR, P_CTX)
    assert len(out) == 2  # производные, «на проверке» и «нет» не наследуются
    first = out[0]
    assert "(tg 3409)" in first and "(из прошлого сида; снято: tg 3399 — «" in first
    assert "заменяет 3399» — пульс 17:55)" in first
    assert out[1] == "- выбрать шрифт заголовков доски (пульс 16:43) (из прошлого сида)"
    # переходит дальше без второй пометки, снятый номер помнится
    again = seed_draft.inherit_awaiting(out, CUR, [])
    assert again == out


def test_f77_fully_revoked_goes_with_reason():
    ctx = P_CTX + ["- 2026-10-09T19:00:00 — tg 3409 снимаю: ссылку Гриша открыл сам"]
    out = seed_draft.inherit_awaiting(PREV[:1], CUR, ctx)
    assert out[0].startswith("- снято: да на безопасность пишущей ручки")
    assert "tg 3399 — «" in out[0] and "tg 3409 — «tg 3409 снимаю" in out[0]


def test_f77_revoke_without_numbers_by_words():
    ctx = ["- 2026-10-09T19:00:00 — шрифт заголовков доски снимаю — выбрал дизайнер"]
    out = seed_draft.inherit_awaiting(PREV[4:], [], ctx)
    assert out[0].startswith("- снято: выбрать шрифт заголовков доски")


def test_f77_not_duplicated_when_current_has_it():
    cur = CUR + ["- тап по tg-ссылке (tg 3409) (пульс 19:12)"]
    assert seed_draft.inherit_awaiting(PREV[:1], cur + ["- да по tg 3399"], []) == []
    # 3409 уже здесь, 3399 снят пульсом — строка уходит «снято» с обеими причинами
    out = seed_draft.inherit_awaiting(PREV[:1], cur, P_CTX)
    assert out[0].startswith("- снято: да на безопасность") and \
        "tg 3409 — уже в «ждёт Гришу» этого сида" in out[0]
    assert seed_draft.inherit_awaiting(
        PREV[4:], ["- выбрать шрифт заголовков доски (пульс 19:12)"], []) == []


def test_f77_done_in_linear_dropped_by_existing_logic():
    line = seed_draft.inherit_awaiting(
        ["- принять SOC-107 и поставить Done (пульс 16:43)"], [], [])
    assert line and seed_draft.drop_resolved(
        line, {"SOC-107": ("Done", "completed")}) == []


def _seed(awaiting):
    return "## главное\n\n- x\n\n## ждёт Гришу\n\n{0}\n\n## следующий шаг\n\n- y\n".format(
        "\n".join(awaiting))


def test_f77_reception_reports_dropped_awaiting(tmp_path, monkeypatch):
    prev = _seed(PREV)
    assert seed_draft.dropped_awaiting(prev, _seed(CUR)) == [
        seed_draft._bare(PREV[0]), seed_draft._bare(PREV[4])]
    kept = _seed(CUR + seed_draft.inherit_awaiting(PREV, CUR, P_CTX))
    assert seed_draft.dropped_awaiting(prev, kept) == []

    origin = tmp_path / "old"
    (origin / "input").mkdir(parents=True)
    (origin / "input" / "handoff-seed.md").write_text(prev, encoding="utf-8")
    import tide.launcher.handoff as handoff
    monkeypatch.setattr(handoff, "_origin_entry", lambda home, rec: origin)
    got = reception._lost_decisions(tmp_path, {}, _seed(CUR))
    assert got and got[0].startswith("«ждёт Гришу» прошлого сида: выпало 2")
    assert reception._lost_decisions(tmp_path, {}, kept) == []
