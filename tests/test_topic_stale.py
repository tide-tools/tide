"""SOC-105: метка «страница топика отстала» — в сиде и в шов-синке.

Время подставляется явно (пульсы в context, «сверено» на странице): системные часы
не участвуют.
"""
from __future__ import annotations

from datetime import datetime

from tide import seam_sync as ss
from tide.arc import stream
from tide.launcher import seed_draft as sd

NAME = "02-peredacha-raboty"
PULSE = "- 2026-10-09T20:00:00 — пульс: довожу шов-синк"


def _page(root, checked, thread):
    d = root / ".tide" / "topics" / NAME
    d.mkdir(parents=True, exist_ok=True)
    body = ("# {0}\n\nkind: topic\nдом:        tide-stack, нить {1}\n\n"
            "Гриша ждёт: передача работы чёткая.\n").format(NAME, thread)
    if checked is not None:
        body += "сверено:    {0}\n".format(checked)
    (d / "topic.md").write_text(body, encoding="utf-8")
    return d / "topic.md"


def _block(tmp_project, checked, context=(PULSE,), name="handoffs"):
    thread = stream.new_thread(tmp_project, name, goal="цель")
    _page(tmp_project, checked, thread.name)
    return sd.topic_block(tmp_project, thread, context=list(context))


def _stale(lines):
    return [ln for ln in lines if "страница топика отстала" in ln]


def test_fresh_page_has_no_mark(tmp_project):
    # сверено за 1 ч 30 мин до пульса — в пределах порога
    assert _stale(_block(tmp_project, "2026-10-09 18:30", name="fresh")) == []
    # ровно на пороге — ещё не отстала
    assert _stale(_block(tmp_project, "2026-10-09 18:00", name="edge")) == []
    assert sd.TOPIC_STALE_HOURS == 2


def test_stale_page_says_hours_and_what_to_do(tmp_project):
    lines = _block(tmp_project, "2026-10-09 15:58")
    [mark] = _stale(lines)
    assert mark == ("  - топик: 02-peredacha-raboty — страница топика отстала на 4 ч — "
                    "освежи «где мы / в полёте / сверено»")
    # метка стоит сразу под строкой с датой правки, выжимка страницы не потеряна
    at = lines.index(mark)
    assert lines[at - 1].startswith("  - топик: 02-peredacha-raboty — страница правлена ")
    assert any("Гриша ждёт — передача работы чёткая." in ln for ln in lines)


def test_never_checked_page_is_marked_not_checked(tmp_project):
    [mark] = _stale(_block(tmp_project, None))
    assert "не сверялась" in mark and "освежи «где мы / в полёте / сверено»" in mark


def test_no_pulse_no_mark(tmp_project):
    assert _stale(_block(tmp_project, "2026-10-01 10:00", context=[])) == []
    assert _stale(_block(tmp_project, None, name="nostamp",
                         context=["- без штампа — просто строка"])) == []
    # блок без context (как зовут старые вызовы) тоже молчит
    thread = stream.new_thread(tmp_project, "other", goal="цель")
    _page(tmp_project, None, thread.name)
    assert _stale(sd.topic_block(tmp_project, thread)) == []


def test_date_only_is_end_of_day_and_unreadable_is_silent(tmp_project, tmp_path):
    f = tmp_path / "topic.md"
    f.write_text("сверено: 2026-10-09\n", encoding="utf-8")
    assert sd.topic_stale_line(f, datetime(2026, 10, 10, 1, 0)) == ""
    assert "отстала на 3 ч" in sd.topic_stale_line(f, datetime(2026, 10, 10, 3, 0))
    f.write_text("сверено: вчера вечером\n", encoding="utf-8")
    assert sd.topic_stale_line(f, datetime(2026, 10, 12)) == ""
    # порог — параметр: на 3 ч отставшая при пороге 4 ч молчит
    f.write_text("сверено: 2026-10-09 12:00\n", encoding="utf-8")
    assert sd.topic_stale_line(f, datetime(2026, 10, 9, 15), hours=4) == ""
    assert "отстала на 3 ч" in sd.topic_stale_line(f, datetime(2026, 10, 9, 15), hours=2)


SEED = """## следующий шаг

- довести шов-синк

## карта входа

- /a/b — читать
- топик: /x/02-peredacha-raboty/topic.md · Linear-родитель SOC-117 — Backlog
  - топик: 02-peredacha-raboty — страница правлена 2026-10-09 15:58
  - топик: 02-peredacha-raboty — страница топика отстала на 4 ч — освежи «где мы / в полёте / сверено»
  - топик: Гриша ждёт — передача работы чёткая.
"""

PASSPORT = """# 02-handoffs-2

## context
- 2026-10-09T18:04:36 — нить принята — шов приёма закрыт механикой
{0}
"""


def test_mark_becomes_seam_sync_item():
    items = ss.seed_items(SEED)
    assert items["topic"] == [
        "02-peredacha-raboty — страница топика отстала на 4 ч — "
        "освежи «где мы / в полёте / сверено»"]
    assert items["next"] == ["довести шов-синк"]
    # свежая страница — пункта нет
    assert ss.seed_items(SEED.replace("страница топика отстала", "страница ок"))["topic"] == []


def test_item_missing_then_named_then_dropped():
    # не названо и не снято — расхождение, видно в списке
    rep = ss.reconcile(SEED, PASSPORT.format("- 2026-10-09T18:10:00 — довести шов-синк"), n=3)
    row = next(r for r in rep["items"] if r["block"] == "topic")
    assert row["status"] == "missing" and row["label"] == "топик"
    assert rep["gaps"] == 1
    assert "- [топик] 02-peredacha-raboty — страница топика отстала на 4 ч" in ss.render(rep)
    # «беру» — новая назвала страницу топика
    named = ss.reconcile(SEED, PASSPORT.format(
        "- 2026-10-09T18:10:00 — беру: освежаю страницу топика 02-peredacha-raboty"), n=3)
    assert next(r for r in named["items"] if r["block"] == "topic")["status"] == "named"
    # «снимаю: … — почему»
    gone = ss.reconcile(SEED, PASSPORT.format(
        "- 2026-10-09T18:10:00 — снимаю: страница топика — её ведёт main, не наша нить"), n=3)
    row = next(r for r in gone["items"] if r["block"] == "topic")
    assert row["status"] == "dropped" and "main" in row["why"]
