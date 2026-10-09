"""SOC-39 — автохендофф по заполнению контекста.

Сторож контекста (Stop-хук ``context-watch``), журнал диалога нити, посев
``tide handoff continue --auto``, условие остановки, сигнал, адрес нити
(``tide thread holder``) и имя первой AO-сессии.

Всё — на временных каталогах: проект, control-home, фейковые транскрипты, фейковые
адаптеры. Ни одна настоящая AO-сессия не поднимается, живые .tide/ не трогаются.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from tide import cli, fields, handoff_contract, handoff_queue as hq, harness, registry, \
    roster, sessions
from tide.adapters.base import SpawnResult, TerminalAdapter
from tide.arc import artifact, dialog, holder, stream
from tide.hooks import context_watch as cw, session_start
from tide.launcher import autohandoff as ah, handoff, pickup

SID = "11111111-0000-4000-8000-00000000aaaa"


# --- фикстуры ------------------------------------------------------------------

@pytest.fixture
def world(tmp_path, monkeypatch):
    """Control-home + проект с открытой нитью и одной сессией, пришпиленной к SID."""
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    build_tide_skeleton(proj, name="proj")
    roster.add(home, "proj", str(proj))
    thread = stream.new_thread(proj, "seam", goal="держать шов чистым")
    sess = stream.new_session(proj, "seam", "s1", claude_session=SID)
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    for k in (cw.SOFT_ENV, cw.NUDGE_ENV, cw.HARD_ENV, cw.ALARM_ENV, ah.ALERT_CMD_ENV,
              handoff.NO_SPAWN_ENV,
              "AO_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(proj)
    return {"home": home, "proj": proj, "thread": thread, "sess": sess,
            "tmp": tmp_path}


def _transcript(path: Path, tokens: int, reply: str = "готово") -> Path:
    """Фейковый транскрипт: реплика человека и ответ агента с usage на *tokens*."""
    rows = [
        {"type": "user", "message": {"role": "user", "content": "сделай"}},
        {"type": "assistant", "timestamp": "2026-10-09T10:00:00Z",
         "message": {"id": "m1", "model": "claude-opus-5-5",
                     "content": [{"type": "text", "text": reply}],
                     "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": tokens - 10}}},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def _payload(world, tokens, **extra):
    t = _transcript(world["tmp"] / "tx" / "{0}.jsonl".format(SID), tokens)
    p = {"session_id": SID, "transcript_path": str(t), "stop_hook_active": False}
    p.update(extra)
    return p


def _work(proj: Path, num: str, thread: str, items: str, status: str = "taken") -> Path:
    d = proj / ".tide" / "arcs" / "works" / "{0}-rabota".format(num)
    d.mkdir(parents=True, exist_ok=True)
    (d / "work.md").write_text(
        "# Работа {0}\n\nkind: work\nstatus: {1}\nthread: {2}\n\n## чеклист\n{3}".format(
            num, status, thread, items), encoding="utf-8")
    return d


class _Fake(TerminalAdapter):
    name = "fake"

    def __init__(self):
        self.spawned = None

    def spawn(self, *, command, cwd, title="tide", dry_run=False):
        self.spawned = {"command": command, "cwd": cwd, "title": title}
        return SpawnResult(ok=True, ref="term_new", detail="fake spawn")

    def focus(self, handle):
        return True


# --- хвост журнала диалога (журнал ведёт пульт, tide только читает) ---------------

def _pult_log(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
                    + "torn {", encoding="utf-8")
    return path


def test_dialog_tail_from_env_jsonl_clipped(world, monkeypatch):
    log = _pult_log(world["tmp"] / "pult" / "dialog.jsonl", [
        {"ts": "2026-10-09T05:00:00+00:00", "route": "proj/seam", "dir": "in", "kind": "tg", "text": "привет\nкак дела"},
        {"ts": "2026-10-09T05:01:00+00:00", "route": "proj/seam", "dir": "out", "kind": "reply", "text": "о" * 900},
        {"ts": "2026-10-09T05:02:00+00:00", "route": "proj/seam", "dir": "out", "kind": "reply", "text": ""},
    ])
    monkeypatch.setenv(dialog.LOG_ENV, str(log))
    lines = dialog.tail(world["thread"])
    assert len(lines) == 2
    assert lines[0].endswith(" · Гриша · tg · привет как дела")
    body = lines[1].split(" · ", 3)[3]
    assert len(body) == dialog.CLIP and body.endswith("…")
    assert dialog.tail(world["thread"], n=1) == lines[1:]


def test_dialog_tail_from_thread_field_relative_and_md(world):
    md = world["proj"] / "notes" / "dialog-tail.md"
    md.parent.mkdir(parents=True)
    md.write_text("# хвост\n\n> первая\n\n> вторая\n", encoding="utf-8")
    goal = stream.passport_path(world["thread"])
    fields.set_field(goal, dialog.LOG_FIELD, "notes/dialog-tail.md")
    assert dialog.tail(world["thread"], n=2) == ["> первая", "> вторая"]


def test_dialog_tail_absent_is_empty_not_error(world, monkeypatch):
    assert dialog.tail(world["thread"]) == []
    monkeypatch.setenv(dialog.LOG_ENV, str(world["tmp"] / "nope.jsonl"))
    assert dialog.tail(world["thread"]) == []


def test_hooks_write_only_the_threads_dialog_md(world, monkeypatch):
    """Слова человека — в dialog.md нити (tests/test_dialog_log.py); сторож — никуда."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": SID, "prompt": "[tg] двигай дальше"})))
    hq.cmd_handoff_confirm(None)
    cw.watch_reason(world["proj"], _payload(world, 100_000))
    assert list(world["proj"].rglob("dialog.md")) == [world["thread"] / "dialog.md"]


