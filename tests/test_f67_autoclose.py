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
    assert "уже закрыта" in _v(pair, tr, alive=False)["why"]
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
    assert [c[0] for c in calls] == ["manager", "say", "kill"]
    assert "old-7" in calls[0][1]
    assert calls[1][1] == "new-9" and "закрыта в 20:00" in calls[1][2]
    assert calls[2] == ("kill", "old-7")
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
