"""SOC-101 F67 (старт-гейт приёма узнаёт «ждёт его решения») + автозакрытие старой.

ao не вызывается: AO-сессия, транскрипт, отправка и kill подменены.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tide import handoff_queue as hq, paths
from tide.hooks import autoclose as ac


# --- F67 ----------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    "Ждёт его решения: truth-gate, доска с телефона",
    "ждёт Гришиного решения по SOC-105",
    "SOC-105 ждёт решения Гриши",
    "ждёт ответа от Гриши",
])
def test_f67_retell_knows_awaiting_forms(said):
    assert "что ждёт Гришу" not in hq.retell_gaps(said)


def test_f67_old_forms_and_negatives_kept():
    assert hq.retell_gaps("Гриша ждёт промер, нельзя пушить") == ["что ждёт Гришу"]
    assert hq.retell_gaps("ждёт Гришу PR #5, нельзя пушить") == ["ожидание Гриши"]


SEED_WITH = """## ожидание Гриши
- —
## ждёт Гришу

- нет (решения у менеджера) (пульс 18:00)
- SOC-101 на проверке (In Review)
## следующий шаг
- дальше
"""

SEED_EMPTY = """## ожидание Гриши
- — в пульсах не названо
## ждёт Гришу
- нет
## следующий шаг
"""


def test_f67_hint_says_seed_has_it(tmp_path):
    seed = tmp_path / "seed.md"
    seed.write_text(SEED_WITH, encoding="utf-8")
    hint = hq.retell_hint(["ожидание Гриши", "что ждёт Гришу"], "t/s", str(seed))
    assert "В сиде это есть (что ждёт Гришу)" in hint
    assert "В сиде нет: ожидание Гриши" in hint


def test_f67_hint_seed_empty_block(tmp_path):
    seed = tmp_path / "seed.md"
    seed.write_text(SEED_EMPTY, encoding="utf-8")
    hint = hq.retell_hint(["что ждёт Гришу"], "t/s", str(seed))
    assert "В сиде нет: что ждёт Гришу" in hint
    assert "есть" not in hint


def test_f67_hint_without_seed_is_generic():
    hint = hq.retell_hint(["что ждёт Гришу"], "t/s")
    assert "Возьми из сида" in hint and "В сиде нет:" not in hint


# --- автозакрытие ---------------------------------------------------------------

NOW = datetime(2026, 10, 9, 20, 0, 0)


def _row(kind, at, content):
    return json.dumps({"type": kind, "timestamp": at.isoformat(),
                       "message": {"content": content}}, ensure_ascii=False)


def _transcript(tmp_path, *, live_worker=False, notified=True, forwarded=True,
                last=NOW - timedelta(minutes=10)) -> Path:
    t0 = NOW - timedelta(hours=1)
    rows = [
        _row("assistant", t0, [{"type": "tool_use", "id": "tu1", "name": "Agent",
                                "input": {"description": "прогон тестов"}}]),
        _row("user", t0, [{"type": "tool_result", "tool_use_id": "tu1",
                           "content": "Async agent launched. agentId: ag1"}]),
    ]
    if not live_worker and notified:
        rows.append(_row("user", last - timedelta(minutes=5),
                         "<task-notification><task-id>ag1</task-id><tool-use-id>tu1"
                         "</tool-use-id><status>completed</status><summary>тесты зелёные"
                         "</summary></task-notification>"))
        if forwarded:
            rows.append(_row("assistant", last, [{
                "type": "tool_use", "id": "tu2", "name": "Bash",
                "input": {"command": "ao send --session new-9 --message 'итог: зелёно'"}}]))
    elif not live_worker:
        rows.append(_row("assistant", last, [{"type": "text", "text": "молчу"}]))
    p = tmp_path / "old.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


REC = {"name": "300-30-handoffs", "from_session": "old-sid", "taken_by": "new-sid",
       "taken_at": (NOW - timedelta(hours=2)).isoformat(timespec="seconds"),
       "raised_at": "-", "mode": "continue", "status": "taken", "arc": "30-@handoffs/05"}


@pytest.fixture
def pair(tmp_control_home, monkeypatch, tmp_path):
    from tide.arc import holder
    from tide.hooks import handed_off
    from tide.launcher import reception

    monkeypatch.setattr(hq, "is_dissolved",
                        lambda home, sid: REC if sid == "old-sid" else None)
    monkeypatch.setattr(hq, "list_offers", lambda home, status=None: [dict(REC)])
    monkeypatch.setattr(holder, "ao_handle_for", lambda home, sid: {
        "old-sid": "old-7", "new-sid": "new-9"}.get(sid, ""))
    monkeypatch.setattr(handed_off, "successor",
                        lambda home, rec: {"ao_session": "new-9", "title": "новая"})
    monkeypatch.setattr(reception, "titles", lambda home, rec: ("handoffs · 05", "handoffs · 04"))
    return tmp_control_home


def _v(home, tr, alive=True, **kw):
    return ac.verdict(home, REC, now=NOW,
                      session_of=lambda h: {"isTerminated": not alive} if h else None,
                      transcript_of=lambda sid: tr, **kw)


def test_autoclose_ready(pair, tmp_path):
    v = _v(pair, _transcript(tmp_path))
    assert v["close"] is True, v["why"]
    assert v["old_ao"] == "old-7" and v["new_ao"] == "new-9"


def test_autoclose_waits_live_worker(pair, tmp_path):
    v = _v(pair, _transcript(tmp_path, live_worker=True))
    assert v["close"] is False and "воркеры старой живы" in v["why"]
    assert "прогон тестов" in v["why"]


def test_autoclose_waits_unforwarded_result(pair, tmp_path):
    v = _v(pair, _transcript(tmp_path, forwarded=False))
    assert v["close"] is False and "не пересланы" in v["why"]
    assert "тесты зелёные" in v["why"]


def test_autoclose_waits_quiet(pair, tmp_path):
    v = _v(pair, _transcript(tmp_path, last=NOW - timedelta(minutes=1)))
    assert v["close"] is False and "тишины" in v["why"]


def test_autoclose_skips_terminated_and_same_ao(pair, tmp_path, monkeypatch):
    tr = _transcript(tmp_path)
    assert "уже вышла (AO: terminated)" in _v(pair, tr, alive=False)["why"]
    from tide.arc import holder
    monkeypatch.setattr(holder, "ao_handle_for", lambda home, sid: "same-1")
    from tide.hooks import handed_off
    monkeypatch.setattr(handed_off, "successor",
                        lambda home, rec: {"ao_session": "same-1", "title": "новая"})
    assert "одной AO-сессии" in _v(pair, tr)["why"]


def test_autoclose_skips_reclaimed_old(pair, tmp_path, monkeypatch):
    monkeypatch.setattr(hq, "is_dissolved", lambda home, sid: None)
    v = _v(pair, _transcript(tmp_path))
    assert v["close"] is False and "другую нить" in v["why"]


def test_sweep_dry_run_does_nothing(pair, tmp_path):
    tr = _transcript(tmp_path)
    calls = []
    res = ac.sweep(pair, dry_run=True, now=NOW,
                   session_of=lambda h: {"isTerminated": False},
                   transcript_of=lambda sid: tr,
                   say=lambda *a: calls.append(a), kill=lambda h: calls.append(h),
                   alert=lambda *a: calls.append(a))
    assert res[0]["close"] and not calls
    assert not (paths.state_dir(pair) / ac.LOG).exists()
    assert "закрыл бы" in ac.render(res[0], dry_run=True)


def test_sweep_closes_in_order_once(pair, tmp_path):
    tr = _transcript(tmp_path)
    calls = []
    kw = dict(now=NOW, session_of=lambda h: {"isTerminated": False},
              transcript_of=lambda sid: tr,
              alert=lambda text, src: calls.append(("manager", text)),
              say=lambda h, text: calls.append(("say", h, text)) or True,
              kill=lambda h: calls.append(("kill", h)) or True)
    res = ac.sweep(pair, **kw)
    assert res[0]["closed"] is True
    assert [c[0] for c in calls] == ["kill", "manager", "say"]  # SOC-143: kill первым
    assert calls[0] == ("kill", "old-7")
    assert "old-7" in calls[1][1]
    assert calls[2][1] == "new-9" and "закрыта в 20:00" in calls[2][2]
    log = (paths.state_dir(pair) / ac.LOG).read_text(encoding="utf-8")
    assert "300-30-handoffs · закрываю · old-7" in log and "kill old-7 · ok" in log
    calls.clear()
    again = ac.sweep(pair, **kw)
    assert again[0]["close"] is False and not calls


def test_sweep_never_kills_calling_session(pair, tmp_path):
    tr = _transcript(tmp_path)
    killed = []
    res = ac.sweep(pair, skip=["old-sid"], now=NOW,
                   session_of=lambda h: {"isTerminated": False},
                   transcript_of=lambda sid: tr, kill=killed.append,
                   alert=lambda *a: None, say=lambda *a: True)
    assert not killed and res[0]["close"] is False


def test_on_prompt_off_by_default(pair, monkeypatch):
    monkeypatch.setattr(ac, "sweep", lambda *a, **k: pytest.fail("sweep при выключенном"))
    assert ac.on_prompt({"session_id": "x"}, home=pair, env={}) == []


def test_on_prompt_on_sweeps_skipping_self(pair, monkeypatch):
    got = {}
    monkeypatch.setattr(ac, "sweep", lambda home, skip=(): got.update(skip=list(skip)) or [])
    ac.on_prompt({"session_id": "me"}, home=pair, env={ac.ENV: "1"})
    assert "me" in got["skip"]
    got.clear()
    ac.on_prompt({"session_id": "me"}, home=pair, env={ac.ENV: "1"})  # троттлинг
    assert not got


def test_cli_autoclose_registered():
    from tide import cli
    p = cli.build_parser() if hasattr(cli, "build_parser") else None
    if p is None:
        pytest.skip("нет build_parser")
    args = p.parse_args(["handoffs", "autoclose", "--dry-run"])
    assert args.dry_run and args.func is ac.cmd_autoclose


# --- SOC-143: одна пара по --offer и новая = принявшая оффер ------------------------

def test_offer_matches_number_and_name():
    assert ac.offer_matches(REC, "300") and ac.offer_matches(REC, "300-30-handoffs")
    assert not ac.offer_matches(REC, "30") and not ac.offer_matches(REC, "299")
    assert not ac.offer_matches(REC, "")


def test_sweep_offer_only_that_pair(pair, tmp_path, monkeypatch):
    other = dict(REC, name="291-01-main", from_session="other-sid", taken_by="o-new")
    monkeypatch.setattr(hq, "list_offers", lambda home, status=None: [dict(REC), other])
    tr = _transcript(tmp_path)
    kw = dict(dry_run=True, now=NOW, session_of=lambda h: {"isTerminated": False},
              transcript_of=lambda sid: tr)
    assert [v["offer"] for v in ac.sweep(pair, offer="300", **kw)] == ["300-30-handoffs"]
    assert ac.sweep(pair, offer="999", **kw) == []


def test_cli_live_without_env_and_offer_closes_nothing(pair, monkeypatch, capsys):
    import argparse
    monkeypatch.delenv(ac.ENV, raising=False)
    got = {}
    monkeypatch.setattr(ac, "sweep", lambda home, dry_run=False, offer=None: got.update(
        dry=dry_run, offer=offer) or [])
    ac.cmd_autoclose(argparse.Namespace(dry_run=False, offer=None))
    assert got == {"dry": True, "offer": None} and "выключено" in capsys.readouterr().out
    ac.cmd_autoclose(argparse.Namespace(dry_run=False, offer="299"))
    assert got == {"dry": False, "offer": "299"}
    ac.cmd_autoclose(argparse.Namespace(dry_run=True, offer="299"))
    assert got == {"dry": True, "offer": "299"}


def test_new_is_offer_taker_not_thread_head(pair, tmp_path, monkeypatch):
    """Старая пара 292: голова нити уже дальше (06), новая пары — принявшая (05)."""
    from tide.hooks import handed_off
    monkeypatch.setattr(handed_off, "successor",
                        lambda home, rec: {"ao_session": "head-11", "title": "06"})
    v = _v(pair, _transcript(tmp_path))
    assert v["new_ao"] == "new-9" and "new-9" in v["new"] and "head-11" not in v["new"]
    assert v["close"] is True, v["why"]


def test_dead_taker_blocks_close_even_if_head_alive(pair, tmp_path, monkeypatch):
    from tide.hooks import handed_off
    monkeypatch.setattr(handed_off, "successor",
                        lambda home, rec: {"ao_session": "head-11", "title": "06"})
    v = ac.verdict(pair, REC, now=NOW, transcript_of=lambda sid: _transcript(tmp_path),
                   session_of=lambda h: {"isTerminated": h == "new-9"})
    assert v["close"] is False and "новая new-9 не жива" in v["why"]


def test_unknown_taker_blocks_close(pair, tmp_path, monkeypatch):
    from tide.arc import holder
    monkeypatch.setattr(holder, "ao_handle_for", lambda home, sid: {
        "old-sid": "old-7"}.get(sid, ""))
    v = _v(pair, _transcript(tmp_path))
    assert v["close"] is False and "принявшей оффер неизвестна" in v["why"]


def test_forward_to_thread_head_counts(tmp_path):
    t0 = NOW - timedelta(minutes=30)
    rows = [_row("user", t0, "<task-notification><summary>итог</summary></task-notification>"),
            _row("assistant", t0 + timedelta(minutes=1), [{
                "type": "tool_use", "id": "x", "name": "Bash",
                "input": {"command": "ao send --session head-11 итог"}}])]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    since = t0 - timedelta(minutes=1)
    assert ac.unforwarded_results(p, since, ["new-9", "head-11"]) == []
    assert ac.unforwarded_results(p, since, "new-9") != []


@pytest.mark.parametrize("new_state,why", [
    (None, "новая new-9 не жива (AO: не знает)"),               # AO-сессии нет / exited
    ({"isTerminated": True}, "новая new-9 не жива (AO: закрыта)"),
])
def test_dead_new_never_closes_old(pair, tmp_path, new_state, why):
    kills = []
    res = ac.sweep(pair, now=NOW, transcript_of=lambda sid: _transcript(tmp_path),
                   session_of=lambda h: new_state if h == "new-9" else {"isTerminated": False},
                   kill=kills.append, alert=lambda *a: None, say=lambda *a: True)
    assert not kills and res[0]["close"] is False and why in res[0]["why"]


def test_not_taken_new_never_closes_old(pair, tmp_path):
    rec = dict(REC, taken_at="-")
    v = ac.verdict(pair, rec, now=NOW, transcript_of=lambda sid: _transcript(tmp_path),
                   session_of=lambda h: {"isTerminated": False})
    assert v["close"] is False and "нет taken-at" in v["why"]


# --- SOC-143: промежуточный итог сабагента = воркер жив ------------------------------

INTERIM = ("<task-notification>\n<task-id>a320</task-id>\n<tool-use-id>tu1</tool-use-id>\n"
           "<status>completed</status>\n<summary>Agent \"полный pytest\" finished</summary>\n"
           "<note>This agent stopped with background work of its own still running. It may "
           "resume on its own when that work completes or reports, and the same task-id "
           "notifies again if it does; the result below may be interim.</note>\n"
           "<result>Прогон идёт, жду уведомления.</result>\n</task-notification>")
FINAL = ("<task-notification>\n<task-id>a320</task-id>\n<tool-use-id>tu1</tool-use-id>\n"
         "<status>completed</status>\n<summary>Agent \"полный pytest\" finished</summary>\n"
         "<result>2000 passed</result>\n</task-notification>")


def _interim_transcript(tmp_path, *tail) -> Path:
    t0 = NOW - timedelta(hours=1)
    rows = [
        _row("assistant", t0, [{"type": "tool_use", "id": "tu1", "name": "Agent",
                                "input": {"description": "полный pytest"}}]),
        _row("user", t0, [{"type": "tool_result", "tool_use_id": "tu1",
                           "content": "Async agent launched. agentId: a320"}]),
        _row("user", NOW - timedelta(minutes=40), INTERIM),
        # старая переслала промежуточное новой — это не итог
        _row("assistant", NOW - timedelta(minutes=30), [{
            "type": "tool_use", "id": "f1", "name": "Bash",
            "input": {"command": "ao send --session new-9 'pytest идёт'"}}]),
    ] + list(tail)
    p = tmp_path / "interim.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_interim_notification_means_worker_alive(pair, tmp_path):
    tr = _interim_transcript(tmp_path)
    assert ac.interim_workers(tr) == ['a320 «Agent "полный pytest" finished»']
    kills = []
    res = ac.sweep(pair, now=NOW, transcript_of=lambda sid: tr,
                   session_of=lambda h: {"isTerminated": False},
                   kill=kills.append, alert=lambda *a: None, say=lambda *a: True)
    assert not kills and res[0]["close"] is False
    assert "не сдали итог" in res[0]["why"] and "a320" in res[0]["why"]


def test_final_after_interim_needs_forward(pair, tmp_path):
    tr = _interim_transcript(tmp_path, _row("user", NOW - timedelta(minutes=20), FINAL))
    assert ac.interim_workers(tr) == []
    v = _v(pair, tr)
    assert v["close"] is False and "не пересланы" in v["why"]
    tr = _interim_transcript(tmp_path, _row("user", NOW - timedelta(minutes=20), FINAL),
                             _row("assistant", NOW - timedelta(minutes=10), [{
                                 "type": "tool_use", "id": "f2", "name": "Bash",
                                 "input": {"command": "ao send --session new-9 '2000 passed'"}}]))
    assert _v(pair, tr)["close"] is True


def test_sendmessage_after_final_revives_worker(tmp_path):
    tr = _interim_transcript(
        tmp_path, _row("user", NOW - timedelta(minutes=20), FINAL),
        _row("assistant", NOW - timedelta(minutes=15), [{
            "type": "tool_use", "id": "s1", "name": "SendMessage",
            "input": {"to": "a320", "message": "ещё прогон"}}]))
    assert ac.interim_workers(tr) == ["a320 «возобновлён письмом после итога»"]


# --- SOC-143: конечный статус AO = старая уже вышла, kill не нужен ---------------------

@pytest.mark.parametrize("old_state,word", [
    ({"status": "exited", "isTerminated": False, "activity": "exited"}, "exited"),
    ({"status": "idle", "isTerminated": False, "activity": {"state": "exited"}}, "exited"),
    ({"status": "terminated", "isTerminated": True}, "terminated"),
    ({"status": "killed"}, "killed"),
    ({"status": "merged", "isTerminated": True}, "merged"),
])
def test_old_already_exited_no_kill(pair, tmp_path, old_state, word):
    kills = []
    res = ac.sweep(pair, now=NOW, transcript_of=lambda sid: _transcript(tmp_path),
                   session_of=lambda h: old_state if h == "old-7" else {"status": "idle"},
                   kill=kills.append, alert=lambda *a: None, say=lambda *a: True)
    assert not kills and res[0]["close"] is False
    assert "уже вышла (AO: {0})".format(word) in res[0]["why"]
    log = (paths.state_dir(pair) / ac.LOG).read_text(encoding="utf-8")
    assert "300-30-handoffs · уже вышла · old-7" in log and "закрываю" not in log


def test_dry_run_exited_writes_no_log(pair, tmp_path):
    res = ac.sweep(pair, dry_run=True, now=NOW, transcript_of=lambda sid: _transcript(tmp_path),
                   session_of=lambda h: {"status": "exited"} if h == "old-7" else {})
    assert "уже вышла" in res[0]["why"]
    assert not (paths.state_dir(pair) / ac.LOG).exists()


def test_failed_kill_says_why_and_rechecks(pair, tmp_path):
    seen = {"n": 0}

    def session_of(h):
        if h != "old-7":
            return {"status": "idle"}
        seen["n"] += 1  # до kill жива, после — уже вышла
        return {"status": "idle"} if seen["n"] == 1 else {"status": "exited"}

    res = ac.sweep(pair, now=NOW, transcript_of=lambda sid: _transcript(tmp_path),
                   session_of=session_of, alert=lambda *a: None, say=lambda *a: True,
                   kill=lambda h: (False, "ao session: session not running"))
    v = res[0]
    assert v["closed"] is False and v["gone"] == "exited"
    out = ac.render(v, dry_run=False)
    assert "kill: ao session: session not running" in out and "уже вышла (AO: exited)" in out
    log = (paths.state_dir(pair) / ac.LOG).read_text(encoding="utf-8")
    assert "НЕ ВЫШЛО · причина: ao session: session not running" in log
    assert "старая уже вышла (AO: exited)" in log
    # неудачный kill не запирает пару; следующий проход видит «уже вышла», без kill
    again = ac.sweep(pair, now=NOW, transcript_of=lambda sid: _transcript(tmp_path),
                     session_of=lambda h: {"status": "exited"} if h == "old-7" else {},
                     kill=lambda h: pytest.fail("kill по вышедшей"))
    assert "уже вышла" in again[0]["why"]


def test_kill_error_carries_ao_words(monkeypatch):
    from tide.adapters import ao

    def boom(args, timeout=60):
        raise ao.AoError("ao session kill: no such session (exit 2)")
    monkeypatch.setattr(ao, "_call", boom)
    assert ac._kill("x") == (False, "ao session kill: no such session (exit 2)")


# --- SOC-143: «закрыта» — только после удачного kill (оффер 299, 20:07) ---------------

def _close_run(pair, tmp_path, kill, old_after):
    seen = {"n": 0}

    def session_of(h):
        if h != "old-7":
            return {"status": "idle"}
        seen["n"] += 1  # первый вопрос — verdict (жива), дальше — после kill
        return {"status": "idle"} if seen["n"] == 1 else old_after

    calls = []
    res = ac.sweep(pair, now=NOW, transcript_of=lambda sid: _transcript(tmp_path),
                   session_of=session_of,
                   alert=lambda text, src: calls.append(("manager", text)),
                   say=lambda h, text: calls.append(("say", h, text)) or True,
                   kill=lambda h: calls.append(("kill", h)) or kill)
    log = (paths.state_dir(pair) / ac.LOG).read_text(encoding="utf-8")
    return res[0], calls, log


def test_close_ok_says_closed_after_kill(pair, tmp_path):
    v, calls, log = _close_run(pair, tmp_path, True, {"status": "idle"})
    assert v["closed"] is True
    assert [c[0] for c in calls] == ["kill", "manager", "say"]
    assert "Закрываю старую в 20:00: ao session kill old-7" in calls[1][1]
    assert "старая handoffs · 04" in calls[2][2] and "закрыта в 20:00" in calls[2][2]
    assert "Нить целиком твоя." in calls[2][2]
    assert "kill old-7 · ok" in log


def test_close_kill_failed_but_gone_says_exited_itself(pair, tmp_path):
    v, calls, log = _close_run(pair, tmp_path, (False, "session not running"),
                               {"status": "exited"})
    assert v["closed"] is False and v["gone"] == "exited"
    assert [c[0] for c in calls] == ["kill", "manager", "say"]
    said = calls[2][2]
    assert calls[2][1] == "new-9"
    assert "уже вышла сама (AO: exited), закрывать не пришлось" in said
    assert "воркеров нет, итоги пересланы. Нить целиком твоя." in said
    assert "закрыта" not in said
    assert "kill не вышел (session not running)" in calls[1][1]
    assert "Закрываю" not in calls[1][1]
    assert "НЕ ВЫШЛО · причина: session not running" in log
    assert "старая уже вышла (AO: exited)" in log


def test_close_kill_failed_old_alive_tells_new_nothing(pair, tmp_path):
    v, calls, log = _close_run(pair, tmp_path, (False, "timeout"), {"status": "idle"})
    assert v["closed"] is False and "gone" not in v
    assert [c[0] for c in calls] == ["kill", "manager"]  # новой — ни слова
    m = calls[1][1]
    assert "автозакрытие не вышло: timeout" in m and "жива" in m
    assert "закрой руками" in m and "Закрываю" not in m
    assert "НЕ ВЫШЛО · причина: timeout" in log and "старая всё ещё жива" in log
