"""SOC-277: лок очереди пингов почты — короткий, с тайм-аутом и тревогой.

Живой случай 12.10: под локом ``mail-ping-queue.json.lock`` держатель перечитывал
журнал ящика на каждую строку очереди — ~65 процессов почты стояли конвоем до 50 мин."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from datetime import timedelta
from pathlib import Path

import pytest

from tide import fields, mail, registry, roster
from tide.arc import stream
from tide.launcher import autohandoff


@pytest.fixture
def world(tmp_path, monkeypatch):
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    build_tide_skeleton(proj, name="proj")
    roster.add(home, "proj", str(proj))
    stream.new_thread(proj, "handoffs", goal="handoffs")
    s = stream.new_session(proj, "handoffs", "handoffs-1", claude_session="sid-h")
    fields.set_field(s / "arc.md", "title", "proj — handoffs · 01")
    registry.record(home, "sid-h", "proj-41", "handoffs")
    man = tmp_path / "manager.json"
    man.write_text(json.dumps({"ao_session": "management-28"}), encoding="utf-8")
    monkeypatch.setenv("TIDE_MANAGER_FILE", str(man))
    monkeypatch.setattr(mail, "_MANAGER_AO", [])
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv(mail.WATCH_ENV, "0")
    monkeypatch.setenv(mail.BATCH_ENV, "1500")
    for env in (mail.LOCK_ENV, mail.LOCK_ALERT_ENV, mail.QUEUE_ALERT_ENV, mail.ALERT_GAP_ENV):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.chdir(proj)
    sent, alerts = [], []
    monkeypatch.setattr(autohandoff, "_ao_send", lambda h, t: sent.append((h, t)) or 0)
    monkeypatch.setattr(autohandoff, "send_alert",
                        lambda text, **kw: alerts.append((text, kw)) or 0)
    return {"home": home, "sent": sent, "alerts": alerts, "tdir": mail.thread_dir("handoffs"),
            "queue": home / ".tide" / "state" / mail.QUEUE_NAME}


def _hold_lock(home: Path, held_for: float = 0.0, sleep: float = 60.0) -> subprocess.Popen:
    """Чужой процесс держит лок очереди (и записку держателя «держу уже *held_for* с»)."""
    lock = mail._queue_path(home).with_suffix(".json.lock")
    holder = mail._holder_path(home)
    lock.parent.mkdir(parents=True, exist_ok=True)
    code = textwrap.dedent("""
        import fcntl, json, os, sys, time
        fh = open(sys.argv[1], "a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        open(sys.argv[2], "w").write(json.dumps({"pid": os.getpid(), "cmd": "mail send x",
                                                  "since": time.time() - float(sys.argv[3])}))
        print("held", flush=True)
        time.sleep(float(sys.argv[4]))
    """)
    proc = subprocess.Popen([sys.executable, "-c", code, str(lock), str(holder),
                             str(held_for), str(sleep)], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_flush_of_a_big_queue_over_a_big_journal_is_fast(world):
    """Очередь 250 строк, журнал ящика > 400 КБ: проход сброса < 2 с, лок — миг."""
    tdir, home = world["tdir"], world["home"]
    box = tdir / "inbox"
    box.mkdir(parents=True, exist_ok=True)
    pad = "x" * 1200
    lines, rows = [], []
    for i in range(1, 251):
        num = "{0:03d}".format(i)
        (box / "{0}-от-board.md".format(num)).write_text("письмо {0}\n".format(i))
        lines.append({"ev": "sent", "num": num, "at": "2026-10-12T20:00:00Z", "from": "board",
                      "first": "письмо {0} {1}".format(i, pad), "bytes": 10})
        lines += [{"ev": "ping", "num": num, "at": "2026-10-12T20:00:0{0}Z".format(k),
                   "to": "proj-41", "code": 0, "kind": "retry", "bytes": 90} for k in range(3)]
        if i % 2:
            lines.append({"ev": "ack", "num": num, "at": "2026-10-12T20:01:00Z", "by": "proj-41"})
        rows.append({"tdir": str(tdir), "addr": "proj/handoffs", "num": num, "sender": "board",
                     "first": "письмо", "at": "2026-10-12T20:00:00Z", "text": "почта"})
    (box / "log.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n"
                                           for e in lines), encoding="utf-8")
    assert (box / "log.jsonl").stat().st_size > 400_000
    world["queue"].parent.mkdir(parents=True, exist_ok=True)
    world["queue"].write_text(json.dumps({"pending": {"proj-41": rows}, "last": {}}))
    held = []
    enter, leave = mail._QueueLock.__enter__, mail._QueueLock.__exit__

    def timed_enter(self):
        out = enter(self)
        self._t0 = time.monotonic()
        return out

    def timed_exit(self, *exc):
        held.append(time.monotonic() - self._t0)
        return leave(self, *exc)

    mail._QueueLock.__enter__, mail._QueueLock.__exit__ = timed_enter, timed_exit
    try:
        t0 = time.monotonic()
        res = mail.flush_pings(home, now=mail._now() + timedelta(seconds=1600))
        took = time.monotonic() - t0
    finally:
        mail._QueueLock.__enter__, mail._QueueLock.__exit__ = enter, leave
    assert took < 2.0, took
    assert max(held) < 0.5, held
    assert res == [{"to": "proj-41", "n": 125, "sent": True, "code": 0}]
    assert world["sent"][0][1].startswith("почта: 125 новых")


def test_send_does_not_hang_on_a_held_lock_and_the_ping_waits_in_the_spool(world):
    """Лок держит другой процесс: send выходит за ~10 с (по умолчанию), письмо в
    ящике, пинг — в лотке; следующий сброс кладёт его в очередь."""
    home = world["home"]
    assert mail.lock_timeout_sec() == 10.0
    mail.send("handoffs", "раз", sender="board")     # первый пинг — сразу, окно пошло
    proc = _hold_lock(home)
    try:
        t0 = time.monotonic()
        out = mail.send("handoffs", "два", sender="board")
        took = time.monotonic() - t0
    finally:
        proc.kill()
        proc.wait()
    assert took <= 12.0, took
    assert out["ping"]["queued"] and out["ping"]["spooled"]
    assert Path(out["path"]).is_file()
    assert len(mail._spool_files(home)) == 1
    assert world["alerts"] == []                      # держат «только что» — не тревога
    res = mail.flush_pings(home)
    assert res[0]["why"].startswith("окно ещё")
    assert not mail._spool_files(home)
    q = json.loads(world["queue"].read_text())
    assert [r["num"] for r in q["pending"]["proj-41"]] == ["002"]
    res = mail.flush_pings(home, now=mail._now() + timedelta(seconds=1600))
    assert res == [{"to": "proj-41", "n": 1, "sent": True, "code": 0}]


def test_lock_held_too_long_alerts_the_manager_once_per_gap(world, monkeypatch):
    home = world["home"]
    monkeypatch.setenv(mail.LOCK_ENV, "0.3")
    mail.send("handoffs", "раз", sender="board")
    proc = _hold_lock(home, held_for=120)
    try:
        t0 = time.monotonic()
        mail.send("handoffs", "два", sender="board")
        mail.send("handoffs", "три", sender="board")
        res = mail.flush_pings(home)
        assert time.monotonic() - t0 < 3.0
    finally:
        proc.kill()
        proc.wait()
    assert res == [{"to": "*", "n": 0, "sent": False, "why": "очередь занята — сброс позже"}]
    assert len(world["alerts"]) == 1                  # не спамим: раз в 30 мин
    text, kw = world["alerts"][0]
    assert kw["route"] == "manager" and kw["urgent"]
    assert "почта встала" in text and "уже 120 с" in text and "mail send x" in text
    assert len(mail._spool_files(home)) == 2
    # окно тревоги вышло — следующая тревога пойдёт
    f = home / ".tide" / "state" / mail.QUEUE_ALERT_NAME
    f.write_text(json.dumps({"at_ts": time.time() - 31 * 60}))
    proc = _hold_lock(home, held_for=120)
    try:
        mail.flush_pings(home)
    finally:
        proc.kill()
        proc.wait()
    assert len(world["alerts"]) == 2


def test_growing_queue_alerts_the_manager(world, monkeypatch):
    monkeypatch.setenv(mail.QUEUE_ALERT_ENV, "3")
    for t in ("раз", "два", "три", "четыре"):
        mail.send("handoffs", t, sender="board")
    assert len(world["alerts"]) == 1
    assert "в очереди пингов 3 строк" in world["alerts"][0][0]
