"""SOC-101 пакет Ж — находки приёмки acceptance-05 §9 (пара 12, board 03→04).

Каждая находка — свой раздел. Сеть и git не трогаются (git — подменой).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tide.launcher import seed_draft


@pytest.fixture
def msk(monkeypatch):
    """Пульсы — местное время −03, транскрипт — UTC."""
    import time

    monkeypatch.setenv("TZ", "Etc/GMT+3")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _write(path: Path, rows) -> Path:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                    encoding="utf-8")
    return path


def _tool(ts, tid, name, inp, cwd="/repo"):
    return {"type": "assistant", "timestamp": ts, "cwd": cwd, "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _result(ts, tid, text):
    return {"type": "user", "timestamp": ts, "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": text}]}]}}


def _notif(ts, tid, status="completed"):
    return {"type": "user", "timestamp": ts, "message": {"content": (
        "<task-notification>\n<task-id>x</task-id>\n<tool-use-id>{0}</tool-use-id>\n"
        "<status>{1}</status>\n<summary>Agent finished</summary>\n</task-notification>"
    ).format(tid, status)}}


# --- F45: фоновые сабагенты старой — в «в работе» ----------------------------------

def test_unfinished_background_worker_reaches_inflight(tmp_path, msk):
    t = _write(tmp_path / "t.jsonl", [
        # A: запущен и кончился — не в работе
        _tool("2026-10-09T16:00:00Z", "t1", "Agent", {"description": "мобильная v2",
                                                      "run_in_background": True}),
        _result("2026-10-09T16:00:01Z", "t1", "Async agent launched successfully.\n"
                "agentId: aaa111 (internal)"),
        _notif("2026-10-09T16:10:00Z", "t1"),
        # B: запущен, кончился, возобновлён SendMessage'ем — и не кончился
        _tool("2026-10-09T16:18:00Z", "t2", "Agent", {"description": "Починить вёрстку"}),
        _result("2026-10-09T16:18:01Z", "t2", "Async agent launched successfully.\n"
                "agentId: bbb222"),
        _notif("2026-10-09T16:30:00Z", "t2"),
        _tool("2026-10-09T17:18:21Z", "t3", "SendMessage", {"to": "bbb222", "message": "дальше"}),
        _result("2026-10-09T17:18:35Z", "t3", json.dumps(
            {"success": True, "message": "Resuming agent bbb222", "resumedAgentId": "bbb222"})),
        # сообщение живому воркеру (без возобновления) — не новый запуск
        _tool("2026-10-09T17:19:00Z", "t4", "SendMessage", {"to": "bbb222", "message": "ещё"}),
        _result("2026-10-09T17:19:01Z", "t4", '{"success":true,"message":"queued"}'),
    ])
    calls = []

    def fake_git(cwd, at):
        calls.append((cwd, at))
        return "/repo", "e30e80d"

    out = seed_draft.background_workers(t, git=fake_git)
    assert out == ["- воркер «Починить вёрстку» — жив в старой сессии (с 14:18), итог "
                   "перешлёт старая; адрес: SendMessage bbb222 из старой, task-id bbb222; "
                   "коммиты — git log /repo после e30e80d"]
    assert calls and calls[0][0] == "/repo"


def test_no_transcript_no_workers():
    assert seed_draft.background_workers(None) == []
