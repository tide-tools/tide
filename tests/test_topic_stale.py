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
    # непарсимое: «отстала» не пишем, но и не молчим — одна строка человеческими словами
    line = sd.topic_stale_line(f, datetime(2026, 10, 12))
    assert "отстала" not in line and "сверено нечитаемо — проверь дату" in line
    assert "\n" not in line
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
        "- 2026-10-09T18:10:00 — снимаю: страница топика 02-peredacha-raboty — её ведёт main, "
        "не наша нить"), n=3)
    row = next(r for r in gone["items"] if r["block"] == "topic")
    assert row["status"] == "dropped" and "main" in row["why"]


# --- пункт 1: сверяем с пульсом нити-дома топика, не своей сессии -------------------

def _session(root, thread, pulse=None, offloaded=None):
    from tide.arc import stream as st
    s = st.new_session(root, thread.name, "main")
    text = s.joinpath("arc.md").read_text(encoding="utf-8")
    if offloaded:
        text = text.replace("offloaded-at: 0", "offloaded-at: " + offloaded, 1)
    if pulse:
        text = text.replace("## context\n", "## context\n{0}\n".format(pulse), 1)
    s.joinpath("arc.md").write_text(text, encoding="utf-8")
    return s


def test_home_thread_is_found_from_topic_field(tmp_project):
    home = stream.new_thread(tmp_project, "main", goal="цель")
    stream.new_thread(tmp_project, "other", goal="цель")
    f = _page(tmp_project, "2026-10-09 10:00", home.name)
    assert sd.topic_home_thread(tmp_project, f) == home
    # поле называет нить, которой нет, — дома нет
    f = _page(tmp_project, "2026-10-09 10:00", "99-@ghost")
    assert sd.topic_home_thread(tmp_project, f) is None


def test_stale_is_judged_against_home_thread_pulse_not_own(tmp_project):
    home = stream.new_thread(tmp_project, "main", goal="цель")
    mine = stream.new_thread(tmp_project, "keeper", goal="цель")
    _session(tmp_project, home, pulse="- 2026-10-09T20:00:00 — пульс дома")
    # новейшая сессия дома — старые пульсы вторых сессий не считаются
    _session(tmp_project, home, pulse="- 2026-10-09T21:00:00 — пульс дома, позже")
    page = _page(tmp_project, "2026-10-09 20:30", home.name)
    # топик связан и с моей нитью (threads:), а дом — main
    page.write_text(page.read_text(encoding="utf-8")
                    + "threads:    {0}\n".format(mine.name), encoding="utf-8")
    own_ctx = ["- 2026-10-09T20:40:00 — мой пульс, свежий"]
    # по моему пульсу страница свежа (10 мин), по пульсу дома 21:00 — отстала на 0,5 ч → порог
    assert _stale(sd.topic_block(tmp_project, mine, context=own_ctx)) == []
    _session(tmp_project, home, pulse="- 2026-10-09T23:10:00 — дом ушёл вперёд")
    [mark] = _stale(sd.topic_block(tmp_project, mine, context=own_ctx))
    assert "отстала на 3 ч" in mark   # 23:10 - 20:30 = 2 ч 40 мин


def test_home_pulse_uses_offloaded_at_and_falls_back_to_own(tmp_project):
    home = stream.new_thread(tmp_project, "main", goal="цель")
    mine = stream.new_thread(tmp_project, "keeper", goal="цель")
    _session(tmp_project, home, pulse="- 2026-10-09T18:00:00 — пульс", offloaded="2026-10-09T22:00:00")
    assert sd.thread_last_pulse(tmp_project, home).hour == 22
    page = _page(tmp_project, "2026-10-09 18:00", home.name)
    page.write_text(page.read_text(encoding="utf-8")
                    + "threads:    {0}\n".format(mine.name), encoding="utf-8")
    [mark] = _stale(sd.topic_block(tmp_project, mine, context=["- 2026-10-09T18:30:00 — свой"]))
    assert "отстала на 4 ч" in mark
    # дом без сессий (пульса нет) — откат на пульс своей сессии: 18:30 - 18:00 в пределах порога
    bare = stream.new_thread(tmp_project, "bare", goal="цель")
    page = _page(tmp_project, "2026-10-09 18:00", bare.name)
    assert sd.thread_last_pulse(tmp_project, bare) is None
    assert _stale(sd.topic_block(tmp_project, bare,
                                 context=["- 2026-10-09T18:30:00 — свой"])) == []
    # дом топика — нити нет в проекте (99-@ghost), а к моей он привязан через threads:
    thread = stream.new_thread(tmp_project, "lonely", goal="цель")
    page = _page(tmp_project, "2026-10-09 10:20", "99-@ghost")
    page.write_text(page.read_text(encoding="utf-8")
                    + "threads:    {0}\n".format(thread.name), encoding="utf-8")
    [mark] = _stale(sd.topic_block(tmp_project, thread,
                                   context=["- 2026-10-09T18:30:00 — свой"]))
    assert "отстала на 8 ч" in mark   # 18:30 - 10:20 = 8 ч 10 мин


