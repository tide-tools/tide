"""SOC-101 пакет Ж — передача по порогу контекста (acceptance-05 §9).

F44 — хук порога даёт готовый шаблон пульса ПЕРЕДАЧА, ``handoff --auto`` без
свежей ПЕРЕДАЧИ предупреждает (``--strict`` — отказывает).
F53 — ``tide thread layer <нить> --expect … --where … --exp …``: голова вписывает
слои нити командой, без записи файлов руками.

Всё на временных каталогах (фикстура ``world`` из test_autohandoff): ни настоящих
AO-сессий, ни ao send, ни Telegram.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tide import cli, fields, handoff_contract, handoff_queue as hq, offload
from tide.arc import stream, thread_layers as tl
from tide.hooks import context_watch as cw, role_gate
from tide.launcher import autohandoff as ah, seed_draft
from tests.test_autohandoff import SID, _payload, world  # noqa: F401 — фикстура


# --- F44: шаблон ПЕРЕДАЧИ -----------------------------------------------------

def test_pass_template_labels_all_parse_into_seed_blocks():
    """Метки шаблона через « · » разбираются сборщиком в свои блоки."""
    note = ("ПЕРЕДАЧА. Где мы: шаг 3 · Ожидание Гриши: «доска с телефона» (tg 3365) · "
            "Ждёт Гришу: SOC-112 на приёмке · В полёте: воркер v2.4 · Хвосты: нет · "
            "Решения: вкладка РЕШЕНИЯ · Опыт: role-gate режет curl · "
            "Карта: /abs/plan.md — план")
    got = handoff_contract.pulse_labels(note)
    assert got == {"expectation": ["«доска с телефона» (tg 3365)"],
                   "awaiting": ["SOC-112 на приёмке"], "inflight": ["воркер v2.4"],
                   "tails": ["нет"], "decisions": ["вкладка РЕШЕНИЯ"],
                   "experience": ["role-gate режет curl"],
                   "entry_map": ["/abs/plan.md — план"]}
    assert handoff_contract.is_pass_note(note)
    # шаблон-заготовка — тоже ПЕРЕДАЧА со всеми метками
    keys = set(handoff_contract.pulse_labels(ah.PASS_TEMPLATE_NOTE))
    assert {"expectation", "awaiting", "inflight", "tails", "decisions", "experience",
            "entry_map"} <= keys


def test_soft_hook_gives_pass_template_then_layers_then_auto(world):
    r = cw.watch_reason(world["proj"], _payload(world, 203_000))
    assert r
    i_pass = r.index('tide offload seam/s1 "ПЕРЕДАЧА. Где мы:')
    i_layer = r.index("tide thread layer seam --expect")
    i_auto = r.index(cw.AUTO_CMD)
    assert i_pass < i_layer < i_auto
    for label in ("Ожидание Гриши: «…» (tg N)", "Ждёт Гришу:", "В полёте:", "Хвосты:",
                  "Решения:", "Опыт:", "Карта: <путь> — зачем"):
        assert label in r
    assert "<что сделал>" not in r


@pytest.mark.parametrize("tokens,word", [(300_000, "жёсткий порог"), (510_000, "ТРЕВОГА")])
def test_hard_and_alarm_hooks_give_the_same_template(world, tokens, word):
    r = cw.watch_reason(world["proj"], _payload(world, tokens))
    assert word in r and ah.pass_template(world["sess"]) in r
    assert "tide thread layer seam" in r and cw.AUTO_CMD in r


def test_hook_commands_pass_the_role_gate(world):
    r = cw.watch_reason(world["proj"], _payload(world, 300_000))
    cmds = [ln.strip().lstrip("0123456789. ") for ln in r.splitlines()
            if ln.strip().lstrip("0123456789. ").startswith("tide ")]
    assert len(cmds) == 3
    for c in cmds:
        assert role_gate._bash_refusal(c) is None, c


def _pulse(sess, note, at):
    offload.write_pulse(sess, note=note, now=at)


def test_fresh_pass_note_window(world, monkeypatch):
    now = datetime(2026, 10, 9, 14, 30)
    assert ah.fresh_pass_note(world["sess"], now=now) is None
    _pulse(world["sess"], "ПЕРЕДАЧА. Ожидание Гриши: «x»", now - timedelta(minutes=40))
    assert ah.fresh_pass_note(world["sess"], now=now) is None  # старше 30 мин
    monkeypatch.setenv(ah.PASS_FRESH_ENV, "60")
    assert ah.fresh_pass_note(world["sess"], now=now).startswith("ПЕРЕДАЧА")
    monkeypatch.delenv(ah.PASS_FRESH_ENV)
    _pulse(world["sess"], "сделано: передача работы 117 фактов", now - timedelta(minutes=1))
    assert ah.fresh_pass_note(world["sess"], now=now) is None  # не метка — слово
    _pulse(world["sess"], "ПЕРЕДАЧА. Где мы: тут", now - timedelta(minutes=5))
    assert "Где мы: тут" in ah.fresh_pass_note(world["sess"], now=now)
    assert ah.pass_warning(world["sess"], now=now) is None


def test_auto_dry_run_without_pass_warns_and_shows_template(world, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    assert cli.main(["handoff", "continue", "--auto", "--dry-run"]) == 0
    cap = capsys.readouterr()
    assert "нет пульса ПЕРЕДАЧА за последние 30 мин" in cap.err
    assert ah.pass_template(world["sess"]) in cap.err
    assert "пульса ПЕРЕДАЧА нет — шаблон" in cap.out
    # --strict на dry-run — только предупреждение, ничего не пишет и не падает
    assert cli.main(["handoff", "continue", "--auto", "--dry-run", "--strict"]) == 0
    assert "с --strict передачу не делаю" in capsys.readouterr().err


def test_auto_strict_refuses_without_pass(world, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    monkeypatch.setenv("TIDE_HANDOFF_NO_SPAWN", "1")
    with pytest.raises(ah.AutoHandoffError, match="--strict"):
        ah.run_auto(world["proj"], session_ref=None, strict=True, spawn=False)
    assert hq.list_offers(world["home"]) == []
    assert not (fields.read_field(world["sess"] / "arc.md", ah.HANDED_OFF_FIELD) or "")


def test_auto_default_warns_but_hands_over(world, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    res = ah.run_auto(world["proj"], spawn=False)
    assert res.pass_warning and res.handoff is not None
    assert "нет пульса ПЕРЕДАЧА" in capsys.readouterr().err


def test_auto_with_fresh_pass_is_silent(world, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    offload.write_pulse(world["sess"], note=ah.PASS_TEMPLATE_NOTE.replace("…", "x"))
    assert cli.main(["handoff", "continue", "--auto", "--dry-run", "--strict"]) == 0
    cap = capsys.readouterr()
    assert "ПЕРЕДАЧА за последние" not in cap.err and "шаблон:" not in cap.out


# --- F53: tide thread layer ---------------------------------------------------

def test_thread_layer_cli_fills_exactly_the_seed_layers(world, capsys):
    thread = world["thread"]
    assert tl.empty_layers(thread)
    rc = cli.main(["thread", "layer", "seam", "--expect", "«доска с телефона» (tg 3365)",
                   "--where", "v2.4 сдана · воркер жив", "--exp", "role-gate режет curl"])
    assert rc == 0
    assert tl.empty_layers(thread) == []
    lays = seed_draft.layers(stream.passport_path(thread).read_text(encoding="utf-8"))
    assert any("«доска с телефона» (tg 3365)" in ln for ln in seed_draft.layer(lays, "expectation"))
    assert any("v2.4 сдана" in ln for ln in seed_draft.layer(lays, "where"))
    assert any("role-gate" in ln for ln in seed_draft.layer(lays, "experience"))
    assert "заполнены" in capsys.readouterr().out
    # повтор дописывает, не затирает; заглушек нет
    cli.main(["thread", "layer", "seam", "--expect", "второе слово"])
    text = stream.passport_path(thread).read_text(encoding="utf-8")
    body = text.partition("### ожидание Гриши")[2].partition("###")[0]
    assert "(tg 3365)" in body and "второе слово" in body and "<" not in body


def test_thread_layer_on_thread_without_skeleton(world):
    pp = stream.passport_path(world["thread"])
    head = pp.read_text(encoding="utf-8").partition("## where we are")[0]
    pp.write_text(head.rstrip() + "\n\n## where we are\n<the live state>\n\n## tail\nx\n",
                  encoding="utf-8")
    assert tl.write_layers(pp, {"where": "шаг 2"}) == ["где мы"]
    text = pp.read_text(encoding="utf-8")
    assert "## tail\nx" in text and "шаг 2" in text
    assert tl.empty_layers(world["thread"]) == ["ожидание Гриши", "опыт"]


def test_thread_layer_needs_a_value(world):
    with pytest.raises(tl.LayersError, match="tide thread layer seam"):
        cli_args = type("A", (), {"target": "seam", "layer_expect": None,
                                  "layer_where": " ", "layer_exp": None})()
        tl.cmd_layer(cli_args)


def test_layers_warning_names_the_layer_command(world):
    w = tl.exit_warning(world["thread"])
    assert "tide thread layer seam --expect" in w
    assert role_gate._bash_refusal(tl.layer_cmd("seam")) is None


# --- F47: агентская доставка в обёртке pasted_content — не реплика Гриши -------

# Промпт как в T23 17:09:09Z (queued_command, origin.kind human): доставка AO,
# обёрнутая харнессом в pasted_content, закрывающий тег — посреди текста.
_PASTED_DELIVERY = (
    '<pasted_content id="29c7">\n<ao-session-delivery id="session-send:78ff2cdf">\n'
    '[from tide-stack-22] [context-keeper · 03] Первые журналы легли, 7 решений:\n'
    '- /x/decisions-tg.jsonl — 3 строки\n{"answer": {"text": "Ну надо с умом выбирать '
    'соннет или \n</pasted_content id="29c7">\n\nопус"}, "status": "принято"}\n'
    '</ao-session-delivery>')


def test_pasted_ao_delivery_is_not_grisha(tmp_path):
    from tide.arc import dialog

    assert dialog.human_text(_PASTED_DELIVERY, {"kind": "human"}) is None
    thread = tmp_path / "t"
    thread.mkdir()
    assert dialog.log_human(tmp_path, thread, _PASTED_DELIVERY,
                            origin={"kind": "human"}) is None
    assert not dialog.dialog_path(thread).exists()


def test_pasted_cross_session_and_uds_header_are_not_grisha():
    from tide.arc import dialog

    uds = ('Another Claude session sent a message:\n<cross-session-message from="uds:/s" '
           'from-name="app-development-22-cf">\nответы дизайнера\n</cross-session-message>'
           '\n\nThis came from another Claude session — not typed by your user.')
    assert dialog.human_text(uds) is None
    wrapped = '<pasted_content id="q1">\n' + uds + '\n</pasted_content id="q1">'
    assert dialog.human_text(wrapped, {"kind": "human"}) is None


def test_grisha_paste_keeps_his_words():
    from tide.arc import dialog

    said = dialog.human_text('<pasted_content id="ab">\nлог ошибки\n</pasted_content '
                             'id="ab">\n\nпочини это', {"kind": "human"})
    assert said == ("ao", "лог ошибки почини это")
