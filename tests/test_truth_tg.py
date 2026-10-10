"""tide truth tg — тема Telegram проекта: ответы Гриши и открытые вопросы.

Всё на tmp_path: свой дом с ростером, свой каталог tg-threads (через
$TIDE_TG_THREADS); живые журналы пульта не трогаем.
"""

from __future__ import annotations

import json

import pytest

from tide import cli, truth_tg


def _row(**kw) -> dict:
    r = {"ts": "2026-10-10T04:49:42+00:00", "group": "factory", "topic_id": 1207,
         "slug": "groovamp"}
    r.update(kw)
    return r


def _out(tg, text, at, **kw):
    return _row(dir="out", kind="alert", tg_msg_ids=[tg], text=text, at=at, ts=at,
                soc=["SOC-162"], soc_src="default", **kw)


def _in(tg, text, at, **kw):
    return _row(dir="in", kind="tg", tg_msg_id=tg, text=text, at=at, ts=at, author="grisha",
                soc=["SOC-162"], soc_src="default", reply_to_tg=None, **kw)


# Как в живом groovamp.jsonl: 1263 пульт привязал к 1258 по времени, менеджер
# подтвердил ручной link; 1303 привязан к 1302 только по времени (а по смыслу — не о
# нём), пульт ещё и закрыл 1302 c how=loose; 1307 — честный реплай на 1306.
GROOVAMP = [
    _out(1258, "❓ Новый джем: подписи по-английски или по-русски?",
         "2026-10-10T04:49:42+00:00", q_state="open"),
    _in(1263, "Все на Английском", "2026-10-10T04:51:53+00:00", q_ref=1258, q_ref_how="loose"),
    _row(kind="q_state", tg_msg_id=1258, q_state="answered", answered_by=1263, how="loose"),
    _out(1301, "❓ Делаем анонс?", "2026-10-10T14:07:00+00:00", q_state="open"),
    _out(1302, "❓ Для замера на Android нужен твой телефон", "2026-10-10T14:07:01+00:00",
         q_state="open"),
    {"kind": "future_kind", "whatever": 1},  # неизвестное — пропускается
    "not json",
    _in(1303, "А как слои переключать", "2026-10-10T14:07:39+00:00", q_ref=1302,
        q_ref_how="loose"),
    _row(kind="q_state", tg_msg_id=1302, q_state="answered", answered_by=1303, how="loose"),
    _row(kind="edit", tg_msg_id=1303, text="А как слои переключать?"),
    _in(1305, "лишнее", "2026-10-10T14:08:00+00:00"),
    _row(kind="delete", tg_msg_id=1305),
    _row(kind="link", tg_msg_id=1263, soc=["SOC-162"], q_ref=1258, q_ref_how="link", by="m-24"),
    _row(kind="link", tg_msg_id=1303, soc=["SOC-170"], by="m-24"),  # только SOC, не вопрос
    _out(1306, "❓ Кнопка записи — красная?", "2026-10-10T14:08:30+00:00", q_state="open"),
    _row(dir="in", kind="tg", tg_msg_id=1307, text="Да, красная", at="2026-10-10T14:09:00+00:00",
         ts="2026-10-10T14:09:00+00:00", soc=["SOC-162"], soc_src="q_ref", reply_to_tg=1306,
         q_ref=1306, q_ref_how="reply"),
    _row(kind="q_state", tg_msg_id=1306, q_state="answered", answered_by=1307, how="reply"),
]


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".tide").mkdir(parents=True)
    roots = {n: tmp_path / n for n in ("music", "tide-stack", "management")}
    for r in roots.values():
        (r / ".tide").mkdir(parents=True)
    (home / "roster.md").write_text(
        "# tide roster\n" + "".join("{0} | {1}\n".format(n, r) for n, r in roots.items()),
        encoding="utf-8")
    d = tmp_path / "tg-threads"
    d.mkdir()
    index = {
        "factory:1207": {"slug": "groovamp", "name": "Groovamp", "project": "music",
                         "tide_thread": "music/main", "tide_topic": None,
                         "soc_default": ["SOC-162"]},
        "factory:1271": {"slug": "kontekst", "name": "Контекст", "project": "tide-stack",
                         "tide_thread": "tide-stack/context-keeper", "soc_default": ["SOC-165"]},
        "factory:1211": {"slug": "agenty", "name": "Агенты и контекст",
                         "project": "tide-stack", "tide_thread": "tide-stack/context-keeper",
                         "soc_default": []},
    }
    (d / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    (d / "groovamp.jsonl").write_text("\n".join(
        r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in GROOVAMP) + "\n",
        encoding="utf-8")
    (d / "agenty.jsonl").write_text("", encoding="utf-8")  # пустая тема; у kontekst файла нет
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv(truth_tg.THREADS_ENV, str(d))
    monkeypatch.chdir(roots["music"])
    return {"home": home, "dir": d, **roots}


def _run(capsys, *argv):
    rc = cli.main(["truth", "tg", *argv])
    return rc, capsys.readouterr().out


def _json(capsys, *argv):
    rc, out = _run(capsys, *argv, "--json")
    assert rc == 0
    return json.loads(out)


def test_default_is_cwd_project(world, capsys):
    rc, out = _run(capsys)
    assert rc == 0
    assert out.startswith("Groovamp · groovamp · нить music/main · SOC по умолчанию: SOC-162")
    assert "Контекст" not in out
    # новейшие сверху: 1303 выше 1263; время −03
    assert out.index("tg:factory/1303") < out.index("tg:factory/1263")
    assert "10.10 01:51  «Все на Английском»" in out
    assert "к вопросу 1258 (привязано вручную)" in out
    assert "groovamp.jsonl:2" in out  # файл:строка входящего


def _answer_line(out, tg):
    lines = out.splitlines()
    i = next(n for n, ln in enumerate(lines) if "tg:factory/{0}".format(tg) in ln)
    return lines[i]


def test_manual_link_is_sure(world, capsys):
    (v,) = _json(capsys)
    a = {a["tg"]: a for a in v["answers"]}[1263]
    assert a["link_confidence"] == "link" and a["question_state"] == "answered"
    _, out = _run(capsys)
    assert "к вопросу 1258 (привязано вручную)" in _answer_line(out, 1263)
    assert "вопрос отвечен" in _answer_line(out, 1263)


def test_reply_is_sure(world, capsys):
    (v,) = _json(capsys)
    a = {a["tg"]: a for a in v["answers"]}[1307]
    assert a["link_confidence"] == "reply" and a["question_state"] == "answered"
    _, out = _run(capsys)
    line = _answer_line(out, 1307)
    assert "к вопросу 1306 (реплай)" in line and "вопрос отвечен" in line


def test_time_link_is_not_sure(world, capsys):
    (v,) = _json(capsys)
    a = {a["tg"]: a for a in v["answers"]}[1303]
    assert a["link_confidence"] == "time" and a["question_state"] == "maybe"
    _, out = _run(capsys)
    line = _answer_line(out, 1303)
    assert "к вопросу 1302? (по времени — не уверено, подтверди: " in line
    assert "pult.py thread-link --tg 1303 --soc SOC-170 --q 1302" in line
    assert "вопрос отвечен" not in line


def test_no_question_is_none(world, capsys):
    with open(world["dir"] / "groovamp.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(_in(1310, "просто так", "2026-10-10T15:00:00+00:00")) + "\n")
    (v,) = _json(capsys)
    assert {a["tg"]: a for a in v["answers"]}[1310]["link_confidence"] == "none"


def test_open_questions(world, capsys):
    (v,) = _json(capsys)
    # 1258 закрыт уверенно (ручная link), 1306 — реплаем; 1302 закрыт пультом только
    # по времени — остаётся открытым с пометкой
    assert [q["tg"] for q in v["questions"]] == [1302, 1301]
    q1302, q1301 = v["questions"]
    assert q1302["state"] == "maybe" and q1302["maybe_answered_by"] == [1303]
    assert q1301["state"] == "open" and q1301["maybe_answered_by"] == []
    assert q1301["link"] == "tg:factory/1301" and q1301["soc"] == ["SOC-162"]
    _, out = _run(capsys)
    assert "возможно отвечен: tg 1303 (по времени)" in _answer_line(out, 1302)
    assert "возможно отвечен" not in _answer_line(out, 1301)


def test_loose_without_how_is_not_sure(world, capsys):
    """Старые строки q_state без how: уверенность берём у answered_by."""
    with open(world["dir"] / "groovamp.jsonl", "a", encoding="utf-8") as f:
        for r in (_out(1311, "❓ Ещё вопрос", "2026-10-10T15:01:00+00:00", q_state="open"),
                  _in(1312, "мимо", "2026-10-10T15:02:00+00:00", q_ref=1311, q_ref_how="loose"),
                  _row(kind="q_state", tg_msg_id=1311, q_state="answered", answered_by=1312)):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (v,) = _json(capsys)
    assert 1311 in [q["tg"] for q in v["questions"]]


def test_edit_link_delete(world, capsys):
    (v,) = _json(capsys)
    by = {a["tg"]: a for a in v["answers"]}
    assert 1305 not in by  # удалённое не показываем
    a = by[1303]
    assert a["text"] == "А как слои переключать?" and a["edited"]
    assert a["soc"] == ["SOC-170"] and a["soc_src"] == "link"
    assert a["q_ref"] == 1302 and a["q_ref_how"] == "loose"  # link только с SOC — не вопрос
    assert len(a["also"]) == 2  # строки edit и link


def test_limit(world, capsys):
    (v,) = _json(capsys, "--limit", "1")
    assert [a["tg"] for a in v["answers"]] == [1307] and v["answers_total"] == 3


def test_project_filter_and_empty(world, capsys):
    rc, out = _run(capsys, "tide-stack")
    assert rc == 0
    assert "Groovamp" not in out
    assert "Контекст · kontekst" in out and "журнала нет" in out
    assert "Агенты и контекст · agenty" in out and "ответов Гриши нет" in out


def test_thread_and_slug(world, capsys):
    assert {v["slug"] for v in _json(capsys, "tide-stack/context-keeper")} == {"kontekst", "agenty"}
    assert [v["slug"] for v in _json(capsys, "groovamp")] == ["groovamp"]
    assert [v["slug"] for v in _json(capsys, "--project", "music")] == ["groovamp"]


def test_management_sees_all(world, capsys, monkeypatch):
    monkeypatch.chdir(world["management"])
    assert len(_json(capsys)) == 3


def test_unknown_target(world, capsys):
    assert cli.main(["truth", "tg", "nope"]) != 0


def test_no_topics_for_project(world, capsys, monkeypatch):
    monkeypatch.chdir(world["home"])
    root = world["home"].parent / "solo"
    (root / ".tide").mkdir(parents=True)
    with open(world["home"] / "roster.md", "a", encoding="utf-8") as f:
        f.write("solo | {0}\n".format(root))
    monkeypatch.chdir(root)
    rc, out = _run(capsys)
    assert rc == 0 and "нет тем в Telegram — проект solo" in out


def test_no_index(world, capsys, monkeypatch):
    monkeypatch.setenv(truth_tg.THREADS_ENV, str(world["dir"] / "missing"))
    rc, out = _run(capsys)
    assert rc == 0 and "нет тем Telegram" in out


def test_extra_word_rejected_outside_tg(world, capsys):
    assert cli.main(["truth", "music", "лишнее"]) != 0
