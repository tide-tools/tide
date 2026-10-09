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
