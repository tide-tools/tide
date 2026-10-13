"""SOC-271 / SOC-270: ``tide say``, ``tide topic``, ``tide spark --topic`` — обёртки над пультом.

Пульт не зовётся: :func:`tide.tg_pult.run` подменён, каталог пульта — tmp.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from tide import cli, say, tg_pult, topic


@pytest.fixture
def pult(tmp_path, monkeypatch):
    d = tmp_path / "tg-pult"
    d.mkdir()
    (d / "tg-alert").write_text("#!/bin/sh\n", encoding="utf-8")
    (d / "pult.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("TIDE_ALERT_CMD", str(d / "tg-alert"))
    monkeypatch.setattr(tg_pult, "caller_source", lambda: "management/manager-api/manager-api")
    calls = []
    replies = []

    def fake_run(cmd, *, env=None, input_text=None, cwd=None, timeout=60):
        calls.append({"cmd": cmd, "env": env, "input": input_text, "cwd": cwd})
        out = replies.pop(0) if replies else ""
        if isinstance(out, tuple):
            return subprocess.CompletedProcess(cmd, out[0], out[1], out[2])
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(tg_pult, "run", fake_run)
    return SimpleNamespace(dir=d, calls=calls, replies=replies)


# --- резолвер пульта ------------------------------------------------------------

def test_pult_dir_from_alert_cmd(pult):
    assert tg_pult.pult_dir() == pult.dir.resolve()
    assert tg_pult.alert_cmd() == [str(pult.dir / "tg-alert")]
    assert tg_pult.pult_python(pult.dir)[-1] == str(pult.dir / "pult.py")


def test_pult_dir_from_env_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("TIDE_ALERT_CMD", raising=False)
    monkeypatch.setenv("TIDE_PULT_DIR", str(tmp_path))
    assert tg_pult.pult_dir() == tmp_path


def test_pult_missing_is_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("TIDE_ALERT_CMD", raising=False)
    monkeypatch.delenv("TIDE_PULT_DIR", raising=False)
    monkeypatch.setenv("TIDE_HOME", str(tmp_path / "nohome"))
    with pytest.raises(tg_pult.PultError):
        tg_pult.pult_dir()


@pytest.mark.parametrize("name,want", [("topics", "factory"), ("agents", "pulse"), ("Tide Agents", "pulse"),
                                       ("factory", "factory"), ("groovamp", "groovamp")])
def test_group_aliases(name, want):
    assert tg_pult.group(name) == want


# --- tide say -------------------------------------------------------------------

def test_say_goes_to_dm_via_stdin_with_source(pult):
    pult.replies.append("q-abc123\n")
    res = say.say("--привет, это не флаг", wait=0)
    [c] = pult.calls
    assert c["cmd"] == [str(pult.dir / "tg-alert")]          # ни темы, ни группы — личка
    assert c["input"] == "--привет, это не флаг"
    assert c["env"]["TIDE_ALERT_ROUTE"] == "human"
    assert c["env"]["TIDE_ALERT_SOURCE"] == "management/manager-api/manager-api"
    assert res["qid"] == "q-abc123"


def test_say_flags_file_reply_to(pult, tmp_path):
    f = tmp_path / "pic.png"
    f.write_bytes(b"x")
    pult.replies.append("q-1\n")
    say.say("вот", files=[str(f)], reply_to=4970, wait=0)
    cmd = pult.calls[0]["cmd"]
    assert cmd[1:] == ["--file", str(f.resolve()), "--reply-to", "4970"]


def test_say_missing_file_refused_before_pult(pult):
    with pytest.raises(tg_pult.PultError):
        say.say("x", files=["/nope/none.png"], wait=0)
    assert pult.calls == []


def test_say_empty_refused(pult):
    with pytest.raises(tg_pult.PultError):
        say.say("   ", wait=0)


def test_say_alert_refusal_surfaces(pult):
    pult.replies.append((2, "", "tg-alert: empty text, nothing queued"))
    with pytest.raises(tg_pult.PultError, match="empty text"):
        say.say("x", wait=0)


def test_say_waits_for_tg_number_in_dialog(pult):
    pult.replies.append("q-wait1\n")
    dialog = pult.dir / "dialog.jsonl"
    dialog.write_text("", encoding="utf-8")

    def sleep(_s):
        dialog.write_text(json.dumps({"ts": "t", "dir": "out", "kind": "alert", "qid": "q-wait1",
                                      "tg_msg_ids": [4980]}) + "\n", encoding="utf-8")

    res = say.say("x", wait=5, sleep=sleep)
    assert res["tg"] == [4980]
    line = say.render(res)
    assert line.startswith("say: ушло в личку · tg 4980 · q-wait1")


def test_say_render_queued_when_not_sent_yet():
    line = say.render({"qid": "q-x", "tg": None, "source": "", "dialog": "/p/dialog.jsonl"})
    assert "в очереди пульта" in line and "q-x" in line


def test_say_cli_one_line(pult, capsys):
    pult.replies.append("q-cli\n")
    rc = cli.main(["say", "--no-wait", "текст", "из", "слов"])
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0 and len(out) == 1 and "q-cli" in out[0]
    assert pult.calls[0]["input"] == "текст из слов"


# --- tide topic -----------------------------------------------------------------

def _ok(result):
    return json.dumps({"ok": True, "result": result}, ensure_ascii=False) + "\n"


def test_topic_new_maps_group_and_route(pult, capsys):
    pult.replies.append(_ok({"id": 1550, "title": "Тест", "route": "management/signing", "group": "factory",
                             "how": "создана", "backup": "role-topics.json.bak-x"}))
    rc = cli.main(["topic", "new", "topics", "Тест", "--to", "management/signing"])
    c = pult.calls[0]
    assert c["cmd"][-8:] == ["topic-new", "--group", "factory", "--title", "Тест",
                             "--route", "management/signing", "--json"]
    assert c["cwd"] == pult.dir.resolve()
    out = capsys.readouterr().out
    assert rc == 0 and "id 1550" in out and "tide topics (factory)" in out and "management/signing" in out


def test_topic_new_multiword_title_and_dry(pult):
    pult.replies.append(_ok({"id": None, "title": "Два слова", "route": "a/b", "group": "pulse",
                             "how": "dry-run: …", "backup": ""}))
    cli.main(["topic", "new", "agents", "Два", "слова", "--to", "a/b", "--dry-run"])
    cmd = pult.calls[0]["cmd"]
    assert cmd[cmd.index("--title") + 1] == "Два слова" and "--dry-run" in cmd


def test_topic_new_needs_to(pult, capsys):
    assert cli.main(["topic", "new", "topics", "Тест"]) == 2
    assert pult.calls == []


def test_topic_route_rename_close_reopen_ls(pult):
    pult.replies += [_ok({"id": 5, "title": "T", "old": "x/y", "route": "a/b", "group": "pulse", "backup": ""}),
                     _ok({"id": 5, "title": "N", "old": "T", "group": "pulse", "how": "переименована", "backup": ""}),
                     _ok({"id": 5, "title": "N", "group": "pulse", "closed": True, "how": "закрыта", "backup": ""}),
                     _ok({"id": 5, "title": "N", "group": "pulse", "closed": False, "how": "открыта", "backup": ""}),
                     _ok([{"group": "pulse", "id": 5, "title": "N", "route": "a/b", "route_via": "topic",
                           "closed": False}])]
    assert cli.main(["topic", "route", "agents", "5", "--to", "a/b"]) == 0
    assert cli.main(["topic", "rename", "agents", "5", "N"]) == 0
    assert cli.main(["topic", "close", "agents", "N"]) == 0
    assert cli.main(["topic", "reopen", "agents", "N"]) == 0
    assert cli.main(["topic", "ls", "agents"]) == 0
    verbs = [next(a for a in c["cmd"] if a.startswith("topic-")) for c in pult.calls]
    assert verbs == ["topic-route", "topic-rename", "topic-close", "topic-reopen", "topic-ls"]
    assert pult.calls[2]["cmd"][-5:] == ["--group", "pulse", "--topic", "N", "--json"]


def test_topic_pult_error_is_exit_2(pult, capsys):
    pult.replies.append((2, json.dumps({"ok": False, "error": "в pulse нет темы «X»"}) + "\n", "x"))
    assert cli.main(["topic", "close", "agents", "X"]) == 2
    assert "нет темы" in capsys.readouterr().err


def test_topic_ls_without_group(pult, capsys):
    pult.replies.append(_ok([]))
    assert cli.main(["topic", "ls"]) == 0
    assert pult.calls[0]["cmd"][-2:] == ["topic-ls", "--json"]


# --- tide spark --topic ---------------------------------------------------------

def test_spark_topic_target_new_thread():
    a = SimpleNamespace(topic="agents", new_thread="Manager API", thread=None, project="management")
    assert topic.spark_topic_target(a) == {"group": "agents", "title": "Manager API",
                                           "route": "management/manager-api"}


def test_spark_topic_target_existing_thread_and_off():
    a = SimpleNamespace(topic="topics", new_thread=None, thread="signing", project="management")
    assert topic.spark_topic_target(a)["route"] == "management/signing"
    assert topic.spark_topic_target(SimpleNamespace(topic=None)) is None


def _spark_parser(monkeypatch, rc):
    import argparse

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command")
    sp = sub.add_parser("spark")
    sp.add_argument("project")
    sp.add_argument("--new-thread", dest="new_thread")
    sp.add_argument("--thread")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true")
    sp.set_defaults(func=lambda args: rc)       # настоящий spark не зовём
    topic.register_spark_topic(sub)
    return ap


def test_spark_topic_after_ok_spark(pult, monkeypatch, capsys):
    ap = _spark_parser(monkeypatch, 0)
    pult.replies.append(_ok({"id": None, "title": "x", "route": "management/x", "group": "pulse",
                             "how": "dry-run: найду или создам", "backup": ""}))
    args = ap.parse_args(["spark", "management", "--new-thread", "x", "--dry-run", "--topic"])
    assert args.func(args) == 0
    cmd = pult.calls[0]["cmd"]
    assert cmd[cmd.index("--group") + 1] == "pulse" and cmd[cmd.index("--route") + 1] == "management/x"
    assert "--dry-run" in cmd
    assert "spark --topic" in capsys.readouterr().out


def test_spark_topic_skipped_when_spark_failed(pult, monkeypatch):
    ap = _spark_parser(monkeypatch, 1)
    args = ap.parse_args(["spark", "management", "--new-thread", "x", "--topic", "topics"])
    assert args.func(args) == 1
    assert pult.calls == []


def test_spark_real_parser_has_topic_flag():
    p = cli.build_parser()
    args = p.parse_args(["spark", "management", "--new-thread", "x", "--topic"])
    assert args.topic == "agents"
