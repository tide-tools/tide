"""tide.decisions_tg — схема журнала решений, «последняя по id главнее», tide truth check.

Всё на tmp_path: свой дом с roster.md, свои корни; живые журналы не трогаем.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tide import cli, decisions_tg as dt, truth, truth_edits as edits

SLUG = "01-tema"


def rec(**kw) -> dict:
    """Валидная запись нынешнего вида (без src/valid_from), правки — через kw."""
    r = {
        "id": "tg-100-1", "date": "2026-10-09", "project": "alpha", "topic": SLUG,
        "question": {"tg": 100, "ask": "main-1", "at": "2026-10-09T16:22:45+00:00",
                     "text": "Делаем?"},
        "answer": {"tg": [101], "at": "2026-10-09T16:24:00+00:00", "text": "да"},
        "decision": "делаем", "handed_to": [], "soc": [], "status": "принято",
        "supersedes": None, "superseded_by": None, "note": None,
    }
    r.update(kw)
    return r


def full(**kw) -> dict:
    return dt.normalize(rec(**kw))


# --- схема ------------------------------------------------------------------

def test_valid_record():
    r = full()
    assert dt.validate(r, "alpha", SLUG) == []
    assert list(r) == list(dt.KEYS)
    assert r["src"] == "tg:101" and r["valid_from"] == "2026-10-09T16:24:00+00:00"


def test_old_record_needs_normalize():
    old = rec()
    errs = dt.validate(old)
    assert any("нет ключей: src, valid_from" in e for e in errs)
    assert dt.validate(dt.normalize(old)) == []
    assert "src" not in old  # normalize не портит исходник


def test_normalize_keeps_given_fields():
    r = dt.normalize(rec(valid_from="2026-10-10T00:00:00+03:00"))
    assert r["valid_from"] == "2026-10-10T00:00:00+03:00"


@pytest.mark.parametrize("patch, want", [
    ({"id": "3274-2"}, "id «3274-2»"),
    ({"id": "tg-x"}, "id «tg-x»"),
    ({"date": "09.10.2026"}, "date «09.10.2026»"),
    ({"decision": ""}, "decision: нужна непустая строка"),
    ({"status": "отменено"}, "status «отменено»"),
    ({"status": "заменено"}, "status заменено: нужен superseded_by"),
    ({"question": {"tg": "100", "ask": "a", "at": "2026-10-09T16:22:45+00:00", "text": "?"}},
     "question.tg: нужно число"),
    ({"question": {"tg": 100, "ask": "a", "at": "2026-10-09T16:22:45", "text": "?"}},
     "question.at: нужен ISO с поясом"),
    ({"answer": {"tg": [], "at": "2026-10-09T16:24:00+00:00", "text": "да"}},
     "answer.tg: нужен непустой список чисел"),
    ({"answer": {"tg": [True], "at": "2026-10-09T16:24:00+00:00", "text": "да"}},
     "answer.tg: нужен непустой список чисел"),
    ({"answer": {"tg": [101], "at": "вчера", "text": "да"}}, "answer.at: нужен ISO с поясом"),
    ({"valid_from": "2026-10-09"}, "valid_from: нужен ISO с поясом"),
    ({"src": "tg:999"}, "src «tg:999»: должен быть tg:101"),
    ({"handed_to": "music-14"}, "handed_to: нужен список строк"),
    ({"soc": [122]}, "soc: нужен список строк"),
    ({"note": 5}, "note: нужна строка или null"),
    ({"supersedes": 7}, "supersedes: нужна строка или null"),
    ({"topic": "02-drugaya"}, "topic «02-drugaya»: журнал лежит в теме 01-tema"),
    ({"project": "beta"}, "project «beta»: журнал лежит в проекте alpha"),
])
def test_each_schema_error_caught(patch, want):
    r = full()
    r.update(patch)
    errs = dt.validate(r, "alpha", SLUG)
    assert any(want in e for e in errs), errs


def test_missing_key_and_not_object():
    r = full()
    del r["soc"]
    assert dt.validate(r) == ["нет ключей: soc"]
    assert dt.validate([1]) == ["запись не объект JSON"]


def test_replaced_with_superseded_by_is_valid():
    r = full(status="заменено", superseded_by="tg-200")
    assert dt.validate(r) == []


# --- журнал -----------------------------------------------------------------

def _journal(tdir: Path, *rows, raw: bool = False) -> Path:
    """Журнал из *rows*; записи — с src/valid_from на диске (как пишет decide),
    *raw* — как есть (старый вид без них)."""
    tdir.mkdir(parents=True, exist_ok=True)
    p = tdir / dt.JOURNAL_FILE
    p.write_text("".join((r if isinstance(r, str) else json.dumps(
        r if raw else dt.normalize(r), ensure_ascii=False)) + "\n" for r in rows),
        encoding="utf-8")
    return p


def test_load_last_by_id_wins(tmp_path):
    p = _journal(tmp_path / SLUG,
                 rec(),
                 rec(id="tg-200", question={"tg": 200, "ask": "m", "text": "?",
                                            "at": "2026-10-09T17:00:00+00:00"},
                     answer={"tg": [201], "at": "2026-10-09T17:01:00+00:00", "text": "да"}),
                 "",
                 rec(decision="делаем иначе",
                     answer={"tg": [101, 105], "at": "2026-10-09T18:00:00+00:00",
                             "text": "да / точно"}))
    recs, errs = dt.load(p, "alpha")
    assert errs == []
    assert [r["id"] for r in recs] == ["tg-100-1", "tg-200"]
    assert recs[0]["decision"] == "делаем иначе" and recs[0]["_line"] == 4
    assert recs[0]["valid_from"] == "2026-10-09T18:00:00+00:00"


def test_load_reports_file_line(tmp_path):
    p = _journal(tmp_path / SLUG, rec(), "{не json", rec(id="bad"))
    recs, errs = dt.load(p, "alpha")
    assert any(e.startswith("{0}:2: не JSON".format(p)) for e in errs)
    assert any(e.startswith("{0}:3: id «bad»".format(p)) for e in errs)


# --- зеркало в facts.md -----------------------------------------------------

FACTS = """# Факты

