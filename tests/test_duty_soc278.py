"""SOC-278: «дежурный» — надстройка сторожа: мяч/next в вердикте, толчок «мяч у тебя»,
письмо «мяч у менеджера», ❓ при диалоге, разбор супервайзеру, статус duty.json,
статус почты без дубля тревоги, изоляция сбоев. AO, почта и пульт — подделки."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from tide import duty
from tide import watch_stuck as ws

from tests.test_watch_stuck import (  # noqa: F401 — фикстура world
    SID, T0, ended_rows, session, tool_use, user_text, world, write_rows)


class F:
    def __init__(self):
        self.said, self.mailed, self.to, self.alerts = [], [], [], []
        self.alert_ok = True

    def say(self, h, t):
        self.said.append((h, t))
        return True

    def mail(self, t):
        self.mailed.append(t)
        return True

    def mail_to(self, ref, t):
        self.to.append((ref, t))
        return True

    def alert(self, t, source):
        self.alerts.append((t, source))
        return self.alert_ok


def set_passport(world, cursor, nxt="- проверить тесты и отчитаться"):
    (world["entry"] / "arc.md").write_text(
        "# 23\n\ntitle: proj — designer · 23\nstatus: active\nclaude-session: {0}\n\n"
        "## cursor — resume here\n{1}\n\n## next\n{2}\n\n## context\n- п\n".format(SID, cursor, nxt),
        encoding="utf-8")


def go(world, f, minutes, s=None, **kw):
    return ws.run_once(sessions=s or [session()], now=T0 + timedelta(minutes=minutes),
                       say=f.say, mail=f.mail, mail_to=f.mail_to, alert=f.alert,
                       state_dir=world["state"], **kw)


def duty_file(world):
    return json.loads((world["state"] / duty.DUTY_FILE).read_text(encoding="utf-8"))


def test_ball_and_next_in_verdict_and_state(world):
    set_passport(world, "мяч у меня: макет")
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    recs = go(world, F(), 5)
    assert recs[0]["ball"] == "mine" and recs[0]["next"] == "проверить тесты и отчитаться"
    snap = json.loads((world["state"] / ws.STATE_FILE).read_text(encoding="utf-8"))
    assert snap["sessions"]["proj-7"]["ball"] == "mine"


def test_idle_ball_mine_nudge_throttled_30min(world):
    f = F()
    set_passport(world, "мяч у меня: макет")
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    go(world, f, 10)
    assert f.said == []
    go(world, f, 21)
    assert len(f.said) == 1
    assert f.said[0][1] == "дежурный: стоишь 21 мин, мяч у тебя: проверить тесты и отчитаться"
    go(world, f, 40)
    assert len(f.said) == 1                     # < 30 мин
    go(world, f, 52)
    assert len(f.said) == 2                     # 31 мин после первого
    d = duty_file(world)["sessions"]["proj-7"]
    assert d["nudged_at"] and d["last_action"] and d["ball"] == "mine"
    assert d["ao_id"] == "proj-7" and d["role"] == "designer" and d["idle_min"] == 52
    assert d["next"] == "проверить тесты и отчитаться" and d["state"] in ("idle", "ok")


def test_manager_session_not_nudged(world, monkeypatch):
    f = F()
    set_passport(world, "мяч у меня: макет")
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    monkeypatch.setattr(ws, "is_manager", lambda h: True)
    go(world, f, 30)
    assert f.said == [] and f.to == []


def test_ball_user_or_rest_no_nudge(world):
    f = F()
    for cur in ("мяч у пользователя: ждёт слова", "отдых"):
        set_passport(world, cur)
        write_rows(world["transcript"], ended_rows(T0), world["proj"])
        go(world, f, 40)
    assert f.said == [] and f.mailed == []


def test_ball_manager_mail_every_60min(world):
    f = F()
    set_passport(world, "мяч у менеджера: жду решения по SOC-9")
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    go(world, f, 10)
    assert f.mailed == []
    go(world, f, 21)
    assert len(f.mailed) == 1 and "мяч у менеджера" in f.mailed[0] and "proj-7" in f.mailed[0]
    assert f.said == []
    go(world, f, 60)
    assert len(f.mailed) == 1
    go(world, f, 82)
    assert len(f.mailed) == 2


def test_dialog_alert_once_to_human(world):
    f = F()
    set_passport(world, "делаю")
    write_rows(world["transcript"], [user_text(T0), tool_use(T0, name="AskUserQuestion",
               inp={"question": "?"})], world["proj"])
    go(world, f, 21)
    assert len(f.alerts) == 1
    text, source = f.alerts[0]
    assert text.startswith("❓") and "proj-7" in text and "proj — designer" in text
    assert source == "proj/designer/proj-7"
    go(world, f, 22)
    go(world, f, 30)
    assert len(f.alerts) == 1                   # дедуп до смены состояния
    assert f.said == []                         # в диалог не толкаем


def test_dialog_alert_retried_when_delivery_fails(world):
    f = F()
    f.alert_ok = False
    write_rows(world["transcript"], [user_text(T0), tool_use(T0, name="AskUserQuestion",
               inp={"question": "?"})], world["proj"])
    go(world, f, 21)
    go(world, f, 22)
    assert len(f.alerts) == 2
    f.alert_ok = True
    go(world, f, 23)
    go(world, f, 24)
    assert len(f.alerts) == 3


def test_dialog_by_ao_activity_alert_not_before_20min(world):
    """D-115: ❓ пользователю не раньше 20 минут тишины (7 и 19 — нет, 20 — есть)."""
    f = F()
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    for m in (1, 7, 19):
        go(world, f, m, [session(activity="waiting_input")])
        assert f.alerts == [], m
    go(world, f, 20, [session(activity="waiting_input")])
    assert len(f.alerts) == 1
    go(world, f, 25, [session(activity="waiting_input")])
    assert len(f.alerts) == 1


def test_escalation_to_supervisor_once_per_episode(world):
    f = F()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])
    go(world, f, 21)           # толчок сторожа
    go(world, f, 32)           # письмо менеджеру (stuck)
    assert len(f.mailed) == 1 and f.to == []
    go(world, f, 45)
    assert f.to == []          # <30 мин после пинка (толчок в 21)
    go(world, f, 52)
    assert len(f.to) == 1
    ref, letter = f.to[0]
    assert ref == "management/supervisor"
    for frag in ("proj-7", "мин без записей", "толчок сторожа", "письмо менеджеру", "Курсор"):
        assert frag in letter
    go(world, f, 120)
    go(world, f, 300)
    assert len(f.to) == 1
    assert duty_file(world)["sessions"]["proj-7"]["last_action"]


def test_escalation_after_unanswered_duty_nudge(world):
    f = F()
    set_passport(world, "мяч у меня: макет")
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    go(world, f, 21)
    go(world, f, 40)
    assert f.to == []
    go(world, f, 52)
    assert len(f.to) == 1
    go(world, f, 90)
    assert len(f.to) == 1


def test_per_tick_cap(world, tmp_path):
    f = F()
    set_passport(world, "мяч у меня: макет")
    rows, sess = [], []
    for i in range(8):
        sid = "aaaaaaaa-0000-0000-0000-00000000000{0}".format(i)
        write_rows(world["transcript"].with_name(sid + ".jsonl"), ended_rows(T0), world["proj"])
        e = world["proj"] / ".tide" / "arcs" / "06-@r{0}".format(i) / "arcs" / "01-r{0}-1".format(i)
        e.mkdir(parents=True)
        (e / "arc.md").write_text(
            "# r\n\ntitle: proj — r{0}\nstatus: active\nclaude-session: {1}\n\n"
            "## cursor — resume here\nмяч у меня: х\n\n## next\n- шаг\n".format(i, sid),
            encoding="utf-8")
        sess.append(session(sid="proj-{0}".format(10 + i), sessid=sid))
    go(world, f, 30, sess)
    assert len(f.said) == duty.MAX_ACTIONS
    go(world, f, 31, sess)
    assert len(f.said) == 8                     # остаток — на следующем тике


def test_dry_run_sends_nothing_and_writes_no_duty(world):
    f = F()
    set_passport(world, "мяч у меня: макет")
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    recs = go(world, f, 30, dry_run=True)
    assert "duty_nudge" in recs[0]["actions"]
    assert f.said == [] and not (world["state"] / duty.DUTY_FILE).exists()


def test_duty_failure_does_not_break_tick(world, monkeypatch):
    f = F()
    set_passport(world, "мяч у меня: макет")
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])

    def boom(*a, **k):
        raise RuntimeError("duty broke")

    monkeypatch.setattr(duty, "decide", boom)
    monkeypatch.setattr(duty, "mail_status", boom)
    go(world, f, 21)
    assert len(f.said) == 1                     # обычный толчок сторожа цел
    snap = json.loads((world["state"] / ws.STATE_FILE).read_text(encoding="utf-8"))
    assert snap["sessions"]["proj-7"]["state"] == "nudged"


def test_mail_status_reports_without_alerting(tmp_path):
    (tmp_path / "mail-ping-queue.json").write_text(json.dumps(
        {"pending": {"management/main": [{"n": i} for i in range(301)]}, "last": {}}))
    (tmp_path / "mail-queue-alert.json").write_text(json.dumps({"x": "2026-10-12T12:00:00Z"}))
    st = duty.mail_status(tmp_path, T0)
    assert st["queue_rows"] == 301 and st["alarm"] == "queue_big"
    assert st["last_alert_at"] == "2026-10-12T12:00:00Z" and st["alert_by"].startswith("mail.py")


def test_mail_status_lock_held(tmp_path):
    import fcntl
    lock = tmp_path / "mail-ping-queue.json.lock"
    fh = open(lock, "a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    (tmp_path / "mail-ping-queue.json.holder").write_text(json.dumps(
        {"pid": 1, "since": T0.timestamp() - 90, "cmd": "mail send"}))
    try:
        st = duty.mail_status(tmp_path, T0)
    finally:
        fh.close()
    assert st["lock_held"] and st["alarm"] == "busy" and st["lock_held_sec"] == 90.0


def test_next_step_strips_markers():
    assert duty.next_step(["- [ ] первый", "- второй"]) == "первый"
    assert duty.next_step(["1. один"]) == "один"
    assert duty.next_step([]) == ""


def test_cursor_zhdu_is_not_nudged(world):
    f = F()
    set_passport(world, "мяч у меня: SOC-1; жду «отправляй» от пользователя")
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    go(world, f, 40)
    assert f.said == []
    assert duty_file(world)["sessions"]["proj-7"]["ball"] == "mine"
