"""Журнал слов человека — ``<нить>/dialog.md`` (решение 13 менеджмента).

UserPromptSubmit-хук ``handoff-confirm`` пишет туда только слова Гриши: вставки
харнесса, пульта и tide отсекаются; потеря реплики (нить не нашлась, запись упала) —
сигнал в stderr, ``.tide/state/dialog-errors.log`` и ``$TIDE_ALERT_CMD``.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from tide import handoff_queue as hq, paths, roster
from tide.arc import dialog, stream

SID = "22222222-0000-4000-8000-00000000bbbb"
NOW = datetime(2026, 10, 9, 2, 50, 0)


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
    thread = stream.new_thread(proj, "seam", goal="держать шов чистым")
    stream.new_session(proj, "seam", "s1", claude_session=SID)
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    for k in (dialog.ALERT_CMD_ENV, "AO_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(proj)
    return {"proj": proj, "thread": thread, "tmp": tmp_path}


def _alert_sink(world, monkeypatch) -> Path:
    sink = world["tmp"] / "alerts.jsonl"
    script = world["tmp"] / "alert_sink.py"
    script.write_text(
        "import json, os, sys\n"
        "rec = {{k: os.environ.get(k, '') for k in ('TIDE_ALERT_ROUTE', 'TIDE_ALERT_SOURCE')}}\n"
        "rec['text'] = sys.argv[-1]\n"
        "open({0!r}, 'a').write(json.dumps(rec, ensure_ascii=False) + '\\n')\n".format(str(sink)),
        encoding="utf-8")
    monkeypatch.setenv(dialog.ALERT_CMD_ENV, "{0} {1}".format(sys.executable, script))
    return sink


def _prompt(monkeypatch, prompt, sid=SID, **extra):
    payload = {"session_id": sid, "prompt": prompt}
    payload.update(extra)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))
    assert hq.cmd_handoff_confirm(None) == 0


def _lines(world):
    p = world["thread"] / "dialog.md"
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")] \
        if p.is_file() else []


# --- фильтр: только слова человека ----------------------------------------------

@pytest.mark.parametrize("prompt", [
    "<task-notification>\n<task-id>a5f3</task-id>\n<status>completed</status>\n</task-notification>",
    '<cross-session-message from="uds:/tmp/cc-socks/1.sock" from-name="x"> От менеджера </cross-session-message>',
    "<teammate-message teammate_id=\"lead\">сделай</teammate-message>",
    "<system-reminder>что-то</system-reminder>",
    "<command-name>/model</command-name>\n<command-message>model</command-message>",
    "<local-command-stdout>Set model to `Opus 5.5`</local-command-stdout>",
    "[watch] Изменения в сессиях AO с 02:26:\n- tide-stack-6: exited",
    "[alert ← tide/seam] нить встала",
    "[linear SOC-47 «Решить: экран джема»] **Переформулировка задачи (от ao-control):**",
    "Ты запущен tide через Agent Orchestrator. Настоящий проект — /x: работай там",
    "Summarize this agent result as ONE short, substantive line",
    "   ",
])
def test_injected_prompts_are_not_human(prompt):
    assert dialog.human_text(prompt) is None


def test_origin_from_payload_wins():
    assert dialog.human_text("обычный текст", {"kind": "task-notification"}) is None
    assert dialog.human_text("обычный текст", {"kind": "peer", "from": "uds:x"}) is None
    assert dialog.human_text("обычный текст", {"kind": "human"}) == ("ao", "обычный текст")


def test_human_words_kept_cut_and_cleaned():
    assert dialog.human_text("[tg]  Че делается\nпо проектам?") == ("tg", "Че делается по проектам?")
    # AO доставил сводку, пока Гриша печатал: его слова — до вставки
    assert dialog.human_text("план должен быть не только в чате если мы ид[watch] Изменения в"
                             " сессиях AO с 03:11:\n- x") == ("ao", "план должен быть не только"
                                                              " в чате если мы ид")
    assert dialog.human_text("[Image #4] на доске вообще[linear SOC-46 «Уточнить»] v0.28") \
        == ("ao", "[Image #4] на доске вообще")
    assert dialog.human_text("напоминание, ты сам помнишь?\n\nAttached files (read these files"
                             " in the workspace):\n- .ao/attachments/a.png") \
        == ("ao", "напоминание, ты сам помнишь?")
    assert dialog.human_text("срочно почини [AO transcript correlation ID: ao-123]") \
        == ("ao", "срочно почини")


# --- хук: запись в нить ---------------------------------------------------------

def test_hook_records_human_skips_injected(world, monkeypatch):
    _prompt(monkeypatch, "и я думаю тех лид и продукт должны уехать в app-development")
    _prompt(monkeypatch, "<task-notification>\n<task-id>x</task-id>\n</task-notification>")
    _prompt(monkeypatch, '<cross-session-message from="uds:/tmp/a.sock"> привет </cross-session-message>')
    _prompt(monkeypatch, "[tg] тут?")
    lines = _lines(world)
    assert len(lines) == 2
    assert lines[0].endswith(" · Гриша · ao · и я думаю тех лид и продукт должны уехать"
                             " в app-development")
    assert lines[1].endswith(" · Гриша · tg · тут?")
    text = (world["thread"] / "dialog.md").read_text(encoding="utf-8")
    assert text.startswith(dialog.HEADER)
    assert not paths.state_dir(world["proj"]).joinpath(dialog.ERRORS_LOG).exists()


def test_plain_chat_outside_ao_without_pin_is_silent(world, monkeypatch, capsys):
    sink = _alert_sink(world, monkeypatch)
    _prompt(monkeypatch, "привет", sid="33333333-0000-4000-8000-00000000cccc")
    assert _lines(world) == []
    assert not sink.exists()
    assert "журнал диалога" not in capsys.readouterr().err


# --- сбой — сигнал, не тишина ---------------------------------------------------

def test_ao_session_without_thread_is_signalled(world, monkeypatch, capsys):
    sink = _alert_sink(world, monkeypatch)
    monkeypatch.setenv("AO_SESSION_ID", "proj-9")
    _prompt(monkeypatch, "срочно почини", sid="44444444-0000-4000-8000-00000000dddd")
    assert _lines(world) == []
    err = capsys.readouterr().err
    assert "журнал диалога" in err and "срочно почини" in err
    log = paths.state_dir(world["proj"]) / dialog.ERRORS_LOG
    assert "срочно почини" in log.read_text(encoding="utf-8")
    rec = json.loads(sink.read_text(encoding="utf-8").splitlines()[0])
    assert rec["TIDE_ALERT_ROUTE"] == "manager" and "срочно почини" in rec["text"]


def test_write_failure_is_signalled(world, monkeypatch, capsys):
    sink = _alert_sink(world, monkeypatch)
    (world["thread"] / "dialog.md").mkdir()  # запись упадёт: на месте файла каталог
    _prompt(monkeypatch, "хочу иметь лог всего что сделано")
    err = capsys.readouterr().err
    assert "запись" in err and "хочу иметь лог" in err
    rec = json.loads(sink.read_text(encoding="utf-8").splitlines()[0])
    assert "хочу иметь лог" in rec["text"]


def test_hook_crash_is_signalled_not_raised(world, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(dialog, "log_human", boom)
    _prompt(monkeypatch, "привет")
    assert "хук упал (RuntimeError: сломалось)" in capsys.readouterr().err
