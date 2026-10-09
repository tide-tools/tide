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


# --- F46: ожидание Гриши — его слова из доставок, не фразы про решения менеджера ----

def _delivery(ts, who, body):
    return {"type": "user", "timestamp": ts, "message": {"content": (
        "<ao-session-delivery id=\"s:{0}\">\n[from {1}] {2}\n</ao-session-delivery>"
    ).format(ts, who, body)}}


def test_grisha_words_come_from_deliveries_with_tg_and_time(tmp_path, msk):
    t = _write(tmp_path / "t.jsonl", [
        _delivery("2026-10-09T16:35:00Z", "management-15", "Слово Гриши (tg 3286, поручение "
                  "через менеджера): «Вёрстка выплывает, где выплыло» — сделай"),
        _delivery("2026-10-09T17:07:00Z", "management-16", "Слово Гриши (tg 3353): «Я хочу "
                  "видеть полное разбиение, что куда ушло по токенам». Ещё: Слово Гриши "
                  "(tg, ~14:10): «хочу видеть, когда работа сделана»"),
        # не слова Гриши: текст со скрина, подпись без двоеточия
        _delivery("2026-10-09T16:22:00Z", "management-14", "Слово Гриши, не переспрашивай: "
                  "вместе с правкой (текст строк «СТОЯТ на месте давно»)"),
    ])
    assert seed_draft.grisha_words(t) == [
        "- «Я хочу видеть полное разбиение, что куда ушло по токенам» (слово Гриши, tg 3353, "
        "через management-16 в 14:07)",
        "- «хочу видеть, когда работа сделана» (слово Гриши, через management-16 в 14:07)",
        "- «Вёрстка выплывает, где выплыло» (слово Гриши, tg 3286, через management-15 в 13:35)",
    ]


def test_manager_decision_phrases_are_not_grisha_quotes():
    ctx = ["- 2026-10-09T14:20:00 — Решения: менеджер — «решение» только у ответа Гриши, "
           "🖐 = просьба руками",
           "- 2026-10-09T13:59:00 — Ждёт решения менеджера: помечать «решением» ответы "
           "менеджера или только ответы Гриши",
           "- 2026-10-09T13:46:00 — слово Гриши: «ответы = решения, пара вопрос→ответ»"]
    assert seed_draft.grisha_quotes(ctx) == [
        "- слово Гриши: «ответы = решения, пара вопрос→ответ» (цитата из пульса "
        "2026-10-09 13:46)"]


# --- F48: «ждёт Гришу» — задачи In Review и шаги «дать Грише» ------------------------

def test_in_review_and_next_for_grisha_reach_awaiting(monkeypatch):
    live = {"SOC-112": ("In Review", "started"), "SOC-107": ("Todo", "unstarted")}
    monkeypatch.setattr(seed_draft, "linear_states", lambda keys: live)
    bodies = {
        seed_draft.AWAITING: seed_draft.next_for_grisha(seed_draft.block_next(
            ["проверить снимки; ссылку tg:// дать Грише на проверку · таймлайн SOC-107"])),
        seed_draft.WHERE: ["- SOC-112 мобильная v2, SOC-107 таймлайн"],
    }
    text = seed_draft.seal(bodies, context=[])
    block = text.split("## ждёт Гришу", 1)[1].split("\n## ", 1)[0]
    assert "- проверить снимки; ссылку tg:// дать Грише на проверку (из следующего шага)" \
        in block
    assert ("- SOC-112 на проверке (In Review) — предлагаю: посмотреть и сказать слово "
            "через менеджера; Done ставит проверяющий") in block
    assert "SOC-107" not in block


# --- F50: карта сборщика — без пустого «главного», с кодом и тестами -------------------

def test_map_skips_empty_main_and_adds_code_and_tests(tmp_path):
    thread = tmp_path / "29-@board"
    sess = thread / "arcs" / "03-board-3"
    sess.mkdir(parents=True)
    (sess / "arc.md").write_text("# s\n", encoding="utf-8")
    (thread / "plan.md").write_text("# plan\n\n## главное\n\n", encoding="utf-8")
    t = _write(tmp_path / "t.jsonl", [
        _tool("2026-10-09T16:00:00Z", "a", "Edit", {"file_path": "/r/board/home.html"}),
        _tool("2026-10-09T16:05:00Z", "b", "Bash", {
            "command": "cd /r && python3 -m pytest -q board/ 2>&1 | tail -3"}),
        _tool("2026-10-09T16:06:00Z", "c", "Bash", {
            "command": "cd /r && git add board/live.py .tide/arcs/x/arc.md && git commit -m x"}),
    ])
    out = seed_draft.collected_map(tmp_path, sess, thread, transcripts=[t])
    text = "\n".join(out)
    assert "«главное»" not in text
    assert "- код (последние тронутые сессией и воркерами): /r/board/live.py, " \
           "/r/board/home.html" in text
    assert "/.tide/" not in text.split("код", 1)[1]
    assert "- тесты: cd /r && python3 -m pytest -q board/ (последний прогон в транскрипте)" \
        in text
    # команда из опыта сильнее транскрипта
    assert seed_draft.test_command(["- тесты: `env -u X .venv/bin/python -m pytest -q`"],
                                   [t]) == "env -u X .venv/bin/python -m pytest -q (из опыта)"
    # «главное» есть — пункт plan.md на месте
    (thread / "plan.md").write_text("# plan\n\n## главное\n\n- SOC-51 доска\n",
                                    encoding="utf-8")
    assert "«главное»" in "\n".join(seed_draft.collected_map(tmp_path, sess, thread,
                                                             transcripts=[]))


