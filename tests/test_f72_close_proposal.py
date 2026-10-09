"""SOC-143 F72: «закрыть старую?» менеджеру — только когда у старой нет живых воркеров
и их итоги пересланы новой (та же проверка, что у автозакрытия). Иначе предложение
откладывается (``close-deferred:``) и уходит из хука handoff-confirm, когда условие
выполнится. AO фальшивый, транскрипт старой — во временной папке.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from tide import handoff_queue as hq
from tide.launcher import reception

from tests.test_handoff_reception_alert import _pulse, seam, sent  # noqa: F401  fixtures
from tests.test_handoff_autospawn import world  # noqa: F401  fixture
from tests.test_handoff_propose_close import ao, manager  # noqa: F401  fixtures


def _row(kind, at, content):
    return json.dumps({"type": kind, "timestamp": at.isoformat(),
                       "message": {"content": content}}, ensure_ascii=False)


def _launch(at):
    return [
        _row("assistant", at, [{"type": "tool_use", "id": "tu1", "name": "Agent",
                                "input": {"description": "прогон тестов",
                                          "run_in_background": True}}]),
        _row("user", at, [{"type": "tool_result", "tool_use_id": "tu1",
                           "content": "Async agent launched successfully. agentId: ag1"}]),
    ]


def _notif(at, *, interim=False):
    tail = (" Agent stopped with background work of its own still running; the result "
            "below may be interim." if interim else "")
    return _row("user", at, "<task-notification><task-id>ag1</task-id><tool-use-id>tu1"
                "</tool-use-id><status>completed</status><summary>тесты зелёные</summary>"
                "{0}</task-notification>".format(tail))


def _forward(at, to="proj-13"):
    return _row("assistant", at, [{"type": "tool_use", "id": "tu2", "name": "Bash",
                                   "input": {"command": "ao send --session {0} --message "
                                             "'итог: зелёно'".format(to)}}])


@pytest.fixture
def old_tr(seam, tmp_path, monkeypatch):
    from tide.arc import holder
    from tide.hooks import handed_off

    p = tmp_path / "old.jsonl"
    p.write_text("", encoding="utf-8")
    monkeypatch.setattr(reception, "_old_transcript",
                        lambda sid: p if sid == "old-sid" else None)
    monkeypatch.setattr(holder, "ao_handle_for", lambda home, sid: {
        "old-sid": "proj-12", "new-sid": "proj-13"}.get(sid, ""))
    monkeypatch.setattr(handed_off, "successor",
                        lambda home, rec: {"ao_session": "proj-13"})
    return p


def _write(p, rows):
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _marks(seam):
    text = hq.list_offers(seam["home"])[0]["path"].read_text(encoding="utf-8")
    return hq._get(text, reception.CLOSE_PROPOSED), hq._get(text, reception.CLOSE_DEFERRED)


def test_live_worker_defers_then_final_and_forward_proposes(seam, sent, ao, manager,
                                                            old_tr):
    t0 = datetime.now() - timedelta(minutes=30)
    _write(old_tr, _launch(t0))
    assert _pulse() == 0                                   # приём
    assert ao.said == []                                   # воркер жив — не предлагаем
    proposed, deferred = _marks(seam)
    assert proposed == "-" and "воркеры старой живы (1)" in deferred
    assert reception.retry_deferred(seam["home"], throttle=False) == [
        "{0}: deferred".format(seam["key"])]
    assert ao.said == []

    later = datetime.now() + timedelta(seconds=5)
    _write(old_tr, _launch(t0) + [_notif(later)])          # финал, но не переслан
    assert reception.retry_deferred(seam["home"], throttle=False)[0].endswith("deferred")
    assert ao.said == []
    assert "не пересланы новой" in _marks(seam)[1]

    _write(old_tr, _launch(t0) + [_notif(later), _forward(later + timedelta(seconds=5))])
    assert reception.retry_deferred(seam["home"], throttle=False) == [
        "{0}: sent".format(seam["key"])]
    assert len(ao.said) == 1 and ao.said[0][0] == "management-14"
    assert "закрыть proj — seam · 01 · proj-12?" in ao.said[0][1]
    assert _marks(seam)[0].endswith("· management-14 · sent")
    # больше не повторяется
    assert reception.retry_deferred(seam["home"], throttle=False) == []
    assert len(ao.said) == 1


def test_interim_result_still_defers(seam, sent, ao, manager, old_tr):
    t0 = datetime.now() - timedelta(minutes=30)
    later = datetime.now() + timedelta(seconds=5)
    _write(old_tr, _launch(t0) + [_notif(later, interim=True),
                                  _forward(later + timedelta(seconds=5))])
    assert _pulse() == 0
    assert ao.said == []
    assert "промежуточный" in _marks(seam)[1]


def test_no_workers_proposes_at_reception(seam, sent, ao, manager, old_tr):
    _write(old_tr, [_row("assistant", datetime.now(), [{"type": "text", "text": "передаю"}])])
    assert _pulse() == 0
    assert len(ao.said) == 1
    assert _marks(seam)[1] == "-"


def test_retry_is_throttled_and_skips_undeferred(seam, sent, ao, manager, old_tr):
    _write(old_tr, [])
    assert reception.retry_deferred(seam["home"]) == []    # отложенных нет — тишина
    t0 = datetime.now() - timedelta(minutes=30)
    _write(old_tr, _launch(t0))
    assert _pulse() == 0 and ao.said == []
    assert reception.retry_deferred(seam["home"]) != []    # первый — идёт
    assert reception.retry_deferred(seam["home"]) == []    # второй — в окне тишины