сверено: 2026-10-09

## Решения

- решение: делаем — ответ «да» · 09.10.2026 · `tg:100`, `tg:101`
{extra}"""


def test_facts_in_sync(tmp_path):
    tdir = tmp_path / SLUG
    p = _journal(tdir, rec())
    (tdir / "facts.md").write_text(FACTS.format(extra=""), encoding="utf-8")
    recs, _ = dt.load(p)
    assert dt.check_facts(tdir / "facts.md", recs) == []


def test_facts_desync_caught(tmp_path):
    tdir = tmp_path / SLUG
    p = _journal(tdir, rec(), rec(id="tg-300", answer={"tg": [301], "text": "да",
                                                       "at": "2026-10-09T17:01:00+00:00"}))
    (tdir / "facts.md").write_text(FACTS.format(extra="- решение без ссылки · 09.10.2026\n"),
                                   encoding="utf-8")
    recs, _ = dt.load(p)
    errs = dt.check_facts(tdir / "facts.md", recs)
    assert len(errs) == 2
    assert "facts.md:8: строка решения без ссылки" in errs[0]
    assert "{0}:2: решение tg-300 не отражено".format(p) in errs[1]


def test_facts_without_section(tmp_path):
    tdir = tmp_path / SLUG
    p = _journal(tdir, rec())
    (tdir / "facts.md").write_text("# Факты\n\n## Факты (новые сверху)\n\n- x\n",
                                   encoding="utf-8")
    recs, _ = dt.load(p)
    assert "нет секции «## Решения»" in dt.check_facts(tdir / "facts.md", recs)[0]


# --- tide truth check -------------------------------------------------------

@pytest.fixture
def world(tmp_path, monkeypatch):
    """Дом с ростером: alpha — журнал в порядке, beta — с ошибками, gamma — без журналов."""
    home = tmp_path / "home"
    (home / ".tide").mkdir(parents=True)
    roots = {n: tmp_path / n for n in ("alpha", "beta", "gamma")}
    for r in roots.values():
        (r / ".tide" / "topics").mkdir(parents=True)
    a = roots["alpha"] / ".tide" / "topics" / SLUG
    _journal(a, rec())
    (a / "facts.md").write_text(FACTS.format(extra=""), encoding="utf-8")
    b = roots["beta"] / ".tide" / "topics" / "02-b"
    _journal(b, rec(project="beta", topic="02-b"), rec(project="beta", topic=SLUG, id="tg-5"))
    (b / "facts.md").write_text(FACTS.format(extra=""), encoding="utf-8")
    g = roots["gamma"] / ".tide" / "topics" / "01-g"
    g.mkdir()
    (g / "topic.md").write_text("# 01-g\n\ntitle: g\n", encoding="utf-8")
    (home / "roster.md").write_text(
        "# tide roster\n" + "".join("{0} | {1}\n".format(n, r) for n, r in roots.items()),
        encoding="utf-8")
    for d in (a, b):
        edits.baseline(d)  # стартовая строка журнала правок
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.chdir(elsewhere)
    return {"home": home, **roots}


def _run(capsys, *argv):
    rc = cli.main(["truth", *argv])
    return rc, capsys.readouterr().out


def test_check_ok_project(world, capsys):
    rc, out = _run(capsys, "check", "--project", "alpha")
    assert rc == 0
    assert "alpha/01-tema: ок 1 решений" in out


def test_check_errors_exit_1(world, capsys):
    before = {str(p): p.read_bytes() for p in world["beta"].rglob("*") if p.is_file()}
    rc, out = _run(capsys, "check")
    assert rc == 1
    assert "alpha/01-tema: ок 1 решений" in out
    assert "beta/02-b: ошибок" in out
    assert "decisions-tg.jsonl:2: topic «01-tema»: журнал лежит в теме 02-b" in out
    assert "decisions-tg.jsonl:2: id «tg-5»" not in out  # tg-5 — валидный id
    assert "gamma" not in out  # без журнала и «Решений» — молчим
    after = {str(p): p.read_bytes() for p in world["beta"].rglob("*") if p.is_file()}
    assert before == after  # только чтение


def test_check_json(world, capsys):
    rc, out = _run(capsys, "check", "--project", "beta", "--json")
    data = json.loads(out)
    assert rc == 1 and data[0]["slug"] == "02-b" and data[0]["errors"]


# --- счётчик фактов в tide truth (пункты всех секций, решения отдельно) ------

def test_truth_counts_decisions_and_now(world, capsys):
    t = world["gamma"] / ".tide" / "topics" / "02-only"
    t.mkdir()
    (t / "topic.md").write_text("# 02-only\n\ntitle: только решения\n", encoding="utf-8")
    (t / "facts.md").write_text(
        "# Ф\n\nсверено: 2026-10-09\n\n## Сейчас\n\n- идёт выкат\n\n"
        "## Решения\n\n- решение: да · `tg:1`, `tg:2`\n", encoding="utf-8")
    rc, out = _run(capsys, "--project", "gamma")
    assert rc == 0 and "только решения · фактов 2, из них решений 1" in out
    rc, out = _run(capsys, "02-only")
    assert "Решения:\nfacts.md:11 - решение: да" in out
    assert "Факты (0 из 0" not in out
    info = truth.topic_info("gamma", t)
    assert (info["facts"], info["decisions"]) == (2, 1)


# --- tide truth inbox / decide ---------------------------------------------

Q_AT, A_AT = "2026-10-08T12:00:00+00:00", "2026-10-08T12:05:00+00:00"


def _dialog_rows():
    """Поддельный журнал пульта: пары 500→501 (ask-closed по id лога), 600→601
    (reply_to_tg), 700→701 (пропущена), 100→101 (уже записана), 800→ сводка."""
    out = lambda tg, text, **kw: {"ts": Q_AT, "dir": "out", "kind": "alert", "route": "human",
                                  "tg_msg_ids": [tg], "text": text, **kw}
    inc = lambda tg, text, **kw: {"ts": A_AT, "dir": "in", "kind": "tg", "tg_msg_id": tg,
                                  "text": text, **kw}
    return [
        out(500, "<b>❓ Кнопку</b> делаем &amp; красим?", format="html"),
        inc(501, "[tg 🎤] Да, делаем.\n Красим тоже.\n(аудио: /x/501.ogg)"),
        {"ts": A_AT, "kind": "ask-closed", "ask": "main-1", "source": 501},
        out(600, "❓ Второй вопрос?"),
        inc(601, "второй да", reply_to_tg=600),
        inc(602, "ещё и вот так", reply_to_tg=600),
        out(700, "❓ Личное?"),
        inc(701, "личное"),
        {"ts": A_AT, "kind": "ask-closed", "ask": 700, "source": 701},
        out(100, "❓ Делаем?"),
        inc(101, "да"),
        {"ts": A_AT, "kind": "ask-closed", "ask": 100, "source": 101},
        out(800, "сводка"),
        {"ts": A_AT, "kind": "ask-closed", "ask": 600, "source": 800},  # свёрнут, не ответ
        inc(901, "реплай мимо пульта", reply_to_tg=900),
    ]


@pytest.fixture
def pult_world(world, tmp_path, monkeypatch):
    """world + журнал пульта с битой строкой, log.jsonl и decisions-skip.jsonl."""
    mg = tmp_path / "management"
    (mg / ".tide" / "topics").mkdir(parents=True)
    roster = world["home"] / "roster.md"
    roster.write_text(roster.read_text(encoding="utf-8") + "management | {0}\n".format(mg),
                      encoding="utf-8")
    dialog = tmp_path / "dialog.jsonl"
    rows = [json.dumps(r, ensure_ascii=False) for r in _dialog_rows()]
    rows.insert(3, '{"ts":"x","kind":"ask-closed","ask":3102 3103,"source":}')  # битая
    dialog.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"id": "main-1", "actor": "m", "text": "<b>❓ Кнопку</b> делаем &amp; красим?"},
                              ensure_ascii=False) + "\n", encoding="utf-8")
    (mg / ".tide" / "topics" / dt.SKIP_FILE).write_text(
        json.dumps({"q_tg": 700, "a_tg": 701, "reason": "личное"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    monkeypatch.setenv(dt.DIALOG_ENV, str(dialog))
    monkeypatch.setenv(dt.LOG_ENV, str(log))
    return {**world, "management": mg, "dialog": dialog, "log": log}


def test_pult_tolerates_broken_line(pult_world):
    p = dt.Pult.from_files(pult_world["dialog"], pult_world["log"])
    assert p.tg_of("main-1") == 500  # id лога → tg по тексту исходящего
    pairs = {(x["question"]["tg"], x["answer"]["tg"]) for x in p.pairs()}
    assert pairs == {(500, 501), (600, 601), (600, 602), (700, 701), (100, 101), (900, 901)}


def test_inbox_lists_new_hides_recorded_and_skipped(pult_world, capsys):
    rc, out = _run(capsys, "inbox", "--since", "2026-10-08")
    assert rc == 0
    assert "500 → 501" in out and "600 → 601" in out and "600 → 602" in out
    assert "«❓ Кнопку делаем & красим?» → «Да, делаем. Красим тоже.»" in out
    assert "900 → 901  «(вопроса нет в журнале пульта)»" in out
    assert "100 → 101" not in out  # записано в alpha (answer.tg)
    assert "700 → 701" not in out  # в decisions-skip.jsonl
    assert "без решения: 4" in out and "пропущено: 1" in out
    rc, out = _run(capsys, "inbox", "--since", "2026-10-09")
    assert "без решения: 0" in out


def test_inbox_json(pult_world, capsys):
    rc, out = _run(capsys, "inbox", "--since", "2026-10-08", "--json")
    data = json.loads(out)
    first = data[0]
    assert set(first) == {"question", "answer"}
    assert first["question"] == {"tg": 500, "ask": "main-1", "at": Q_AT,
                                 "text": "❓ Кнопку делаем & красим?"}
    assert first["answer"] == {"tg": 501, "at": A_AT, "text": "Да, делаем. Красим тоже."}


def _decide(capsys, *extra):
    return _run(capsys, "decide", "--topic", "alpha/" + SLUG, *extra)


def test_decide_writes_record_and_fact(pult_world, capsys):
    tdir = pult_world["alpha"] / ".tide" / "topics" / SLUG
    rc, out = _decide(capsys, "--q", "500", "--a", "501", "--decision", "кнопку делаем",
                      "--soc", "SOC-7", "--handed-to", "music-1")
    assert rc == 0, out
    lines = (tdir / dt.JOURNAL_FILE).read_text(encoding="utf-8").splitlines()
    new = json.loads(lines[-1])
    assert dt.validate(new, "alpha", SLUG) == []
    assert new["id"] == "tg-500" and new["src"] == "tg:501" and new["valid_from"] == A_AT
    assert new["question"]["ask"] == "main-1" and new["answer"]["text"] == "Да, делаем. Красим тоже."
    assert new["soc"] == ["SOC-7"] and new["handed_to"] == ["music-1"]
    facts = (tdir / "facts.md").read_text(encoding="utf-8")
    assert facts.rstrip().endswith("- решение: кнопку делаем — ответ Гриши «Да, делаем. Красим "
                                   "тоже.» · 08.10.2026 · `tg:500`, `tg:501`")
    rc, out = _run(capsys, "check", "--project", "alpha")
    assert rc == 0 and "ок 2 решений" in out
    rc, out = _run(capsys, "inbox", "--since", "2026-10-08")
    assert "500 → 501" not in out


def test_decide_multi_answer_and_custom_fact(pult_world, capsys):
    tdir = pult_world["alpha"] / ".tide" / "topics" / SLUG
    rc, out = _decide(capsys, "--q", "600", "--a", "601,602", "--decision", "второе да",
                      "--fact", "своя строка")
    assert rc == 0, out
    new = json.loads((tdir / dt.JOURNAL_FILE).read_text(encoding="utf-8").splitlines()[-1])
    assert new["answer"]["tg"] == [601, 602]
    assert new["answer"]["text"] == "второй да / ещё и вот так"
    assert "- своя строка · 08.10.2026 · `tg:600`, `tg:601`, `tg:602`" in \
        (tdir / "facts.md").read_text(encoding="utf-8")


def test_decide_id_taken_fails(pult_world, capsys):
    tdir = pult_world["alpha"] / ".tide" / "topics" / SLUG
    before = (tdir / dt.JOURNAL_FILE).read_bytes(), (tdir / "facts.md").read_bytes()
    rc = cli.main(["truth", "decide", "--topic", "alpha/" + SLUG, "--q", "500", "--a", "501",
                   "--decision", "x", "--id", "tg-100-1"])
    err = capsys.readouterr().err
    assert rc == 1 and "id занят: tg-100-1" in err
    assert ((tdir / dt.JOURNAL_FILE).read_bytes(), (tdir / "facts.md").read_bytes()) == before


def test_decide_invalid_record_writes_nothing(pult_world, capsys):
    tdir = pult_world["alpha"] / ".tide" / "topics" / SLUG
    before = (tdir / dt.JOURNAL_FILE).read_bytes()
    rc = cli.main(["truth", "decide", "--topic", "alpha/" + SLUG, "--q", "500", "--a", "501",
                   "--decision", "x", "--id", "bad-id"])
    assert rc == 1 and "id «bad-id»" in capsys.readouterr().err
    assert (tdir / dt.JOURNAL_FILE).read_bytes() == before


def test_decide_unknown_topic(pult_world, capsys):
    rc = cli.main(["truth", "decide", "--topic", "alpha/99-net", "--q", "500", "--a", "501",
                   "--decision", "x"])
    assert rc == 1 and "топика нет, заведи" in capsys.readouterr().err


def test_decide_supersedes(pult_world, capsys):
    tdir = pult_world["alpha"] / ".tide" / "topics" / SLUG
    rc, out = _decide(capsys, "--q", "500", "--a", "501", "--decision", "делаем по-новому",
                      "--supersedes", "tg-100-1")
    assert rc == 0, out
    lines = (tdir / dt.JOURNAL_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    old, new = json.loads(lines[1]), json.loads(lines[2])
    assert old["id"] == "tg-100-1" and old["status"] == "заменено" \
        and old["superseded_by"] == "tg-500" and old["decision"] == "делаем"
    assert new["supersedes"] == "tg-100-1" and new["status"] == "принято"
    facts = (tdir / "facts.md").read_text(encoding="utf-8")
    assert "- решение: делаем — ответ «да» (заменено → tg-500) · 09.10.2026 · `tg:100`, `tg:101`" \
        in facts
    recs, errs = dt.load(tdir / dt.JOURNAL_FILE, "alpha")
    assert errs == [] and dt.check_facts(tdir / "facts.md", recs) == []


def test_decide_dry_run_writes_nothing(pult_world, capsys):
    tdir = pult_world["alpha"] / ".tide" / "topics" / SLUG
    before = (tdir / dt.JOURNAL_FILE).read_bytes(), (tdir / "facts.md").read_bytes()
    rc, out = _decide(capsys, "--q", "500", "--a", "501", "--decision", "проба", "--dry-run",
                      "--supersedes", "tg-100-1")
    assert rc == 0
    assert out.startswith("dry-run: ничего не записано")
    assert '"id": "tg-500"' in out and "(заменено → tg-500)" in out
    assert ((tdir / dt.JOURNAL_FILE).read_bytes(), (tdir / "facts.md").read_bytes()) == before


def test_decide_creates_section(pult_world, capsys):
    t = pult_world["gamma"] / ".tide" / "topics" / "01-g"
    rc, out = _run(capsys, "decide", "--topic", "gamma/01-g", "--q", "500", "--a", "501",
                   "--decision", "да")
    assert rc == 0, out
    facts = (t / "facts.md").read_text(encoding="utf-8")
    assert facts.startswith("## Решения\n\n- решение: да")
    rc, out = _run(capsys, "check", "--project", "gamma")
    assert rc == 0


def test_add_decision_row_inserts_at_section_end():
    lines = ["# Ф", "", "## Решения", "", "- a · `tg:1`", "", "## Факты", "", "- b"]
    out = dt.add_decision_row(lines, "- new · `tg:2`")
    assert out[4:6] == ["- a · `tg:1`", "- new · `tg:2`"] and out[-1] == "- b"


# --- SOC-130 шаг 3: журнал правок, дубль id, src на диске, decide без вопроса ---

def test_check_catches_missing_src_on_disk(tmp_path):
    p = _journal(tmp_path / SLUG, rec(), raw=True)
    _, errs = dt.load(p, "alpha")
    assert any("нет на диске: src, valid_from" in e for e in errs), errs


def test_duplicate_id_counted_like_summary(world, capsys):
    a = world["alpha"] / ".tide" / "topics" / SLUG
    _journal(a, rec(), rec(decision="делаем иначе"))
    (a / "facts.md").write_text(FACTS.format(
        extra="- решение: делаем иначе — ответ «да» · 09.10.2026 · `tg:100`, `tg:101`\n"),
        encoding="utf-8")
    edits.baseline(a, reason="тест", force=True)
    res = dt.check_topic("alpha", a)
    assert res["decisions"] == 1
    assert any("действующих строк 2, а действующих решений в журнале 1" in e
               for e in res["errors"])
    assert res["notes"] == ["tg-100-1: строк 2 (1, 2) без superseded_by — правка записи, "
                            "действует строка 2"]
    # старую строку пометили — счёт сходится, сводка считает так же
    text = (a / "facts.md").read_text(encoding="utf-8").replace(
        "ответ «да» · 09", "ответ «да» (заменено → tg-100-1, новая версия) · 09", 1)
    (a / "facts.md").write_text(text, encoding="utf-8")
    edits.baseline(a, reason="тест", force=True)
    res = dt.check_topic("alpha", a)
    assert res["errors"] == [] and res["decisions"] == 1
    assert truth.topic_info("alpha", a)["decisions"] == 1
    rc, out = _run(capsys, "check", "--project", "alpha")
    assert rc == 0 and "правка записи: tg-100-1" in out


def test_decide_writes_edits_row(pult_world, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-keeper")
    tdir = pult_world["alpha"] / ".tide" / "topics" / SLUG
    rc, out = _decide(capsys, "--q", "500", "--a", "501", "--decision", "да")
    assert rc == 0, out
    rows = edits.read_rows(tdir)
    assert [r["action"] for r in rows] == ["baseline", "decide"]
    last = rows[-1]
    assert last["id"] == "tg-500" and last["src"] == "tg:501" and last["session"] == "sid-keeper"
    assert last["files"] == ["decisions-tg.jsonl", "facts.md"]
    assert last["before"] == rows[0]["after"] and last["after"] == edits.state(tdir)
    assert edits.check_dir(tdir) == []


def test_edit_without_journal_caught_and_blocks_decide(pult_world, capsys):
    tdir = pult_world["alpha"] / ".tide" / "topics" / SLUG
    with (tdir / "facts.md").open("a", encoding="utf-8") as fh:
        fh.write("\n## Факты (новые сверху)\n\n- приписка мимо журнала\n")
    rc, out = _run(capsys, "check", "--project", "alpha")
    assert rc == 1 and "facts.md: правка без журнала" in out
    before = (tdir / dt.JOURNAL_FILE).read_bytes()
    rc = cli.main(["truth", "decide", "--topic", "alpha/" + SLUG, "--q", "500", "--a", "501",
                   "--decision", "x"])
    err = capsys.readouterr().err
    assert rc == 1 and "без журнала правка не проходит" in err
    assert (tdir / dt.JOURNAL_FILE).read_bytes() == before
    # признать расхождение можно только по имени темы и с причиной
    rc = cli.main(["truth", "baseline", "--project", "alpha"])
    assert rc == 1 and "признать" in capsys.readouterr().out
    rc = cli.main(["truth", "baseline", "--topic", "alpha/" + SLUG, "--reason", "приписка человека"])
    assert rc == 0
    capsys.readouterr()
    rc, out = _run(capsys, "check", "--project", "alpha")
    assert rc == 0, out
    assert edits.read_rows(tdir)[-1]["note"] == "приписка человека"


def test_chain_break_inside_journal_caught(world):
    a = world["alpha"] / ".tide" / "topics" / SLUG
    first = edits.state(a)
    (a / "facts.md").write_text("подмена\n", encoding="utf-8")
    edits.append(a, "edit", {**first, "facts.md": "0" * 16}, edits.state(a))
    errs = edits.check_dir(a)
    assert len(errs) == 1 and ":2: правка без журнала между строками — facts.md" in errs[0]


def test_no_journal_is_error_until_baseline(world, capsys):
    (world["alpha"] / ".tide" / "topics" / SLUG / edits.EDITS_FILE).unlink()
    rc, out = _run(capsys, "check", "--project", "alpha")
    assert rc == 1 and "нет журнала правок" in out and "tide truth baseline" in out
    rc, out = _run(capsys, "baseline", "--project", "alpha")
    assert rc == 0 and "alpha/01-tema: baseline" in out
    rc, out = _run(capsys, "baseline", "--project", "alpha")
    assert "сходится — не трогаю" in out
    rc, out = _run(capsys, "check", "--project", "alpha")
    assert rc == 0


def test_decide_no_question(pult_world, capsys):
    tdir = pult_world["alpha"] / ".tide" / "topics" / SLUG
    rc = cli.main(["truth", "decide", "--topic", "alpha/" + SLUG, "--no-question", "--a", "701",
                   "--decision", "x"])
    assert rc == 1 and "--no-question требует --note" in capsys.readouterr().err
    rc = cli.main(["truth", "decide", "--topic", "alpha/" + SLUG, "--no-question", "--a", "999",
                   "--decision", "x", "--note", "голос"])
    assert rc == 1 and "ответа tg:999 нет среди входящих" in capsys.readouterr().err
    rc, out = _decide(capsys, "--no-question", "--a", "701", "--decision", "решили голосом",
                      "--note", "голос без реплая")
    assert rc == 0, out
    new = json.loads((tdir / dt.JOURNAL_FILE).read_text(encoding="utf-8").splitlines()[-1])
    assert new["id"] == "tg-701" and new["question"] is None and new["src"] == "tg:701"
    assert new["note"] == "голос без реплая"
    assert (tdir / "facts.md").read_text(encoding="utf-8").rstrip().endswith(
        "· 08.10.2026 · `tg:701`")
    rc, out = _run(capsys, "check", "--project", "alpha")
    assert rc == 0, out
    assert dt.validate(dict(new, note=None)) == ["question null: нужна note — почему решение без вопроса"]


def test_skip_verb_journaled(pult_world, capsys):
    d = pult_world["management"] / ".tide" / "topics"
    edits.baseline(d)
    rc, out = _run(capsys, "skip", "--q", "600", "--a", "602", "--reason", "повтор")
    assert rc == 0 and "записано" in out
    assert (600, 602) in dt.load_skip(d / dt.SKIP_FILE)
    assert edits.read_rows(d)[-1]["action"] == "skip" and edits.check_dir(d) == []
    rc = cli.main(["truth", "skip", "--q", "600", "--a", "602", "--reason", "ещё"])
    assert rc == 1 and "уже в" in capsys.readouterr().err
    rc, out = _run(capsys, "check", "--project", "management")
    assert rc == 0 and "management/(decisions-skip.jsonl): ок 2 пропусков" in out
