"""SOC-101 пакет Г — слои нити и разметка пульса доходят до сида.

Живая пара из приёмки continuity-audit (обезличена): паспорт передающей сессии
continuity-audit · 01 с пульсом «ПЕРЕДАЧА» и уроками «ОПЫТ:» и сид, который из
него собрал старый код (tests/fixtures/handoff_seed/). В том сиде «опыт» — «уроков
не записано», «ждёт Гришу» — «ничего не ждёт», а ПЕРЕДАЧА обрезана до 72 знаков.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import handoff_contract as hc
from tide.arc import dialog, stream
from tide.launcher import autohandoff as ah, seed_draft

FIX = Path(__file__).parent / "fixtures" / "handoff_seed"


def _block(seed: str, name: str) -> str:
    return seed.partition("## {0}\n\n".format(name))[2].partition("\n## ")[0].strip()


@pytest.fixture
def live(tmp_project, monkeypatch):
    """Нить и сессия, паспорт сессии — живой паспорт continuity-audit · 01."""
    monkeypatch.delenv(dialog.LOG_ENV, raising=False)
    thread = stream.new_thread(tmp_project, "continuity-audit", goal="аудит непрерывности")
    sess = stream.new_session(tmp_project, "continuity-audit", "s1", goal="план v1 аудита")
    (sess / "arc.md").write_text(
        (FIX / "continuity-audit-01-arc.md").read_text(encoding="utf-8"), encoding="utf-8")
    return {"root": tmp_project, "thread": thread, "sess": sess}


# --- п.1 · F22: метки ноты пульса → блоки сида --------------------------------

def test_labels_any_case_many_per_note():
    got = hc.pulse_labels("ПЕРЕДАЧА. Ждёт Гришу: да на PR. В ПОЛЁТЕ: воркер A. "
                          "урок: не резать. Решения: SOC-100 → board; замер = мерка. "
                          "Адресаты: менеджер m-13")
    assert got == {"awaiting": ["да на PR"], "inflight": ["воркер A"],
                   "experience": ["не резать"],
                   "decisions": ["SOC-100 → board; замер = мерка"]}


def test_label_must_start_a_phrase_and_stop_labels_end_the_piece():
    assert hc.pulse_labels("сделал опыт: так себе") == {}  # не метка — середина фразы
    got = hc.pulse_labels("Опыт: раз. Где мы: два. Решение Гриши (через m-7, 09.10): три")
    assert got == {"experience": ["раз"], "decisions": ["три"]}
    assert hc.pulse_head("ПЕРЕДАЧА. Опыт: раз") == "ПЕРЕДАЧА."


def test_live_pass_pulse_feeds_experience_awaiting_inflight(live):
    seed = ah.build_auto_seed(live["sess"])
    exp = _block(seed, "опыт")
    # 5 уроков из ПЕРЕДАЧИ + урок «ОПЫТ:» из пульса 07:05 — каждый пунктом
    assert "- вопросы — менеджеру через uds, не окном AskUserQuestion (пульс 07:30)" in exp
    assert "- голова пишет только tide CLI (role-gate), Linear — через linear-as-manager " \
           "(пульс 07:30)" in exp
    assert "запрет записи у головы — role-gate tide, не баг Claude Code (пульс 07:05)" in exp
    assert exp.index("(пульс 07:30)") < exp.index("(пульс 07:05)")  # свежие сверху
    awaiting = _block(seed, "ждёт Гришу")
    assert awaiting.startswith("- ничего от меня напрямую — порог/модели решает он "
                               "через management-13 (пульс 07:30)")
    inflight = _block(seed, "в работе прямо сейчас")
    assert inflight.startswith("- ничего, все воркеры закончили (пульс 07:30)")
    # стоп-метка «Адресаты:» не утекла в «в работе»
    assert "management-13 (uds" not in inflight


def test_sources_order_pulse_then_thread_layer_then_input_seed(live):
    pp = stream.passport_path(live["thread"])
    text = pp.read_text(encoding="utf-8").partition("## where we are\n")[0]
    pp.write_text(text + "## where we are\n### ожидание Гриши\n- слово (09.10)\n\n"
                  "### где мы\n- идёт приёмка\n\n### ждёт Гришу\n- да на мерж PR #3\n\n"
                  "### опыт\n- урок нити\n", encoding="utf-8")
    seed_in = live["sess"] / "input" / seed_draft.INPUT_SEED_NAME
    seed_in.parent.mkdir(parents=True, exist_ok=True)
    seed_in.write_text("## опыт\n\n- урок входного сида\n", encoding="utf-8")
    seed = ah.build_auto_seed(live["sess"])
    exp = _block(seed, "опыт").splitlines()
    i_pulse = next(i for i, ln in enumerate(exp) if "(пульс 07:30)" in ln)
    assert i_pulse < exp.index("- урок нити") < exp.index("- урок входного сида")
    awaiting = _block(seed, "ждёт Гришу").splitlines()
    assert awaiting[0].endswith("(пульс 07:30)") and awaiting[1] == "- да на мерж PR #3"


def test_state_labels_come_from_the_newest_pulse_only():
    ctx = ["- 2026-10-09T05:00:00 — Ждёт Гришу: старое. Опыт: первый",
           "- 2026-10-09T06:00:00 — Ждёт Гришу: новое. Опыт: второй"]
    got = seed_draft.labeled_pulses(ctx)
    assert got["awaiting"] == ["- новое (пульс 06:00)"]
    assert got["experience"] == ["- второй (пульс 06:00)", "- первый (пульс 05:00)"]


# --- п.2 · F23: пустой блок не врёт ------------------------------------------

_LIES = ("Гришу ничего не ждёт", "в полёте ничего", "ничего не в полёте")


def test_old_live_seed_lied_and_the_new_one_names_checked_sources(tmp_project, monkeypatch):
    old = (FIX / "continuity-audit-02-seed.md").read_text(encoding="utf-8")
    assert any(lie in old for lie in _LIES)  # так было: утверждение вместо дыры
    monkeypatch.delenv(dialog.LOG_ENV, raising=False)
    stream.new_thread(tmp_project, "empty", goal="пустая нить")
    sess = stream.new_session(tmp_project, "empty", "s1", goal="ничего")
    seed = ah.build_auto_seed(sess)
    assert not any(lie in seed for lie in _LIES)
    for name in ("ждёт Гришу", "в работе прямо сейчас", "опыт", "решения"):
        body = _block(seed, name)
        assert body.startswith("— источник пуст: "), (name, body)
    assert "метки «Ждёт Гришу:» в пульсах" in _block(seed, "ждёт Гришу")
    assert "открытые пункты работ нити" in _block(seed, "в работе прямо сейчас")


# --- п.3 · F24/F16: пульсы не режутся, сжатие только по бюджету -------------------

def _pulse_lines(live_sess) -> list:
    text = (live_sess / "arc.md").read_text(encoding="utf-8")
    return [ln for ln in text.partition("## context\n")[2].partition("\n## ")[0].splitlines()
            if ln.startswith("- 20")]


def test_live_pass_pulse_arrives_whole(live):
    old = (FIX / "continuity-audit-02-seed.md").read_text(encoding="utf-8")
    assert "- 2026-10-09T07:30:18 — ПЕРЕДАЧА. Ожидание Гриши: каждая новая сессия п…" in old
    seed = ah.build_auto_seed(live["sess"])
    passing = _pulse_lines(live["sess"])[-1]
    assert "ПЕРЕДАЧА" in passing and len(passing) > 1000
    assert passing in seed.splitlines()  # целиком, ни знака не срезано


def test_pulses_not_clipped_while_the_journal_fits(live):
    # тело сида большое (слой «где мы» нити на 40 строк) — журнал влезает в 30%:
    # ни один пульс, даже длиннее 160 знаков, не режется
    pp = stream.passport_path(live["thread"])
    text = pp.read_text(encoding="utf-8").partition("## where we are\n")[0]
    pp.write_text(text + "## where we are\n### где мы\n" + "".join(
        "- сделано: пункт {0} доведён до приёмки, сдан и проверен руками\n".format(i)
        for i in range(120)), encoding="utf-8")
    seed = ah.build_auto_seed(live["sess"])
    pulses = _pulse_lines(live["sess"])
    assert sum(len(p) > 160 for p in pulses) >= 5
    for ln in pulses:
        assert ln in seed.splitlines(), ln[:60]
    assert "сжато строк" not in seed


def test_live_journal_is_compressed_only_by_budget(live):
    seed = ah.build_auto_seed(live["sess"])
    journal = _block(seed, "сырой журнал")
    # бюджет мал (тело короткое) — старшее сжато, но с честным счётом и путём
    assert "сжато строк" in journal and "arc.md (## context)" in journal


def test_pass_pulse_is_kept_whole_even_when_older_and_over_budget():
    ctx = ["- 2026-10-09T05:{0:02d}:00 — шаг {0}: ".format(i) + "детали " * 40
           for i in range(20)]
    ctx = [c.rstrip() for c in ctx]
    ctx[3] = ("- 2026-10-09T05:03:00 — ПЕРЕДАЧА (черновая). " + "слово " * 80).rstrip()
    out = seed_draft.block_journal(ctx, budget=300, source="arc.md")
    assert ctx[3] in out                      # старше хвоста, бюджет мал — а целиком
    assert any(ln.endswith("…") for ln in out)  # остальное сжато по бюджету
    tail = seed_draft.block_cursor([], ctx, clip=72)
    assert tail[0] == ctx[-1]                 # последний пульс — целиком при любом clip
    assert all(len(ln) <= 72 for ln in tail[1:])


# --- п.4 · F17: уведомления харнесса — не реплики Гриши ---------------------------

# Живая строка из dialog.md нити 31-@main (и сида continuity-audit · 02, S:59).
LIVE_NOTICE = ("- 2026-10-09T05:51:19 · Гриша · ao · Your claude.ai usage limit has reset. "
               "Continue the task you were working on when the limit was reached; do not "
               "repeat work that is already complete.")


@pytest.mark.parametrize("prompt", [
    "Your claude.ai usage limit has reset. Continue the task you were working on.",
    "usage limit has reset — continue",
    "[Cross-session idle notice] tide-stack-10 is idle",
    "[SYSTEM NOTIFICATION - NOT USER INPUT]\nThis is an automated background-task event",
    "<task-notification><task-id>b1</task-id></task-notification>",
    "<ao-session-delivery from=\"x\">[from management-13] сделай</ao-session-delivery>",
    "<cross-session-message from-name=\"board\">готово</cross-session-message>",
])
def test_harness_notices_are_not_written_as_human(prompt, tmp_path):
    assert dialog.human_text(prompt) is None
    thread = tmp_path / "t"
    thread.mkdir()
    assert dialog.log_human(tmp_path, thread, prompt) is None
    assert not dialog.dialog_path(thread).exists()


def test_human_words_before_a_notice_survive():
    assert dialog.human_text("поправь сид [SYSTEM NOTIFICATION - NOT USER INPUT] x") == \
        ("ao", "поправь сид")
    assert dialog.human_text("лимит снова кончился, жди") == ("ao", "лимит снова кончился, жди")


def test_live_notice_already_in_dialog_md_is_dropped_on_assembly(live):
    dialog.dialog_path(live["thread"]).write_text(
        dialog.HEADER + LIVE_NOTICE + "\n- 2026-10-09T06:00:00 · Гриша · ao · где сид?\n",
        encoding="utf-8")
    assert dialog.tail(live["thread"]) == ["- 2026-10-09T06:00:00 · Гриша · ao · где сид?"]
    seed = ah.build_auto_seed(live["sess"])
    assert "usage limit" not in seed


def test_pult_log_notice_rows_are_dropped(live, tmp_path, monkeypatch):
    import json

    log = tmp_path / "own.jsonl"
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in (
        {"ts": "2026-10-09T05:00:00", "dir": "in", "kind": "ao",
         "text": "[Cross-session idle notice] board is idle"},
        {"ts": "2026-10-09T05:01:00", "dir": "in", "kind": "tg", "text": "привет"},
    )) + "\n", encoding="utf-8")
    from tide import fields
    fields.set_field(stream.passport_path(live["thread"]), dialog.LOG_FIELD, str(log))
    assert [ln.rsplit(" · ", 1)[1] for ln in dialog.tail(live["thread"])] == ["привет"]


def test_pass_label_is_the_start_of_the_note_not_a_word_in_it():
    pulse = ["- 2026-10-09T07:12:04 — справки: передача работы 117 фактов",
             "- 2026-10-09T07:30:18 — ПЕРЕДАЧА. всё", "- 2026-10-09T07:31:00 — последний"]
    assert not seed_draft.is_protected_pulse(pulse, 0)
    assert seed_draft.is_protected_pulse(pulse, 1) and seed_draft.is_protected_pulse(pulse, 2)
