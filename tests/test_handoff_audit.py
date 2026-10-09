"""SOC-101 п.3: `tide handoffs audit <id>` — what a handoff seed lacks against the
prior session passport, the thread (decisions, where we are, dialog.md) and the
understood goal recorded at reception."""

from __future__ import annotations

import json

from tide import cli, fields, handoff_queue as hq
from tide.launcher import handoff_audit as ha

CREATED = "2026-10-09T12:00:00"

PRIOR = """# 01-main

title: p — main · 01
goal: вести нить
status: active

## summary
итог

## cursor — resume here
чиню аудит хендоффов

## context
- 2026-10-09T10:00:00 — нить принята — шов приёма закрыт механикой
- 2026-10-09T10:05:00 — решил класть понятую цель в оффер
- 2026-10-09T10:10:00 — Длинная строка пульса про сид. Хвост с деталями, путями и прочим, который сжатый сид не несёт целиком никогда
- 2026-10-09T10:20:00 — потерянная строка, которой в сиде нет
- 2026-10-09T13:00:00 — после оффера: в сид не могла попасть
## next
собрать аудит · показать Грише
"""

SEED = """# автохендофф

## курсор

чиню аудит хендоффов
- 2026-10-09T10:05:00 — решил класть понятую цель в оффер

## дальше

- собрать аудит

## решения

- 01 — первое решение (not done)
- 03 — третье (not done)

## опыт

- урок про хуки

## где мы — нить

### ожидание Гриши
надёжный хендофф

## журнал сессии — старше курсора

- 10:10 — Длинная строка пульса про сид.

## хвост диалога

- 2026-10-09T11:00:00 · Гриша · tg · где хендофф?
"""

THREAD_WHERE = """
## where we are
### ожидание Гриши — к чему ведём
надёжный хендофф
### где мы — 9 окт
идёт аудит сида
### опыт — уроки
- урок про хуки
- урок, который потерялся
"""

DECISIONS = """# decisions

## 01 — first
status: accepted
what: первое решение

## 02 — second
status: accepted
what: второе решение, забытое сидом

## 03 — third
status: accepted
what: третье

## 04 — later
status: accepted
what: принято после оффера
"""

DIALOG = """# диалог нити

- 2026-10-09T11:00:00 · Гриша · tg · где хендофф?
- 2026-10-09T11:30:00 · Гриша · ao · реплика, которой нет в сиде
- 2026-10-09T12:30:00 · Гриша · ao · после оффера
"""


def _world(tmp_control_home):
    from tide import roster
    from tide.arc import stream
    from tide.init_home import scaffold_project

    proj = tmp_control_home / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    roster.add(tmp_control_home, "proj", str(proj))
    thread = stream.new_thread(proj, "main", goal="надёжный хендофф")
    prior = stream.new_session(proj, "main", "main")
    (prior / "arc.md").write_text(PRIOR, encoding="utf-8")
    sess = stream.new_session(proj, "main", "main-2", from_ref=prior.name)
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text(SEED, encoding="utf-8")
    goal = stream.passport_path(thread)
    goal.write_text(goal.read_text(encoding="utf-8").split("## where we are")[0]
                    + THREAD_WHERE, encoding="utf-8")
    (thread / "decisions.md").write_text(DECISIONS, encoding="utf-8")
    (thread / "dialog.md").write_text(DIALOG, encoding="utf-8")
    path = hq.offer(tmp_control_home, "main", project="proj", seed=str(seed),
                    arc="{0}/{1}".format(thread.name, sess.name))
    hq._set_field(path, "created", CREATED)
    return sess


def _by_key(report):
    return {s["key"]: s for s in report["sections"]}


def test_audit_names_what_the_seed_lacks(tmp_control_home):
    sess = _world(tmp_control_home)
    rep = ha.audit(tmp_control_home, "main")
    assert rep["prior"].endswith("01-main")
    s = _by_key(rep)
    assert [k for k, _t in ha.AUDIT_SECTIONS] == [x["key"] for x in rep["sections"]]
    assert s["cursor"]["status"] == ha.OK
    ctx = s["context"]   # mechanical pulse and the post-offer line don't count
    assert (ctx["total"], ctx["present"], ctx["compressed"]) == (3, 1, 1)
    assert ctx["missing"][0].endswith("потерянная строка, которой в сиде нет")
    assert s["next"]["missing"] == ["показать Грише"]
    dec = s["decisions"]
    assert dec["missing"] == ["- 02 — второе решение, забытое сидом"] and dec["after"] == 1
    assert s["experience"]["missing"] == ["- урок, который потерялся"]
    assert s["expectation"]["missing"] == ["идёт аудит сида"]
    assert s["dialog"]["missing"] == [
        "- 2026-10-09T11:30:00 · Гриша · ao · реплика, которой нет в сиде"]
    und = s["understood"]
    assert und["status"] == ha.EMPTY   # offer not taken yet

    fields.set_field(sess / "arc.md", "goal", "собрать аудит сида")
    hq.take(tmp_control_home, "main", session="sid-x")
    und = _by_key(ha.audit(tmp_control_home, "main"))["understood"]
    # F35: goal паспорта — эхо сида; пока сессия не сказала своё, цели нет
    assert und["status"] == ha.MISSING and und["goal"] is None
    assert und["missing"] == ["цель принявшей сессии не записана",
                              "первого пульса принявшей сессии нет"]


