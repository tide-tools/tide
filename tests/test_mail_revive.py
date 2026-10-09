"""SOC-143: повтор почты поднимается сам после смерти ожидальщика (kill, перезагрузка)."""

from __future__ import annotations

import io
import json
import sys
from datetime import timedelta

import pytest

from tide import cli, mail
from tests.test_mail import world  # noqa: F401 — общая tmp-фикстура


@pytest.fixture
def spawned(world, monkeypatch):  # noqa: F811
    monkeypatch.setenv(mail.WATCH_ENV, "1")
    calls = []
    monkeypatch.setattr(mail, "_spawn_watch",
                        lambda tdir, num=None: calls.append(tdir) or 4242)
    return calls


def test_revive_spawns_only_when_pending_and_no_watcher(world, spawned):  # noqa: F811
    tdir = world["tdir"]
    assert mail.revive(tdir) is None and spawned == []          # ящик пуст
    mail.send("handoffs", "письмо", sender="m", watch=False)
    assert mail.revive(tdir) == 4242 and spawned == [tdir]
    held = mail._try_lock(tdir)                                 # «живой» ожидальщик
    try:
        assert mail.watcher_alive(tdir)
        assert mail.revive(tdir) is None and len(spawned) == 1
    finally:
        held.close()                                            # «kill»: лок снят ядром
    assert not mail.watcher_alive(tdir)
    assert mail.revive(tdir) == 4242 and len(spawned) == 2
    mail.read("handoffs", 1, by="x")
    assert mail.revive(tdir) is None and len(spawned) == 2      # прочитано — не ждём


def test_revive_off_by_env(world, spawned, monkeypatch):  # noqa: F811
    mail.send("handoffs", "a", sender="m", watch=False)
    monkeypatch.setenv(mail.WATCH_ENV, "0")
    assert mail.revive(world["tdir"]) is None and spawned == []


def test_alerted_letter_is_not_pending(world, spawned):  # noqa: F811
    mail.send("handoffs", "a", sender="m", watch=False)
    mail._append(world["tdir"], {"ev": "alert", "num": "001", "at": "x"})
    assert mail.pending(world["tdir"]) == [] and mail.revive(world["tdir"]) is None


def test_session_hooks_revive_the_project(world, spawned, monkeypatch, capsys):  # noqa: F811
    from tide.hooks import handed_off, session_start

    mail.send("handoffs", "a", sender="m", watch=False)
    # SessionStart чужой сессии — подъём всё равно по всему проекту
    session_start.render(world["proj"], "executor", session="sid-other")
    assert spawned == [world["tdir"]]
    monkeypatch.setattr(handed_off, "cmd_prompt", lambda a: 0)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "sid-h1"})))
    assert mail.cmd_prompt_hook(None) == 0
    assert "почта: 1 непрочитанных" in capsys.readouterr().out
    assert len(spawned) == 2


def test_every_mail_cli_call_revives(world, spawned, capsys):  # noqa: F811
    mail.send("handoffs", "a", sender="m", watch=False)
    assert cli.main(["mail", "ls", "handoffs"]) == 0
    assert spawned and all(t == world["tdir"] for t in spawned)


def test_send_does_not_double_spawn_with_live_watcher(world, spawned):  # noqa: F811
    held = mail._try_lock(world["tdir"])
    try:
        out = mail.send("handoffs", "a", sender="m")
        assert out["watcher"] is None and spawned == []
    finally:
        held.close()


def test_watch_box_busy_when_another_holds_lock(world):  # noqa: F811
    mail.send("handoffs", "a", sender="m")
    held = mail._try_lock(world["tdir"])
    try:
        assert mail.watch_box(world["tdir"], minutes=15, sleep=lambda s: None) == "busy"
    finally:
        held.close()


def test_restarted_watcher_still_retries_twice_and_alerts(world, monkeypatch):  # noqa: F811
    """Ожидальщик умер сразу после отправки; новый (поднятый хуком) проходит всё."""
    mail.send("handoffs", "срочно", sender="m")
    tdir = world["tdir"]
    clock = {"t": mail._now()}
    real_step = mail.step

    def fake_sleep(sec):
        clock["t"] = clock["t"] + timedelta(seconds=sec * 5)   # прыжок дальше срока

    def step_at(tdir_, num, minutes=None, now=None):
        # пинги штампуются настоящим временем — «сейчас» всегда впереди них
        clock["t"] = max(clock["t"], mail._now()) + timedelta(minutes=16)
        return real_step(tdir_, num, minutes=minutes, now=clock["t"])

    monkeypatch.setattr(mail, "step", step_at)
    assert mail.watch_box(tdir, minutes=15, sleep=fake_sleep) == "idle"
    assert len(world["sent"]) == 1 + mail.MAX_RETRIES
    (text, kw), = world["alerts"]
    assert kw["route"] == "manager" and "почта 001" in text
    assert not (tdir / "inbox" / mail.PIDFILE_NAME).exists()
    assert not mail.watcher_alive(tdir)
