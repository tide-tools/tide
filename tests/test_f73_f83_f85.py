"""SOC-143 F83 / F73 / F85 (+F71): сборка сида — «в полёте» по транскрипту на момент
сборки, статус SOC-N из Linear во всех блоках, «ждёт Гришу» — только свои и не Done."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tide.launcher import seed_draft

T0 = datetime(2026, 10, 9, 22, 20, 0, tzinfo=timezone.utc)


def _row(kind, at, content):
    return json.dumps({"type": kind, "timestamp": at.isoformat().replace("+00:00", "Z"),
                       "message": {"role": kind, "content": content}}, ensure_ascii=False)


def _notif(agent, tid, desc, interim=False):
    note = ("<note>This agent stopped with background work of its own still running. "
            "It may resume on its own when that work completes or reports, and the same "
            "task-id notifies again if it does; the result below may be interim.</note>\n"
            if interim else "")
    return ("<task-notification>\n<task-id>{0}</task-id>\n<tool-use-id>{1}</tool-use-id>\n"
            "<status>completed</status>\n<summary>Agent \"{2}\" finished</summary>\n{3}"
            "<result>готово</result>\n</task-notification>").format(agent, tid, desc, note)


def _launch(tid, agent, desc, at):
    return [_row("assistant", at, [{"type": "tool_use", "id": tid, "name": "Agent",
                                    "input": {"description": desc,
                                              "run_in_background": True}}]),
            _row("user", at, [{"type": "tool_result", "tool_use_id": tid,
                               "content": "Async agent launched. agentId: " + agent}])]


def _transcript(tmp_path: Path, rows) -> Path:
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


# --- F83 ------------------------------------------------------------------------------

def test_f83_final_notification_means_finished(tmp_path):
    at = T0.replace(minute=22, second=17)
    tr = _transcript(tmp_path, _launch("tu1", "a1", "Завести тему студии и факт 3594", T0)
                     + [_row("user", at, _notif("a1", "tu1", "Завести тему студии и факт 3594"))])
    live, done = seed_draft.worker_runs(tr)
    assert live == []
    assert [(a, d) for a, d, _ in done] == [("a1", "Завести тему студии и факт 3594")]
    assert seed_draft.background_workers(tr, git=lambda c, a: ("", "")) == []


def test_f83_interim_notification_means_still_running(tmp_path):
    tr = _transcript(tmp_path, _launch("tu1", "a1", "полный pytest", T0)
                     + [_row("user", T0.replace(minute=30),
                             _notif("a1", "tu1", "полный pytest", interim=True))])
    live, done = seed_draft.worker_runs(tr)
    assert [r[0] for r in live] == ["a1"] and done == []
    lines = seed_draft.worker_lines(live, git=lambda c, a: ("", ""))
    assert len(lines) == 1 and "полный pytest" in lines[0] and "жив" in lines[0]


def test_f83_label_inflight_settled_by_transcript(tmp_path):
    # пара 16: метка 19:21:56, итог 19:22:17 — сид держал сабагента «в работе»
    at = T0.replace(minute=22, second=17)
    tr = _transcript(tmp_path, _launch("tu1", "a1", "Завести тему студии и факт 3594", T0)
                     + [_row("user", at, _notif("a1", "tu1", "Завести тему студии и факт 3594"))])
    live, done = seed_draft.worker_runs(tr)
    label = ("- сабагент — тема app-development 01 + факт 3594 (по слову management-20) "
             "(пульс 19:21)")
    other = "- запрос менеджеру про порог — ответа нет (пульс 19:21)"
    kept, gone = seed_draft.settle_inflight([label, other], live, done)
    assert kept == [other]
    assert len(gone) == 1 and "сдал итог" in gone[0] and "Завести тему студии" in gone[0]
    where = ["- 09.10 19:22 — сделано: SOC-130 · идёт: тема app-development + факт 3594 "
             "(сабагент 04, принять по диску) · ждёт Гришу: ❓ 3423"]
    marked = seed_draft.mark_finished_workers(where, live, done)
    assert "сдал итог" in marked[0] and "не в полёте" in marked[0]


def test_f83_live_worker_keeps_label(tmp_path):
    tr = _transcript(tmp_path,
                     _launch("tu1", "a1", "Завести тему студии и факт 3594", T0)
                     + _launch("tu2", "a2", "полный pytest", T0)
                     + [_row("user", T0.replace(minute=25),
                             _notif("a1", "tu1", "Завести тему студии и факт 3594"))])
    live, done = seed_draft.worker_runs(tr)
    assert [r[0] for r in live] == ["a2"]
    label = "- сабагент гоняет полный pytest (пульс 19:21)"
    kept, gone = seed_draft.settle_inflight([label], live, done)
    assert kept == [label] and gone == []


def test_f83_generic_label_without_live_workers(tmp_path):
    tr = _transcript(tmp_path, _launch("tu1", "a1", "что-то другое", T0)
                     + [_row("user", T0.replace(minute=40), _notif("a1", "tu1", "что-то другое"))])
    live, done = seed_draft.worker_runs(tr)
    kept, gone = seed_draft.settle_inflight(["- сабагент доводит (пульс 00:01)"], live, done)
    assert kept == [] and len(gone) == 1


# --- F73 ------------------------------------------------------------------------------

def _seal(bodies, live, monkeypatch):
    monkeypatch.setattr(seed_draft, "linear_fetch", lambda keys: live)
    seed_draft._LINEAR_CACHE.clear()
    try:
        return seed_draft.seal(bodies, context=[])
    finally:
        seed_draft._LINEAR_CACHE.clear()


def test_f73_done_marked_in_every_block(monkeypatch):
    text = _seal({seed_draft.WHERE: ["- SOC-107 (In Review), шаг 2 сдан"],
                  seed_draft.EXPECTATION: ["- Гриша ждёт SOC-107"],
                  seed_draft.NEXT: ["- сверка по SOC-107"],
                  seed_draft.MAIN: ["- SOC-107 — шов"]},
                 {"SOC-107": ("Done", "completed")}, monkeypatch)
    for head in (seed_draft.WHERE, seed_draft.EXPECTATION, seed_draft.NEXT, seed_draft.MAIN):
        body = text.split("## " + head, 1)[1].split("\n## ", 1)[0]
        assert "(SOC-107 — Done в Linear)" in body, head
    assert text.count("SOC-107 — Done в Linear") == 4


def test_f73_mark_not_doubled():
    live = {"SOC-1": ("Done", "completed")}
    once = seed_draft.mark_closed(["- SOC-1 шаг"], live)
    assert seed_draft.mark_closed(once, live) == once


# --- F85 / F71 ------------------------------------------------------------------------

class _Live(dict):
    takers = {}


def test_f85_taker_parse():
    assert seed_draft.parse_taker("Взял: tide-stack — board · 03 (AO tide-stack-9)") == (
        "tide-stack", "board")
    assert seed_draft.parse_taker("Взял и сделал доступ: https://x") is None


def test_f85_foreign_and_done_not_awaiting(monkeypatch):
    live = _Live({"SOC-102": ("In Review", "started"), "SOC-105": ("In Review", "started"),
                  "SOC-130": ("Done", "completed"), "SOC-98": ("Todo", "unstarted")})
    live.takers = {"SOC-102": [("tide-stack", "board")],
                   "SOC-105": [("tide-stack", "topics"), ("tide-stack", "context-keeper")]}
    bodies = {
        seed_draft.THREAD_SELF: ["tide-stack", "context-keeper"],
        seed_draft.MAIN: ["- SOC-105 — мерка (б); SOC-102 доска",
                          "- не дублировать: continuity-audit (SOC-98)"],
        seed_draft.AWAITING: ["- Done SOC-130 — менеджер/main (из следующего шага)",
                              "- ❓ 3423 форма опросов"],
    }
    text = _seal(bodies, live, monkeypatch)
    aw = text.split("## " + seed_draft.AWAITING, 1)[1].split("\n## ", 1)[0]
    assert "SOC-105 на проверке" in aw          # своя (Взял: … context-keeper)
    assert "SOC-102" not in aw                  # чужая (board)
    # уже Done — не пункт; SOC-181: только номером в сводной строке «снято из сида»
    assert [ln for ln in aw.splitlines() if "SOC-130" in ln] == [
        "- снято из сида (отвечено или закрыто в Linear): 1 — SOC-130"]
    assert "❓ 3423" in aw
    lin = text.split("## " + seed_draft.LINEAR, 1)[1].split("\n## ", 1)[0]
    others = lin.split("ведут другие нити", 1)[1]
    assert "SOC-102" in others and "ведёт tide-stack — board" in others
    assert "SOC-98" in others and "не дублировать" in others   # F71
    assert "SOC-105" not in others


def test_f85_no_thread_known_keeps_review():
    live = _Live({"SOC-9": ("In Review", "started")})
    live.takers = {"SOC-9": [("x", "board")]}
    assert seed_draft.foreign_issues(["SOC-9"], live, ()) == {}
    assert seed_draft.review_awaiting(live, {}) != []
    assert seed_draft.review_awaiting(live, {"SOC-9": "ведёт x — board"}) == []