# --- сторож контекста ------------------------------------------------------------

def _alert_sink(world, monkeypatch) -> Path:
    """TIDE_ALERT_CMD → скрипт во временном каталоге: дописывает env+argv строкой JSON."""
    sink = world["tmp"] / "alerts.jsonl"
    script = world["tmp"] / "alert_sink.py"
    script.write_text(
        "import json, os, sys\n"
        "rec = {{k: os.environ.get(k, '') for k in "
        "('TIDE_ALERT_ROUTE', 'TIDE_ALERT_SOURCE', 'TIDE_ALERT_TEXT')}}\n"
        "rec['argv'] = sys.argv[-1]\n"
        "open({0!r}, 'a').write(json.dumps(rec, ensure_ascii=False) + '\\n')\n"
        .format(str(sink)), encoding="utf-8")
    monkeypatch.setenv(ah.ALERT_CMD_ENV, "{0} {1}".format(sys.executable, script))
    return sink


def _alerts(sink: Path):
    if not sink.is_file():
        return []
    return [json.loads(ln) for ln in sink.read_text(encoding="utf-8").splitlines()]


def test_watch_silent_below_soft(world):
    assert cw.watch_reason(world["proj"], _payload(world, 199_000)) is None
    assert fields.read_field(world["sess"] / "arc.md", cw.CTX_PING_FIELD) is None


