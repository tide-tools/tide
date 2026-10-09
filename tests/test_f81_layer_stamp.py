"""SOC-143 F81 (acceptance-09 §9 п.2): штамп слоя не затирает время слов.

Живой случай пары 16: ``tide thread layer --expect "09.10 (TG, через менеджера) —
…"`` лёг строкой «09.10 19:22 — 09.10 (TG, через менеджера)»: 19:22 — время
записи, а 3573 сказано в 18:50, 3372 — в 14:16. Теперь текст, начатый датой,
штампа не получает, а у «tg N» время берётся из журнала пульта.
"""

from __future__ import annotations

import json
from datetime import datetime

from tide import fields
from tide.arc import dialog, stream, thread_layers as tl
from tests.test_autohandoff import world  # noqa: F401 — фикстура

NOW = datetime(2026, 10, 9, 19, 22)
TIMES = {3573: datetime(2026, 10, 9, 18, 50), 3372: datetime(2026, 10, 9, 14, 16)}
LIVE = ("09.10 (TG, через менеджера): «я вообще не хочу заглядывать в окна терминала» "
        "(tg 3573); «То, что рекомендуешь, давай возьмём» (tg 3372)")


def _layer(goal, head):
    lines = goal.read_text(encoding="utf-8").splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.lower().startswith("### " + head))
    out = []
    for ln in lines[i + 1:]:
        if ln.startswith("#"):
            break
        if ln.strip():
            out.append(ln)
    return out


def test_dated_text_gets_no_stamp_and_quotes_get_their_time(world):
    goal = stream.passport_path(world["thread"])
    tl.write_layers(goal, {"expectation": LIVE, "experience": "09.10 — грабли: x"},
                    now=NOW, tg_times=TIMES)
    exp = _layer(goal, "ожидание")[-1]
    assert not exp.startswith("- 09.10 19:22 —")  # было: «09.10 19:22 — 09.10 (TG…»
    assert exp.startswith("- 09.10 (TG, через менеджера)")
    assert "(tg 3573, 09.10 18:50)" in exp and "(tg 3372, 09.10 14:16)" in exp
    assert _layer(goal, "опыт")[-1] == "- 09.10 — грабли: x"
    # время записи слоя — в поле, свежесть по нему, а не по времени слов
    assert fields.read_field(goal, tl.EXPECT_AT_FIELD) == "2026-10-09T19:22:00"
    assert tl.freshness(world["thread"])["expect_at"] == NOW


def test_undated_quote_is_stamped_with_its_tg_time():
    line = tl.stamp_line("«я вообще не хочу…» (tg 3573); «давай возьмём» (tg 3372)",
                         NOW, TIMES)
    assert line == ("- 09.10 18:50 — «я вообще не хочу…» (tg 3573); «давай возьмём» "
                    "(tg 3372, 09.10 14:16)")


def test_no_journal_keeps_write_stamp_and_given_time_is_kept():
    assert tl.stamp_line("«x» (tg 3573)", NOW, {}) == "- 09.10 19:22 — «x» (tg 3573)"
    assert tl.stamp_line("«x» (tg 3573, 18:50)", NOW, TIMES) == \
        "- 09.10 19:22 — «x» (tg 3573, 18:50)"
    assert tl.stamp_line("«x» (tg ~3525)", NOW, TIMES) == "- 09.10 19:22 — «x» (tg ~3525)"
    assert tl.tg_refs("(tg 3573) (tg 3372, 14:16) tg ~3525") == [3573]


def test_where_keeps_write_stamp(world):
    goal = stream.passport_path(world["thread"])
    tl.write_layers(goal, {"where": "09.10 сделано: x"}, now=NOW, tg_times=TIMES)
    assert _layer(goal, "где мы") == ["- 09.10 19:22 — 09.10 сделано: x"]


def test_tg_times_from_pult_journal(tmp_path, monkeypatch):
    log = tmp_path / "dialog.jsonl"
    rows = [{"ts": "2026-10-09T21:50:57+00:00", "dir": "in", "kind": "tg",
             "tg_msg_id": 3573, "text": "Да я вообще не хочу…"},
            {"ts": "2026-10-09T21:51:00+00:00", "dir": "in", "kind": "queue",
             "tg_msg_ids": [3573]},
            {"ts": "2026-10-09T17:16:38+00:00", "dir": "out", "kind": "tg",
             "tg_msg_id": 3372}]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\nbroken\n", encoding="utf-8")
    monkeypatch.delenv(dialog.LOG_ENV, raising=False)
    monkeypatch.setenv(dialog.TG_LOG_ENV, str(log))
    got = dialog.tg_times([3573, 3372, 1])
    expect = datetime.fromisoformat("2026-10-09T21:50:57+00:00").astimezone().replace(
        tzinfo=None)
    assert got == {3573: expect}


def test_cmd_layer_reads_journal(world, tmp_path, monkeypatch, capsys):
    log = tmp_path / "dialog.jsonl"
    log.write_text(json.dumps({"ts": "2026-10-09T18:50:00", "dir": "in", "kind": "tg",
                               "tg_msg_id": 3573}) + "\n", encoding="utf-8")
    monkeypatch.delenv(dialog.LOG_ENV, raising=False)
    monkeypatch.setenv(dialog.TG_LOG_ENV, str(log))
    monkeypatch.chdir(world["proj"])
    args = type("A", (), {"target": "seam", "layer_expect": "«x» (tg 3573)"})()
    assert tl.cmd_layer(args) == 0
    goal = stream.passport_path(world["thread"])
    assert _layer(goal, "ожидание")[-1] == "- 09.10 18:50 — «x» (tg 3573)"
