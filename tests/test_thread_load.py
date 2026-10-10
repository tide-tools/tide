"""SOC-153: `tide thread load` — сборщик нагрузки нити.

Фиксированный вход (--now, --linear файл-фикстура, транскрипты, офферы, почта,
расход во временном HOME) → выход --json по контракту load-format.md
(continuity-audit · 04): ровно те поля, те типы, правило tuned «2 из 6».
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from tide import cli
from tide.arc import thread_load

NOW = dt.datetime(2026, 10, 9, 23, 0, tzinfo=dt.timezone.utc)

# Контракт load-format.md — ключи каждого уровня.
TOP = {"generated_at", "window_h", "rule", "sources", "threads"}
THREAD = {"project", "thread", "name", "head", "handoffs", "mail", "linear", "workers",
          "incoming_24h", "incoming_detail", "open_tails", "spend", "stuck", "signals",
          "flag", "split"}
HEAD = {"arc", "title", "session", "context_k", "context_at"}
HANDOFFS = {"count_24h", "median_interval_h", "last_at", "auto", "offers",
            "context_at_handoff_k", "live_workers_at_handoff"}
MAIL = {"letters", "unread", "oldest_unacked_min", "max_ack_delay_min"}
LINEAR = {"in_progress", "in_review", "tasks", "source"}
TASK = {"id", "status", "title", "lproject", "src"}
WORKER = {"id", "what", "since"}
INCOMING = {"deliveries", "human", "teammate"}
SPEND = {"usd_today", "usd_h5", "usd_per_h", "share_project", "share_total"}
STUCK = {"stalls", "max_stall_min", "repeats", "role_denies", "hook_errors", "idle_now_min",
         "sessions", "recent_3h", "markers"}
STUCK_SESSION = {"arc", "session", "stalls", "max_stall_min", "idle_now_min", "repeats",
                 "role_denies", "hook_errors"}
SIGNAL = {"signal", "value", "rule"}
SPLIT = {"basis", "text", "a", "b"}
PART = {"name", "tasks", "tails"}
TUNED = {"handoffs_24h", "linear_in_progress", "mail_oldest_unacked_min", "spend_usd_per_h",
         "open_tails", "stuck_markers"}


def _z(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _local(t: dt.datetime) -> str:
    return t.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def _ago(**kw) -> dt.datetime:
    return NOW - dt.timedelta(**kw)


def _jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # компактно, как пишет claude: сборщик фильтрует строки по '"role":"assistant"'
    path.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n"
                            for r in rows), encoding="utf-8")


def _assistant(t, content, usage=None):
    msg = {"role": "assistant", "model": "claude-opus-5-5", "content": content}
    if usage:
        msg["usage"] = usage
    return {"type": "assistant", "timestamp": _z(t), "message": msg}


def _user(t, content):
    return {"type": "user", "timestamp": _z(t), "message": {"role": "user", "content": content}}


def _agent(tid, desc):
    return {"type": "tool_use", "id": tid, "name": "Agent", "input": {"description": desc}}


def _launched(tid, agent):
    return {"type": "tool_result", "tool_use_id": tid,
            "content": "Async agent launched successfully.\nagentId: {0}".format(agent)}


def _notif(agent, interim=False):
    tail = " result below may be interim" if interim else ""
    return ("<task-notification><task-id>{0}</task-id><status>completed</status>"
            "<summary>итог {0}{1}</summary></task-notification>".format(agent, tail))


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ctl = home / "Documents" / "tide-home"
    (ctl / ".tide" / "handoffs").mkdir(parents=True)
    (ctl / "roster.md").write_text("# roster\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TIDE_HOME", str(ctl))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("TIDE_LINEAR_CACHE", raising=False)
    monkeypatch.chdir(tmp_path)

    proj = home / "Documents" / "projects" / "demo"
    thread = proj / ".tide" / "arcs" / "01-@alpha"
    for num, sid in (("01-alpha", "s-old"), ("02-alpha-2", "s-new")):
        d = thread / "arcs" / num
        d.mkdir(parents=True)
        (d / "arc.md").write_text("title: demo — alpha · {0}\nclaude-session: {1}\n".format(
            num[:2], sid), encoding="utf-8")
    quiet = proj / ".tide" / "arcs" / "02-@quiet" / "arcs" / "01-quiet"
    quiet.mkdir(parents=True)
    (quiet / "arc.md").write_text("title: quiet\nclaude-session: s-quiet\n", encoding="utf-8")

    tr = home / ".claude" / "projects" / "-demo"
    usage = {"input_tokens": 1000, "cache_creation_input_tokens": 9000,
             "cache_read_input_tokens": 150000}
    _jsonl(tr / "s-old.jsonl", [
        _user(_ago(hours=20), "Ты запущен tide — нить alpha"),
        _assistant(_ago(hours=19), [{"type": "text", "text": "ок"}],
                   {"input_tokens": 10, "cache_read_input_tokens": 200000}),
    ])
    _jsonl(tr / "s-new.jsonl", [
        _user(_ago(hours=2), "Ты запущен tide — нить alpha"),
        _user(_ago(minutes=90), "сделай доску"),
        _user(_ago(minutes=85), '<ao-session-delivery from="x">поручение</ao-session-delivery>'),
        _assistant(_ago(minutes=60), [_agent("tu1", "воркер A"), _agent("tu2", "воркер B"),
                                      _agent("tu4", "воркер D")], usage),
        _user(_ago(minutes=59), [_launched("tu1", "aaa1"), _launched("tu2", "bbb2"),
                                 _launched("tu4", "ddd4")]),
        _user(_ago(minutes=50), _notif("aaa1")),
        _user(_ago(minutes=49), _notif("bbb2", interim=True)),
        _assistant(_ago(minutes=10), [{"type": "text", "text": "жду"}], usage),
        # после --now — не в счёт
        _assistant(NOW + dt.timedelta(minutes=10), [_agent("tu9", "поздний")],
                   {"input_tokens": 999999}),
        _user(NOW + dt.timedelta(minutes=11), [_launched("tu9", "zzz9")]),
    ])
    _jsonl(tr / "s-quiet.jsonl", [_assistant(_ago(hours=40), [{"type": "text", "text": "x"}],
                                             usage)])
    old = (NOW - dt.timedelta(hours=48)).timestamp()
    os.utime(tr / "s-quiet.jsonl", (old, old))

    for i in range(8):
        (ctl / ".tide" / "handoffs" / "{0:03d}-alpha.md".format(i + 1)).write_text(
            "status: taken\narc: 01-@alpha/02-alpha-2\nproject: demo\ncreated: {0}\n"
            "from-session: s-old\nraised-by: {1}\nmode: continue\n".format(
                _local(_ago(hours=2 * i + 1)), "auto" if i % 2 else "human"),
            encoding="utf-8")

    _jsonl(thread / "inbox" / "log.jsonl", [
        {"ev": "sent", "num": 1, "at": _local(_ago(minutes=45)), "from": "main"},
        {"ev": "sent", "num": 2, "at": _local(_ago(minutes=200)), "from": "main"},
        {"ev": "ack", "num": 2, "at": _local(_ago(minutes=194))},
    ])

    def issue(i, title, status):
        return {"id": "SOC-{0}".format(i), "title": title, "status": status, "project": None,
                "taken": {"project": "demo", "thread": "alpha"}}

    linear = tmp_path / "linear-cache.json"
    linear.write_text(json.dumps({"fetched_at": "2026-10-09T22:30:00+00:00", "issues": [
        issue(1, "Доска: плашка", "In Progress"), issue(2, "Доска: кнопка", "In Progress"),
        issue(3, "Почта: квитанции", "In Progress"), issue(4, "Почта: тревога", "In Review"),
        {"id": "SOC-9", "title": "чужая", "status": "In Progress",
         "assigned": {"project": "demo", "thread": "other"}},
    ]}, ensure_ascii=False), encoding="utf-8")

    cost = tmp_path / "cost-tree.json"
    cost.write_text(json.dumps({"generated_at": "2026-10-09T19:55:00-03:00", "nodes": [
        {"key": "demo", "level": "project", "today": {"usd": 200.0}, "h5": {"usd": 80.0}},
        {"key": "demo/01-@alpha", "level": "thread", "today": {"usd": 100.0}, "h5": {"usd": 60.0}},
    ]}), encoding="utf-8")
    monkeypatch.setenv("TIDE_COST_TREE", str(cost))
    return dict(linear=linear)


def _run(capsys, *extra):
    rc = cli.main(["thread", "load", "--project", "demo", "--json", "--now", NOW.isoformat(),
                   *extra])
    assert rc == 0
    return json.loads(capsys.readouterr().out)


def test_json_matches_load_format_contract(world, capsys):
    d = _run(capsys, "--linear", str(world["linear"]))
    assert set(d) == TOP
    assert d["generated_at"] == thread_load.iso(NOW)
    assert d["window_h"] == 24
    assert d["rule"] == {"name": "tuned", "need": 2, "signals": {
        "handoffs_24h": ">= 8", "linear_in_progress": ">= 3", "mail_oldest_unacked_min": "> 30",
        "spend_usd_per_h": ">= 10.0", "open_tails": ">= 14", "stuck_markers": ">= 1"}}
    assert d["sources"] == {"linear": "2026-10-09T22:30:00+00:00",
                            "cost": "2026-10-09T19:55:00-03:00"}
    assert [t["thread"] for t in d["threads"]] == ["01-@alpha"]  # тихая нить не живая

    t = d["threads"][0]
    assert set(t) == THREAD
    assert (t["project"], t["name"]) == ("demo", "alpha")
    assert set(t["head"]) == HEAD
    assert t["head"]["arc"] == "02-alpha-2" and t["head"]["session"] == "s-new"
    assert t["head"]["context_k"] == 160  # fill_of последнего ответа до --now
    assert t["head"]["context_at"] == thread_load.iso(_ago(minutes=10))

    h = t["handoffs"]
    assert set(h) == HANDOFFS
    assert h["count_24h"] == 8 and h["auto"] == 4 and h["median_interval_h"] == 2.0
    assert len(h["offers"]) == 8 and all(isinstance(o, str) for o in h["offers"])
    assert h["context_at_handoff_k"] == [200] * 8
    assert h["live_workers_at_handoff"] == [0] * 8

    assert set(t["mail"]) == MAIL
    assert t["mail"] == {"letters": 2, "unread": 1, "oldest_unacked_min": 45.0,
                         "max_ack_delay_min": 6.0}

    lin = t["linear"]
    assert set(lin) == LINEAR
    assert (lin["in_progress"], lin["in_review"]) == (3, 1)
    assert all(set(x) == TASK and x["src"] == "linear" for x in lin["tasks"])
    assert lin["source"] == "linear-cache 2026-10-09T22:30:00+00:00"

    # aaa1 сдал итог; bbb2 — промежуточный итог (interim_workers) = жив; ddd4 без итога;
    # zzz9 запущен после --now
    assert [w["id"] for w in t["workers"]] == ["bbb2", "ddd4"]
    assert all(set(w) == WORKER for w in t["workers"])

    assert set(t["incoming_detail"]) == INCOMING
    assert t["incoming_detail"] == {"deliveries": 1, "human": 1, "teammate": 0}
    assert t["incoming_24h"] == 2
    assert t["open_tails"] is None  # записки нет — «нечем посчитать»

    assert set(t["spend"]) == SPEND
    assert t["spend"] == {"usd_today": 100.0, "usd_h5": 60.0, "usd_per_h": 12.0,
                          "share_project": 0.5, "share_total": 0.5}

    st = t["stuck"]
    assert set(st) == STUCK
    assert all(set(s) == STUCK_SESSION for s in st["sessions"])
    assert set(st["recent_3h"]) == {"stalls", "repeats", "role_denies"}
    assert isinstance(st["markers"], list)

    assert all(set(s) == SIGNAL for s in t["signals"])
    fired = {s["signal"] for s in t["signals"]}
    assert fired <= TUNED
    assert {"handoffs_24h", "linear_in_progress", "mail_oldest_unacked_min",
            "spend_usd_per_h"} <= fired
    assert t["flag"] is True

    sp = t["split"]
    assert set(sp) == SPLIT and set(sp["a"]) == PART and set(sp["b"]) == PART
    assert sp["basis"] == "linear"
    assert {sp["a"]["name"], sp["b"]["name"]} == {"Доска", "Почта"}


def test_draft_rule_and_seed_fallback(world, capsys):
    d = _run(capsys, "--rule", "draft", "--linear", "")
    assert d["rule"]["name"] == "draft"
    assert set(d["rule"]["signals"]) == {"handoff_interval_h", "linear_in_progress",
                                         "mail_oldest_unacked_min", "spend_share_project"}
    assert d["sources"]["linear"] == "seed"
    t = d["threads"][0]
    assert t["linear"]["tasks"] == [] and t["linear"]["source"].startswith("seed ")
    assert {s["signal"] for s in t["signals"]} == {"mail_oldest_unacked_min",
                                                   "spend_share_project"}


def test_human_output(world, capsys):
    rc = cli.main(["thread", "load", "--project", "demo", "--now", NOW.isoformat(),
                   "--linear", str(world["linear"])])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("нагрузка нитей на ")
    assert "!! demo/alpha" in out and "предлагаю разделить" in out


def test_human_has_no_none_for_empty_values(world, capsys):
    """Пустое в человеческом выводе — «—», слова None нет; в --json те же поля остаются null."""
    rc = cli.main(["thread", "load", "--project", "demo", "--now", NOW.isoformat(), "--linear", ""])
    assert rc == 0
    out = capsys.readouterr().out
    assert "None" not in out
    assert "хвосты — " in out

    d = _run(capsys, "--linear", "")
    t = d["threads"][0]
    assert t["open_tails"] is None and t["mail"]["max_ack_delay_min"] is not None
    assert "context_k" in t["head"] and "median_interval_h" in t["handoffs"]


def test_human_all_fields_empty():
    """Все поля строки нити пусты — ни одного None, ни одного падения."""
    d = dict(generated_at=None, rule=dict(name="tuned", need=2, signals={}), threads=[dict(
        project="p", thread="01-@t", name="t", flag=False,
        head=dict(context_k=None), handoffs=dict(count_24h=0, median_interval_h=None),
        mail=dict(unread=0, oldest_unacked_min=None), linear=dict(in_progress=0, in_review=0),
        workers=[], incoming_24h=0, open_tails=None, spend=dict(usd_per_h=None),
        stuck=dict(markers=[]), signals=[dict(signal="x", value=None, rule=">= 1")], split=None)])
    out = thread_load.human(d)
    assert "None" not in out
    assert "почта —" in out and "хвосты —" in out and "контекст — тыс." in out
    d["threads"][0]["mail"] = dict(unread=2, oldest_unacked_min=None)
    assert "None" not in thread_load.human(d)