# --- F51: задачи Linear — и из цели нити, и из прошлого сида -------------------------

def test_linear_keys_from_thread_goal_and_previous_seed(monkeypatch):
    live = {"SOC-40": ("Todo", "unstarted"), "SOC-51": ("Done", "completed"),
            "SOC-112": ("In Review", "started"), "SOC-90": ("Canceled", "canceled")}
    monkeypatch.setattr(seed_draft, "linear_states", lambda keys: live)
    bodies = {seed_draft.WHERE: ["- SOC-112 мобильная"],
              seed_draft.LINEAR_SOURCES: [
                  ("цели нити", "Доска tide; ведёт SOC-51 и SOC-40"),
                  ("прошлого сида", "- SOC-90 — https://x/SOC-90\n- SOC-112 — https://x")]}
    text = seed_draft.seal(bodies, context=[])
    block = text.split("## задачи Linear", 1)[1].split("\n## ", 1)[0]
    assert "- SOC-40 — https://linear.app/socaseinpoint/issue/SOC-40 — Todo (из цели нити)" \
        in block
    assert "SOC-112 — https://linear.app/socaseinpoint/issue/SOC-112 — In Review\n" in block
    assert "  - SOC-51 — https://linear.app/socaseinpoint/issue/SOC-51 — закрыта (Done) " \
           "(из цели нити)" in block
    assert "  - SOC-90 — https://linear.app/socaseinpoint/issue/SOC-90 — закрыта (Canceled) " \
           "(из прошлого сида)" in block
    assert "_linear_sources" not in text and "цели нити\", " not in text


# --- F52: «имя · NN = id» не рвётся --------------------------------------------------

def test_name_with_equals_ao_id_is_kept_whole():
    got = seed_draft.block_next(["«готово — посмотри» (вид от designer · 06 = "
                                 "app-development-22) ~2.5 ч · таймлайн SOC-107 ~5 ч"])
    assert got == ["- «готово — посмотри» (вид от designer · 06 = app-development-22) ~2.5 ч",
                   "- таймлайн SOC-107 ~5 ч"]


# --- C4/F39: решения не режутся посреди, опыт не задваивается ------------------------

PASS_1420 = ("- 2026-10-09T14:20:04 — Сделано за сессию: SOC-112 мобильная. Решения: "
             "менеджер — «решение» только у ответа Гриши, ❓ отдельным сообщением; формат "
             "решений = decisions-tg.jsonl, поле note → «прочитано как да». Адреса: менеджер "
             "management-16, main · 04 tide-stack-24. Опыт: мобильную проверять в WebKit; "
             "итоги слать и менеджеру, и main; TIDE_DIALOG_LOG путает тесты. План Б для "
             "ссылки в TG = пишущая ручка")


def test_decision_label_ends_with_its_sentence():
    got = seed_draft.labeled_pulses([PASS_1420])
    assert got["decisions"] == [
        "- менеджер — «решение» только у ответа Гриши, ❓ отдельным сообщением "
        "(статус: не указан) (пульс 14:20)",
        "- формат решений = decisions-tg.jsonl, поле note → «прочитано как да» "
        "(статус: не указан) (пульс 14:20)"]
    assert got["experience"][-1] == "- TIDE_DIALOG_LOG путает тесты (пульс 14:20)"


def test_same_lesson_in_other_words_is_folded():
    out = seed_draft.block_experience([], [
        "- мобильную проверять в WebKit (пульс 14:20)",
        "- итоги слать и менеджеру, и main (пульс 14:20)",
        "- TIDE_DIALOG_LOG путает тесты (пульс 14:20)",
        "- WebKit воспроизводит баги iPhone — мобильную проверять в WebKit (пульс 13:35)",
        "- TIDE_DIALOG_LOG в оболочке воркера путает журнал — тесты гонять и с env -u "
        "(пульс 13:42)",
        "- итоги и хвосты слать и менеджеру, и главной нити (сейчас main · 04) (пульс 08:36)",
    ])
    assert out[:-1] == [
        "- WebKit воспроизводит баги iPhone — мобильную проверять в WebKit (пульс 13:35)",
        "- итоги и хвосты слать и менеджеру, и главной нити (сейчас main · 04) (пульс 08:36)",
        "- TIDE_DIALOG_LOG в оболочке воркера путает журнал — тесты гонять и с env -u "
        "(пульс 13:42)"]
