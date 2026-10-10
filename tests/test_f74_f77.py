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