def test_watch_ladder_one_ping_per_threshold(world, monkeypatch):
    sink = _alert_sink(world, monkeypatch)
    pp = world["sess"] / "arc.md"
    assert cw.watch_reason(world["proj"], _payload(world, 199_000)) is None
    assert _alerts(sink) == []
    # 200k — мягкий пинг, один; в лог пульта (route=log)
    r1 = cw.watch_reason(world["proj"], _payload(world, 200_000))
    assert r1 and "tide offload seam/s1" in r1 and cw.AUTO_CMD in r1
    assert "передача нити начинается сейчас" in r1
    assert fields.read_field(pp, cw.CTX_PING_FIELD).startswith("200 ")
    (a1,) = _alerts(sink)
    assert a1["TIDE_ALERT_ROUTE"] == "log"
    assert a1["TIDE_ALERT_SOURCE"] == "proj/seam/s1"
    assert a1["argv"] == a1["TIDE_ALERT_TEXT"] and "200 тыс." in a1["argv"]
    # второй стоп между порогами — молчим
    assert cw.watch_reason(world["proj"], _payload(world, 250_000)) is None
    assert len(_alerts(sink)) == 1
    # 300k — жёсткий пинг, один
    r2 = cw.watch_reason(world["proj"], _payload(world, 300_000))
    assert r2 and "новую работу не бери" in r2 and cw.AUTO_CMD in r2
    assert cw.watch_reason(world["proj"], _payload(world, 420_000)) is None
    assert [a["TIDE_ALERT_ROUTE"] for a in _alerts(sink)] == ["log", "log"]
    # 500k — тревога: ещё одно требование сессии + сигнал менеджеру
    r3 = cw.watch_reason(world["proj"], _payload(world, 520_000))
    assert r3 and "ТРЕВОГА" in r3 and cw.AUTO_CMD in r3
    last = _alerts(sink)[-1]
    assert last["TIDE_ALERT_ROUTE"] == "manager"
    assert last["TIDE_ALERT_SOURCE"] == "proj/seam/s1"
    assert last["argv"] == "сессия s1 на 520 тыс. токенов, хендоффа нет"
    assert last["TIDE_ALERT_TEXT"] == last["argv"]
    assert fields.read_field(pp, cw.CTX_PING_FIELD).startswith("500 ")
    # дальше — тишина, ступени кончились
    assert cw.watch_reason(world["proj"], _payload(world, 700_000)) is None
    assert len(_alerts(sink)) == 3
    # продолжение после блока — анти-петля
    fields.set_field(pp, cw.CTX_PING_FIELD, "")
    assert cw.watch_reason(world["proj"], _payload(world, 520_000,
                                                   stop_hook_active=True)) is None


def test_watch_jump_over_steps_pings_once_highest(world):
    r = cw.watch_reason(world["proj"], _payload(world, 610_000))
    assert r and "ТРЕВОГА" in r
    assert cw.watch_reason(world["proj"], _payload(world, 620_000)) is None


def test_watch_without_alert_cmd_still_pings(world):
    assert cw.watch_reason(world["proj"], _payload(world, 210_000))


def test_watch_stands_down_after_handing_over(world):
    fields.set_field(world["sess"] / "arc.md", ah.HANDED_OFF_FIELD, "2026-10-09 → 01-x")
    # хендофф больше не требуем; контекст считаем (SOC-98) — один пинг, без AUTO_CMD
    r = cw.watch_reason(world["proj"], _payload(world, 900_000))
    assert r and "ты передала нить" in r and cw.AUTO_CMD not in r
    assert cw.watch_reason(world["proj"], _payload(world, 950_000)) is None


def test_watch_thresholds_from_env(world, monkeypatch):
    assert cw.thresholds() == (200_000, 300_000, 500_000)
    monkeypatch.setenv(cw.SOFT_ENV, "1000")
    monkeypatch.setenv(cw.HARD_ENV, "2_000")
    monkeypatch.setenv(cw.ALARM_ENV, "3000")
    assert cw.thresholds() == (1000, 2000, 3000)
    assert "ТРЕВОГА" in cw.watch_reason(world["proj"], _payload(world, 5000))
    monkeypatch.setenv(cw.HARD_ENV, "abc")
    assert cw.thresholds() == (1000, cw.HARD_TOKENS, cw.HARD_TOKENS)  # лестница не убывает
    # старое имя мягкого порога — синоним
    monkeypatch.delenv(cw.SOFT_ENV)
    monkeypatch.setenv(cw.NUDGE_ENV, "1500")
    assert cw.thresholds()[0] == 1500


def test_watch_hook_cli_prints_block_json(world, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_payload(world, 600_000))))
    assert cw.cmd_context_watch(None) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and cw.AUTO_CMD in out["reason"]


