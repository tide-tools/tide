"""SOC-234: карточка автономного цикла — свёртка, CLI, паспорт, наследование, закрытие отчётом, control."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tide import cli, cycle, fields
from tide.arc import stream
from tide.launcher import handoff, handoff_report as hr

from tests.test_handoff_autospawn import world  # noqa: F401  fixture

CONTROL = Path.home() / "Documents" / "management" / "tools" / "control"


@pytest.fixture
def sess(world, monkeypatch, tmp_path):
    cyc = tmp_path / "cycles.jsonl"
    monkeypatch.setenv(cycle.CYCLES_FILE_ENV, str(cyc))
    monkeypatch.setenv(hr.REPORTS_FILE_ENV, str(tmp_path / "reports.jsonl"))
    monkeypatch.delenv(hr.NO_REPORT_ENV, raising=False)
    monkeypatch.setenv("AO_SESSION_ID", "proj-7")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-1")
    entry = stream.new_session(world["proj"], "seam", "s1", goal="g")
    fields.set_field(entry / "arc.md", "claude-session", "sid-1")
    return {"entry": entry, "cyc": cyc, **world}


def _ev(p):
    return [json.loads(x) for x in p.read_text().splitlines()]


def test_fold_events():
    evs = [
        {"ev": "start", "id": "c1", "at": "2026-10-12T10:00:00+00:00", "soc": "SOC-1", "estimate_pct": 0.5, "due": None},
        {"ev": "extend", "id": "c1", "at": "t2", "estimate_pct": 1.0, "note": "шире"},
        {"ev": "mode", "id": "c1", "at": "t3", "mode": "talk"},
        {"ev": "end", "id": "ghost", "at": "t4", "status": "done"},     # без start — пропуск
        {"ev": "start", "id": "c2", "at": "t5", "soc": "SOC-2"},
        {"ev": "end", "id": "c2", "at": "t6", "status": "done", "report": "r-1", "note": "ок"},
    ]
    cards = cycle.fold(evs)
    assert list(cards) == ["c1", "c2"]
    assert cards["c1"]["status"] == "active" and cards["c1"]["estimate_pct"] == 1.0
    assert cards["c1"]["mode"] == "talk" and cards["c1"]["notes"] == ["шире"]
    assert cards["c2"]["status"] == "done" and cards["c2"]["report"] == "r-1"
    assert cycle.active_for_soc(cards, "soc-1")["id"] == "c1"
    assert cycle.active_for_soc(cards, "SOC-2") is None


def test_parse_due():
    now = datetime.fromisoformat("2026-10-12T12:00:00-03:00")
    assert cycle.parse_due("13.10 09:30", now).startswith("2026-10-13T09:30:00")
    assert cycle.parse_due("01.10 09:30", now).startswith("2027-10-01")
    assert cycle.parse_due("2026-10-12T20:00:00+00:00", now) == "2026-10-12T20:00:00+00:00"
    assert cycle.parse_due(None) is None
    with pytest.raises(cycle.CycleError):
        cycle.parse_due("завтра")


def test_start_writes_event_and_passport(sess, capsys):
    rc = cli.main(["cycle", "start", "--soc", "SOC-234", "--done-when", "тесты зелёные",
                   "--estimate", "0,6", "--due", "13.10 09:00", "--by", "lead"])
    assert rc == 0
    (ev,) = _ev(sess["cyc"])
    assert ev["ev"] == "start" and ev["soc"] == "SOC-234" and ev["id"].startswith("c-")
    assert ev["project"] == "proj" and ev["thread"] == "01-@seam" and ev["session"] == "01-s1"
    assert ev["ao"] == "proj-7" and ev["estimate_pct"] == 0.6 and ev["done_when"] == "тесты зелёные"
    assert ev["by"] == "lead" and ev["due"][5:16] == "10-13T09:00"
    assert fields.read_field(sess["entry"] / "arc.md", "cycle") == ev["id"]


def test_one_active_per_soc(sess, capsys):
    assert cli.main(["cycle", "start", "--soc", "SOC-9"]) == 0
    assert cli.main(["cycle", "start", "--soc", "SOC-9"]) == 1
    assert "уже идёт цикл" in capsys.readouterr().err
    assert len(_ev(sess["cyc"])) == 1
    assert cli.main(["cycle", "start", "--soc", "SOC-10"]) == 0


def test_start_by_other_goes_to_last_session_of_thread(sess, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "manager-sid")     # не рабочий
    ev = cycle.start(soc="SOC-3", thread="proj/seam")
    assert ev["session"] == "01-s1" and ev["ao"] == ""
    assert fields.read_field(sess["entry"] / "arc.md", "cycle") == ev["id"]
    ev2 = cycle.start(soc="SOC-4", thread="proj/nonexistent")       # нити нет — только запись
    assert ev2["session"] == ""


def test_extend_end_show_ls(sess, capsys):
    cli.main(["cycle", "start", "--soc", "SOC-5", "--estimate", "1"])
    cid = _ev(sess["cyc"])[0]["id"]
    assert cli.main(["cycle", "extend", cid, "--estimate", "2", "--note", "вдвое"]) == 0
    assert cli.main(["cycle", "extend", cid]) == 1
    capsys.readouterr()
    cli.main(["cycle", "ls"])
    assert "SOC-5" in capsys.readouterr().out
    cli.main(["cycle", "show", "--soc", "SOC-5"])
    out = capsys.readouterr().out
    assert "оценка 2%" in out and "вдвое" in out
    assert cli.main(["cycle", "end", cid, "--status", "user", "--note", "слово Гриши"]) == 0
    assert cli.main(["cycle", "end", cid, "--status", "done"]) == 1      # уже закрыт
    assert cli.main(["cycle", "extend", cid, "--note", "x"]) == 1
    capsys.readouterr()
    cli.main(["cycle", "ls"])
    assert "активных циклов нет" in capsys.readouterr().out
    cli.main(["cycle", "ls", "--all"])
    assert "SOC-5" in capsys.readouterr().out
    assert [e["ev"] for e in _ev(sess["cyc"])] == ["start", "extend", "end"]
    assert cycle.state(sess["cyc"])[cid]["status"] == "user"
    cli.main(["cycle", "start", "--soc", "SOC-5"])        # после end можно снова
    assert len(_ev(sess["cyc"])) == 4


def test_cycle_field_is_known(sess):
    assert "cycle" in fields.KNOWN_KEYS
    assert fields.read_field(sess["entry"] / "arc.md", "cycle") is None


def _continue(sess):
    res = handoff.run_handoff(sess["proj"], arc_ref="seam", mode="continue",
                              from_session="sid-1", spawn=False)
    assert res.offer_path
    return stream.last_session(sess["proj"], "seam")


def test_continue_inherits_cycle_and_eta(sess):
    cycle.start(soc="SOC-6")
    fields.set_field(sess["entry"] / "arc.md", "eta", "2026-10-13T10:00:00-03:00")
    new = _continue(sess)
    assert new != sess["entry"]
    cid = fields.read_field(sess["entry"] / "arc.md", "cycle")
    assert fields.read_field(new / "arc.md", "cycle") == cid
    assert fields.read_field(new / "arc.md", "eta") == "2026-10-13T10:00:00-03:00"


def test_continue_without_cycle_adds_nothing_and_born_fields_win(sess):
    new = _continue(sess)
    assert fields.read_field(new / "arc.md", "cycle") is None
    cycle.start(soc="SOC-7")
    res = handoff.run_handoff(sess["proj"], arc_ref="seam", mode="continue", from_session="sid-1",
                              spawn=False, born_fields={"cycle": "forced"})
    assert fields.read_field(stream.last_session(sess["proj"], "seam") / "arc.md", "cycle") == "forced"


def test_new_fork_does_not_inherit(sess):
    cycle.start(soc="SOC-8")
    handoff.run_handoff(sess["proj"], arc_ref="seam", mode="new", from_session="sid-1", spawn=False)
    assert fields.read_field(stream.last_session(sess["proj"], "seam") / "arc.md", "cycle") is None


def test_work_report_done_closes_cycle(sess, capsys):
    ev = cycle.start(soc="SOC-11")
    assert cli.main(["work-report", "--kind", "handoff", "--done", "половина", "--soc", "SOC-11"]) == 0
    assert cycle.state(sess["cyc"])[ev["id"]]["status"] == "active"     # handoff не закрывает
    assert cli.main(["work-report", "--kind", "done", "--done", "другая задача", "--soc", "SOC-99"]) == 0
    assert cycle.state(sess["cyc"])[ev["id"]]["status"] == "active"     # чужой SOC
    capsys.readouterr()
    assert cli.main(["work-report", "--kind", "done", "--done", "готово", "--soc", "SOC-11"]) == 0
    assert "цикл {0} сдан".format(ev["id"]) in capsys.readouterr().out
    card = cycle.state(sess["cyc"])[ev["id"]]
    assert card["status"] == "done" and card["report"].startswith("r-")
    last = _ev(sess["cyc"])[-1]
    assert last["ev"] == "end" and last["status"] == "done"


def test_work_report_kind_cycle_closes_and_foreign_session_does_not(sess):
    ev = cycle.start(soc="SOC-12")
    other = stream.new_session(sess["proj"], "seam", "s2", goal="g")          # без поля cycle
    fields.set_field(other / "arc.md", "claude-session", "sid-2")
    rec = hr.write_report(other, done="x", soc=["SOC-12"], kind="done")
    assert cycle.close_by_report(other, rec, sess["cyc"]) is None
    rec = hr.write_report(sess["entry"], done="y", soc=["SOC-12"], kind="cycle")
    assert cycle.close_by_report(sess["entry"], rec, sess["cyc"])["status"] == "done"


# --- control ----------------------------------------------------------------------

def _control():
    loader = importlib.machinery.SourceFileLoader("control_mod", str(CONTROL))
    spec = importlib.util.spec_from_loader("control_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not CONTROL.exists(), reason="control нет")
def test_control_cycles_section(sess, monkeypatch, tmp_path):
    ctl = _control()
    cyc = tmp_path / "ctl-cycles.jsonl"
    monkeypatch.setattr(ctl, "CYCLES", str(cyc))
    monkeypatch.setattr(ctl, "roster", lambda: {"proj": str(sess["proj"])})
    now = datetime.now().astimezone()
    old = (now - timedelta(hours=5)).isoformat()
    rows = [
        {"ev": "start", "id": "c-a", "at": old, "soc": "SOC-1", "project": "proj", "thread": "01-@seam",
         "estimate_pct": 0.5, "due": (now - timedelta(hours=1)).isoformat()},
        {"ev": "start", "id": "c-b", "at": old, "soc": "SOC-2", "project": "proj", "thread": "01-@seam",
         "estimate_pct": 1, "due": (now + timedelta(hours=9)).isoformat()},
        {"ev": "end", "id": "c-b", "at": now.isoformat(), "status": "done", "report": "r-1"},
        {"ev": "start", "id": "c-c", "at": old, "soc": "SOC-3", "project": "proj", "thread": "01-@seam"},
        {"ev": "end", "id": "c-c", "at": old, "status": "stopped"},
    ]
    cyc.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    fields.set_field(sess["entry"] / "arc.md", "cycle", "c-a")
    fields.set_field(sess["entry"] / "arc.md", "offloaded-at", old)
    out = ctl.sec_cycles(now - timedelta(hours=1))
    assert len(out) == 2                                    # c-c (остановлен) не виден
    a = next(r for r in out if "SOC-1" in r)
    assert "ПРОСРОЧЕН" in a and "без движения 5 ч" in a and "расход: —" in a and "0.5%" in a
    b = next(r for r in out if "SOC-2" in r)
    assert "СДАН" in b
    assert ctl.sec_cycles(now + timedelta(hours=1)) == [a]  # сданный до wm уже не показываем
