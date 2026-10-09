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
    assert und["status"] == ha.PARTIAL and und["goal"] == "собрать аудит сида"
    assert und["missing"] == ["первого пульса принявшей сессии нет"]


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