def test_watch_unknown_session_is_silent(world):
    p = _payload(world, 900_000)
    p["session_id"] = "nobody"
    assert cw.watch_reason(world["proj"], p) is None


def test_install_hooks_twice_one_context_watch_entry(tmp_path):
    from tests.conftest import build_tide_skeleton

    root = tmp_path / "p"
    root.mkdir()
    build_tide_skeleton(root, name="p")
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "rtk stop"}]}]},
         "env": {"X": "1"}}), encoding="utf-8")
    harness.install_hooks(root)
    _, notes = harness.install_hooks(root)
    assert notes == []
    data = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
    assert cmds.count(harness.CONTEXT_WATCH_CMD) == 1
    assert "rtk stop" in cmds and harness.OFFLOAD_NUDGE_CMD in cmds
    assert "SubagentStop" not in data["hooks"]
    assert data["env"] == {"X": "1"}


# --- решение «поднимать / встать» -------------------------------------------------

def test_decide_three_branches():
    go = ah.decide(origin_chain=None, origin_mark=None, progress=4, questions=[])
    assert go.spawn and go.chain == 1 and go.progress == 4
    moved = ah.decide(origin_chain=3, origin_mark=2, progress=5, questions=[])
    assert moved.spawn and moved.chain == 1
    stalled_but_ok = ah.decide(origin_chain=2, origin_mark=5, progress=5, questions=[])
    assert stalled_but_ok.spawn and stalled_but_ok.chain == 3
    stalled = ah.decide(origin_chain=3, origin_mark=5, progress=5, questions=[])
    assert not stalled.spawn and stalled.reason == ah.STOP_STALLED
    asked = ah.decide(origin_chain=None, origin_mark=None, progress=0,
                      questions=["01-vopros — что выбрать?"])
    assert not asked.spawn and asked.reason == ah.STOP_QUESTION
    assert "что выбрать" in asked.detail


def test_progress_counts_checked_items_acts_and_pulses(world):
    # контракт v1: работа в Linear, нить пишет о ней пульсами — пульс тоже прогресс
    # (механика приёма/передачи — нет: tests/test_autohandoff_stall_fix.py)
    from tide import acts, offload

    _work(world["proj"], "07", world["thread"].name, "- [x] a\n- [ ] b\n- [x] c\n")
    _work(world["proj"], "08", "other", "- [x] чужое\n")
    offload.write_pulse(world["sess"], note="просто пульс")
    acts.write_act(world["sess"], "сделал шаг", proof="тест")
    assert ah.count_progress(world["proj"], world["thread"]) == 4


def test_open_questions_only_new_and_only_this_thread(world):
    p = world["proj"]
    tslug = world["thread"].name.split("-", 1)[1]
    artifact.new_artifact(p, "какой вариант?", "question", "А или Б", from_arc=tslug)
    artifact.new_artifact(p, "чужой вопрос", "question", "?", from_arc="other")
    artifact.new_artifact(p, "сообщение", "message", "текст", from_arc=tslug)
    done = artifact.new_artifact(p, "старый вопрос", "question", "?", from_arc=tslug)
    artifact.mark_taken(p, done.name, word="ответил")
    qs = ah.open_questions(p, world["thread"])
    assert len(qs) == 1 and "какой вариант?" in qs[0]


# --- посев и --auto -----------------------------------------------------------------

def _prime(world, monkeypatch):
    from tide import offload

    offload.write_pulse(world["sess"], note="собрал хук", cursor="пишу тесты сторожа",
                        next_steps="прогнать тесты · обновить скилл")
    _work(world["proj"], "07", world["thread"].name, "- [x] первый\n- [ ] второй пункт\n")
    log = _pult_log(world["tmp"] / "pult" / "dialog.jsonl", [
        {"ts": "2026-10-09T05:33:50+00:00", "route": "proj/seam", "dir": "in", "kind": "tg",
         "text": "последняя реплика Гриши"}])
    monkeypatch.setenv(dialog.LOG_ENV, str(log))