# --- пункт 2: пункт «топик» закрывается только именем своей страницы ---------------

SEED2 = SEED + """  - топик: 03-doska — страница топика отстала на 5 ч — освежи «где мы / в полёте / сверено»
"""


def _rows(pulse):
    rep = ss.reconcile(SEED2, PASSPORT.format("- 2026-10-09T18:10:00 — " + pulse), n=3)
    return {r["text"].split(" — ")[0]: r["status"] for r in rep["items"] if r["block"] == "topic"}


def test_topic_item_closes_only_by_its_own_page_name():
    # чужое имя не закрывает вторую страницу
    assert _rows("беру: освежаю 03-doska") == {
        "02-peredacha-raboty": "missing", "03-doska": "named"}
    # общие слова без имени не закрывают ни одну
    assert _rows("беру: освежаю страницу топика") == {
        "02-peredacha-raboty": "missing", "03-doska": "missing"}
    # slug, имя без номера и «топик 02» — годятся
    assert _rows("беру: 02-peredacha-raboty")["02-peredacha-raboty"] == "named"
    assert _rows("беру: освежаю страницу peredacha-raboty")["02-peredacha-raboty"] == "named"
    rows = _rows("беру: освежаю топик 02")
    assert rows == {"02-peredacha-raboty": "named", "03-doska": "missing"}
    # номер сам по себе (время, чужие числа) не считается
    assert _rows("беру: правки в 19:02 и 102 строках")["02-peredacha-raboty"] == "missing"


def test_topic_drop_needs_the_page_name_too():
    rows = _rows("снимаю: страница топика — её ведёт main")
    assert rows == {"02-peredacha-raboty": "missing", "03-doska": "missing"}
    rows = _rows("снимаю: 03-doska — её ведёт board")
    assert rows == {"02-peredacha-raboty": "missing", "03-doska": "dropped"}


# --- пункт 3: «сверено» с запятой и пробелами -------------------------------------

def test_checked_accepts_comma_and_extra_spaces(tmp_path):
    want = datetime(2026, 10, 9, 19, 55)
    for raw in ("2026-10-09, 19:55", "2026-10-09 ,  19:55", "2026-10-09   19:55",
                "2026-10-09T19:55", "2026-10-09 в 19:55", "2026-10-09;19:55"):
        assert sd.topic_checked_at("сверено: " + raw + "\n") == want, raw
    # время не потеряно: сверено 19:55, пульс 22:00 — отстала на 2 ч 5 мин → «на 2 ч»
    f = tmp_path / "topic.md"
    f.write_text("сверено: 2026-10-09, 19:55\n", encoding="utf-8")
    assert sd.topic_stale_line(f, datetime(2026, 10, 9, 22, 0, 0)) != ""
    assert sd.topic_stale_line(f, datetime(2026, 10, 9, 21, 50)) == ""   # 1 ч 55 мин
    # только дата — по-прежнему конец дня
    assert sd.topic_checked_at("сверено: 2026-10-09\n") == datetime(2026, 10, 9, 23, 59)


def test_unreadable_checked_says_so_in_one_line(tmp_path):
    f = tmp_path / "02-x" / "topic.md"
    f.parent.mkdir()
    f.write_text("сверено: на днях\n", encoding="utf-8")
    line = sd.topic_stale_line(f, datetime(2026, 10, 9, 22, 0))
    assert line == "  - топик: 02-x — страница топика: сверено нечитаемо — проверь дату"
    # поля нет вовсе — это «не сверялась», а не «нечитаемо»
    f.write_text("без поля\n", encoding="utf-8")
    assert "не сверялась" in sd.topic_stale_line(f, datetime(2026, 10, 9, 22, 0))
    # и в шов-синк такая строка не попадает как «отстала»
    assert ss.topic_stale_items("- топик: 02-x — страница топика: сверено нечитаемо — проверь дату") == []