def test_cli_human_and_json(tmp_control_home, monkeypatch, capsys):
    _world(tmp_control_home)
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    assert cli.main(["handoffs", "audit", "main"]) == 0
    out = capsys.readouterr().out
    assert "≈ сырой журнал: context-строки — 1/3, сжато 1" in out
    assert "нет: показать Грише" in out
    assert "ещё 1 новее сида" in out
    assert cli.main(["handoffs", "audit", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["offer"].endswith("main") and len(data["sections"]) == len(ha.AUDIT_SECTIONS)


def test_audit_unknown_offer_or_missing_seed(tmp_control_home, monkeypatch, capsys):
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    assert cli.main(["handoffs", "audit", "ghost"]) == 1
    hq.offer(tmp_control_home, "noseed", arc="-", project="p", seed="-")
    assert cli.main(["handoffs", "audit", "noseed"]) == 1
    assert "нет сида" in capsys.readouterr().err


def _append_context(passport, *lines):
    text = passport.read_text(encoding="utf-8")
    head, sep, tail = text.partition("## context\n")
    if not sep:
        text = text.rstrip() + "\n\n## context\n" + "\n".join(lines) + "\n"
    else:
        text = head + sep + "\n".join(lines) + "\n" + tail
    passport.write_text(text, encoding="utf-8")


def test_first_pulse_is_the_very_first_real_one_after_take(tmp_control_home):
    """Живой аудит 09.10: оффер 227 принят в 04:34, а «первым пульсом» стоял пульс
    05:00 — первый после свежего коммита. Нужен самый первый настоящий."""
    sess = _world(tmp_control_home)
    hq.take(tmp_control_home, "main", session="sid-y")
    rec = hq.list_offers(tmp_control_home)[0]
    taken = rec["taken_at"]
    # штамп оффера — поздний пульс (как на живом аудите)
    hq._set_field(rec["path"], hq.FIRST_PULSE, "поздний пульс после коммита")
    hq._set_field(rec["path"], hq.FIRST_PULSE_AT, "2099-01-01T00:00:00")
    _append_context(sess / "arc.md",
                    "- 2000-01-01T00:00:00 — до приёма: не в счёт")
    text = (sess / "arc.md").read_text(encoding="utf-8")
    later = "- {0} — старт-гейт: цель — сверить сид".format("2099-01-01T00:00:01")
    (sess / "arc.md").write_text(text.rstrip() + "\n" + later + "\n", encoding="utf-8")
    found = hq.first_real_pulse(sess / "arc.md", taken)
    assert found == ("старт-гейт: цель — сверить сид", "2099-01-01T00:00:01")
    und = _by_key(ha.audit(tmp_control_home, "main"))["understood"]
    assert und["first_pulse"] == "старт-гейт: цель — сверить сид"
    assert und["first_pulse_at"] == "2099-01-01T00:00:01"


def test_first_pulse_skips_mechanical_reception_line(tmp_path):
    p = tmp_path / "arc.md"
    p.write_text("# x\n\n## context\n"
                 "- 2026-10-09T04:34:26 — нить принята — шов приёма закрыт механикой\n"
                 "- 2026-10-09T04:34:55 — старт-гейт: цель ясна\n"
                 "- 2026-10-09T05:00:24 — поздний\n"
                 "## next\nx\n", encoding="utf-8")
    assert hq.first_real_pulse(p, "2026-10-09T04:34:26") == (
        "старт-гейт: цель ясна", "2026-10-09T04:34:55")
    assert hq.first_real_pulse(p, "2026-10-09T05:00:00")[0] == "поздний"
    assert hq.first_real_pulse(p, "2026-10-10T00:00:00") is None


def test_note_first_pulse_stamps_earliest_pulse_from_disk(tmp_control_home):
    """Механика стампа: если до первого дошедшего до кода пульса были другие —
    штампуется самый ранний из паспорта, с его временем."""
    sess = _world(tmp_control_home)
    hq.take(tmp_control_home, "main", session="sid-z")
    text = (sess / "arc.md").read_text(encoding="utf-8").rstrip()
    (sess / "arc.md").write_text(
        text + "\n- 2099-01-01T00:00:01 — ранний пульс\n"
        "- 2099-01-01T00:00:09 — текущий пульс\n", encoding="utf-8")
    rec = hq.note_first_pulse(tmp_control_home, sess, note="текущий пульс")
    assert rec["first_pulse"] == "ранний пульс"
    assert rec["first_pulse_at"] == "2099-01-01T00:00:01"


# --- ручной сид пересказом (SOC-101 п.1б) ------------------------------------
# Живая пара nochnaya-smena: 03-@main/01-session → 02-release (оффер 231,
# 09.10). Сид написан руками своими заголовками (финал/курсор/дальше/…) и
# пересказывает пульс; аудит видел в нём «missing» везде.

from pathlib import Path  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "handoff_audit"


def _hand_world(tmp_control_home):
    from tide import roster
    from tide.arc import stream
    from tide.init_home import scaffold_project

    proj = tmp_control_home / "ns"
    proj.mkdir()
    scaffold_project(proj, name="ns")
    roster.add(tmp_control_home, "ns", str(proj))
    thread = stream.new_thread(proj, "main", goal="выпуск v0.13.8")
    prior = stream.new_session(proj, "main", "session")
    (prior / "arc.md").write_text(
        (FIX / "nochnaya-smena-1-arc.md").read_text(encoding="utf-8"), encoding="utf-8")
    sess = stream.new_session(proj, "main", "release", from_ref=prior.name)
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text((FIX / "nochnaya-smena-2-seed.md").read_text(encoding="utf-8"),
                    encoding="utf-8")
    path = hq.offer(tmp_control_home, "release", project="ns", seed=str(seed),
                    arc="{0}/{1}".format(thread.name, sess.name))
    hq._set_field(path, "created", "2026-10-09T04:48:20")
    return sess


def test_hand_written_seed_is_not_missing(tmp_control_home):
    _hand_world(tmp_control_home)
    s = _by_key(ha.audit(tmp_control_home, "release"))
    ctx = s["context"]
    assert ctx["status"] == ha.PARTIAL and ctx["retold"] >= 4 and not ctx["missing"]
    nxt = s["next"]
    assert nxt["status"] == ha.OK and nxt["present"] + nxt["retold"] == 3
    # «Оба воркера сдали; делаю хендофф» — в сиде «Воркеров в полёте нет»:
    # похоже, но не наверняка — «не распознан», а не «нет»
    cur = s["cursor"]
    assert cur["status"] == ha.UNSURE and not cur["missing"] and cur["unsure"]
    assert all(x["status"] != ha.MISSING for x in s.values())


def test_hand_seed_render_says_retold_and_unrecognized(tmp_control_home, monkeypatch,
                                                       capsys):
    _hand_world(tmp_control_home)
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    assert cli.main(["handoffs", "audit", "release"]) == 0
    out = capsys.readouterr().out
    assert "пересказом" in out and "не распознано: Оба воркера сдали" in out
    assert "✗" not in out


def test_content_match_keeps_a_foreign_seed_missing():
    """Пересказ узнаётся по содержанию, но чужой сид на том же источнике — нет."""
    prior = (FIX / "nochnaya-smena-1-arc.md").read_text(encoding="utf-8")
    line = [ln for ln in prior.splitlines() if "ВОРКЕР A" in ln][0]
    own = (FIX / "nochnaya-smena-2-seed.md").read_text(encoding="utf-8")
    assert ha.coverage(line, ha.tokens(own)) >= ha.RETOLD
    foreign = SEED  # сид tide из этого же файла — про другое
    assert ha.audit_section("context", "t", [line], foreign)["status"] == ha.MISSING


def test_contract_synonyms_cover_hand_headings():
    from tide import handoff_contract as hc

    heads = ["финал", "курсор", "дальше", "решения", "опыт", "карта входа", "окружение"]
    assert set(hc.missing(heads)) == {
        "главное", "задачи Linear", "ждёт Гришу", "в работе прямо сейчас",
        "неотвеченные реплики Гриши", "сырой журнал"}
    assert hc.find_body({"что дальше": ["- x"]}, "next") == ["- x"]
    assert hc.find_body({"уроки": ["- y"]}, "experience") == ["- y"]