def test_auto_seed_from_the_arc(world, monkeypatch):
    _prime(world, monkeypatch)
    seed = ah.build_auto_seed(world["sess"])
    assert "пишу тесты сторожа" in seed
    assert "- прогнать тесты" in seed and "- обновить скилл" in seed
    assert "следующий шаг: прогнать тесты" in seed
    assert "работа 07 · 2. второй пункт" in seed
    assert "последняя реплика Гриши" in seed
    assert "<!--" not in seed
    # все блоки контракта, по порядку, каждый своим заголовком
    heads = [ln[3:] for ln in seed.splitlines() if ln.startswith("## ")]
    # + «открытые хвосты» (C12/F40) — вне контракта, сразу за «где мы»
    names = list(handoff_contract.NAMES)
    names.insert(names.index("где мы") + 1, handoff_contract.TAILS_NAME)
    assert heads == names
    inflight = seed.partition("## в работе прямо сейчас\n\n")[2].partition("\n## ")[0]
    assert "работа 07 · 2. второй пункт" in inflight
    # нет журнала — посев без хвоста, не падение: блок на месте, с причиной
    monkeypatch.delenv(dialog.LOG_ENV)
    bare = ah.build_auto_seed(world["sess"])
    replies = bare.partition("## неотвеченные реплики Гриши\n\n")[2].partition("\n## ")[0]
    assert replies.startswith("— ") and "последняя реплика Гриши" not in bare


def test_auto_dry_run_writes_nothing(world, monkeypatch, capsys):
    _prime(world, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    before = sorted(str(p) for p in world["proj"].rglob("*"))
    assert cli.main(["handoff", "continue", "--auto", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "поднял бы новую сессию" in out and "пишу тесты сторожа" in out
    assert sorted(str(p) for p in world["proj"].rglob("*")) == before
    assert hq.list_offers(world["home"]) == []


def test_auto_spawns_and_successor_inherits_chain(world, monkeypatch, capsys):
    _prime(world, monkeypatch)
    adapter = _Fake()
    monkeypatch.setattr(pickup, "get_adapter", lambda name=None: adapter)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    assert cli.main(["handoff", "continue", "--auto"]) == 0
    out = capsys.readouterr().out
    assert adapter.spawned is not None
    assert "передано в новую сессию" in out.strip().splitlines()[-1]
    (rec,) = hq.list_offers(world["home"])
    born = world["proj"] / ".tide" / "arcs" / world["thread"].name / "arcs" / \
        rec["arc"].split("/", 1)[1]
    pp = born / "arc.md"
    assert fields.read_field(pp, ah.AUTO_CHAIN_FIELD) == "1"
    # отмеченный пункт работы + пульс «собрал хук» (пульс — тоже продвижение)
    assert fields.read_field(pp, ah.PROGRESS_MARK_FIELD) == "2"
    seed = (born / "input" / "handoff-seed.md").read_text(encoding="utf-8")
    assert "пишу тесты сторожа" in seed and "последняя реплика Гриши" in seed
    assert fields.read_field(world["sess"] / "arc.md", ah.HANDED_OFF_FIELD)
    # передавшая сессия — хендофф не требуем, только пинг ступени (SOC-98)
    r = cw.watch_reason(world["proj"], _payload(world, 900_000))
    assert r and cw.AUTO_CMD not in r


def test_auto_stands_on_open_question_and_signals(world, monkeypatch, capsys):
    _prime(world, monkeypatch)
    tslug = world["thread"].name.split("-", 1)[1]
    artifact.new_artifact(world["proj"], "какой вариант?", "question", "А или Б",
                          from_arc=tslug)
    adapter = _Fake()
    monkeypatch.setattr(pickup, "get_adapter", lambda name=None: adapter)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    sink = world["tmp"] / "alert.txt"
    script = world["tmp"] / "alert.py"
    script.write_text("import os, sys; open({0!r}, 'w').write("
                      "os.environ['TIDE_ALERT_ROUTE'] + '|' + sys.argv[-1])".format(str(sink)),
                      encoding="utf-8")
    monkeypatch.setenv(ah.ALERT_CMD_ENV, "{0} {1}".format(sys.executable, script))
    assert cli.main(["handoff", "continue", "--auto"]) == 0
    out = capsys.readouterr().out
    assert adapter.spawned is None
    assert "НЕ поднимаю" in out and "какой вариант?" in out
    (rec,) = hq.list_offers(world["home"])
    assert rec["status"] == hq.STATUS_OFFERED          # оффер висит для ▶
    sent = sink.read_text(encoding="utf-8")
    assert sent.startswith("human|") and "встала" in sent
    desk = artifact.render_list(world["proj"])
    assert "встала" in desk


def test_auto_stands_after_three_stalled_hops(world, monkeypatch, capsys):
    pp = world["sess"] / "arc.md"
    fields.set_field(pp, ah.AUTO_CHAIN_FIELD, "3")
    fields.set_field(pp, ah.PROGRESS_MARK_FIELD, "0")
    adapter = _Fake()
    monkeypatch.setattr(pickup, "get_adapter", lambda name=None: adapter)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    assert cli.main(["handoff", "continue", "--auto"]) == 0
    out = capsys.readouterr().out
    assert adapter.spawned is None
    assert "без продвижения" in out


def test_auto_without_own_session_fails_loud(world, monkeypatch, capsys):
    assert cli.main(["handoff", "continue", "--auto", "--dry-run"]) != 0


# --- адрес нити ---------------------------------------------------------------------

def test_holder_finds_newest_session_and_its_ao_handle(world, monkeypatch, capsys):
    sid2 = "22222222-0000-4000-8000-00000000bbbb"
    s2 = stream.new_session(world["proj"], "seam", "s2", claude_session=sid2)
    registry.record(world["home"], sid2, "proj-9", "{0}/{1}".format(
        world["thread"].name, s2.name))
    rec = holder.holder(world["proj"], "seam", home=world["home"],
                        alive=lambda h: h == "proj-9")
    assert rec["arc"].endswith(s2.name)
    assert rec["claude_session"] == sid2
    assert rec["ao_session"] == "proj-9" and rec["alive"] is True
    # запасной путь — тайник AO
    sessions.remember_ao_sid(world["home"], "management-3", SID)
    assert holder.ao_handle_for(world["home"], SID) == "management-3"
    # CLI
    from tide.adapters import ao as ao_mod
    monkeypatch.setattr(ao_mod.AoAdapter, "alive", lambda self, h: False)
    assert cli.main(["thread", "holder", "seam", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ao_session"] == "proj-9" and out["alive"] is False


def test_thread_screen_still_works_beside_holder(world, capsys):
    assert cli.main(["thread", "seam"]) == 0
    assert capsys.readouterr().out.strip()


# --- имя первой AO-сессии ------------------------------------------------------------

class _AoFake:
    def __init__(self, name):
        self.name, self.renamed = name, []

    def session(self, handle):
        return {"id": handle, "displayName": self.name}

    def rename(self, handle, name):
        self.renamed.append((handle, name))
        return True


def test_first_ao_session_takes_the_title(world, monkeypatch):
    monkeypatch.setenv("AO_SESSION_ID", "management-1")
    # SOC-72: оба номера — «проект — нить · NN · <AO-id>», и в паспорте тоже
    title = fields.read_field(world["sess"] / "arc.md", "title") + " · management-1"
    fake = _AoFake("management-1")
    assert session_start._ao_name_by_title(world["proj"], SID, adapter=fake) == title
    assert fake.renamed == [("management-1", title)]
    assert fields.read_field(world["sess"] / "arc.md", "title") == title
    same = _AoFake(title)
    assert session_start._ao_name_by_title(world["proj"], SID, adapter=same) is None
    assert same.renamed == []
    monkeypatch.delenv("AO_SESSION_ID")
    assert session_start._ao_name_by_title(world["proj"], SID, adapter=fake) is None
