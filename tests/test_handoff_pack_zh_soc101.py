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


# --- F49: две головы — старая работает после приёма без пульсов ----------------

import json as _json  # noqa: E402
from pathlib import Path  # noqa: E402

from tide import handoff_queue as _hq  # noqa: E402
from tide.hooks import handed_off as _ho  # noqa: E402
from tests.test_handed_off_forward import AO_MSG, _hook, handed  # noqa: E402,F401


def _tx(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(_json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def _utc(local_iso):
    from datetime import timezone
    return datetime.fromisoformat(local_iso).astimezone().astimezone(
        timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def test_origin_activity_from_transcript_not_pulses(tmp_path):
    taken = "2026-10-09T14:21:36"
    t = _tx(tmp_path / "tx.jsonl", [
        {"type": "assistant", "timestamp": _utc("2026-10-09T14:22:00"), "message": {}},
        {"type": "assistant", "timestamp": _utc("2026-10-09T14:23:10"), "message": {}},
    ])
    # ход, в котором передала, — в пределах запаса: не улика
    assert _hq.origin_activity("x", taken, transcript=t) is None
    _tx(t, [
        {"type": "assistant", "timestamp": _utc("2026-10-09T14:22:00"), "message": {}},
        {"type": "attachment", "timestamp": _utc("2026-10-09T14:31:57"),
         "attachment": {"type": "queued_command",
                        "prompt": "<ao-session-delivery id=\"s:1\">[from tide-stack-27] x"}},
        {"type": "assistant", "timestamp": _utc("2026-10-09T14:32:05"), "message": {}},
    ])
    ev = _hq.origin_activity("x", taken, transcript=t)
    assert ev and "1 ход" in ev and "1 доставок" in ev and "14:32" in ev


def test_multiples_flags_silent_origin_by_transcript(handed, monkeypatch, tmp_path):
    home = handed["home"]
    rec = _hq.list_offers(home, status=_hq.STATUS_TAKEN)[0]
    assert _hq.multiples(home) == []  # тихая старая — история
    cfg = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    later = (datetime.fromisoformat(rec["taken_at"]) + timedelta(minutes=10)).isoformat()
    _tx(cfg / "projects" / "-x-proj-19" / "old-sid.jsonl",
        [{"type": "assistant", "timestamp": _utc(later), "message": {}}])
    (m,) = _hq.multiples(home)
    assert m["name"] == rec["name"] and "в транскрипте после приёма" in m["evidence"]


def world_proj(handed):
    from tide import roster
    return Path(roster.read_roster(handed["home"])[0]["path"])


def test_two_heads_alarm_goes_to_both_once(handed, monkeypatch):
    home = handed["home"]
    rec = _hq.list_offers(home, status=_hq.STATUS_TAKEN)[0]
    monkeypatch.setattr("tide.arc.holder.ao_handle_for",
                        lambda h, sid: "proj-19" if sid == "old-sid" else "")
    calls = []
    say = lambda h, t: calls.append((h, t)) or True  # noqa: E731
    assert _ho.two_heads_alarm(home, rec, evidence="улика", say=say) == ["proj-24", "proj-19"]
    text = calls[0][1]
    assert "две головы" in text and "нить ведёт новая" in text and "proj-24" in text
    assert "только довести своего воркера и переслать итог новой" in text
    assert "Признак: улика" in text
    assert _ho.two_heads_alarm(home, rec, say=say) == []  # раз на пару
    assert len(calls) == 2


def test_new_writing_to_old_raises_two_heads(handed, monkeypatch, capsys):
    """T27 17:31:36Z → T23 17:31:57Z: новая отдала задачу через старую."""
    assert _hook(monkeypatch, AO_MSG.replace("tide-stack-17", "proj-24")) == 0
    out = capsys.readouterr().out
    assert "не пересылаю" in out and "только довести своего воркера" in out
    assert "Задачу из него не бери" in out
    sent = handed["ao"].calls
    assert sent and sent[0][0] == "proj-24" and "две головы" in sent[0][1]
    assert all("[переслано" not in t for _h, t in sent)


def test_multiples_cli_sends_alarm_and_no_send_only_lists(handed, monkeypatch, tmp_path,
                                                          capsys):
    home = handed["home"]
    rec = _hq.list_offers(home, status=_hq.STATUS_TAKEN)[0]
    cfg = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    later = (datetime.fromisoformat(rec["taken_at"]) + timedelta(minutes=10)).isoformat()
    _tx(cfg / "projects" / "-x" / "old-sid.jsonl",
        [{"type": "assistant", "timestamp": _utc(later), "message": {}}])
    # новая приняла нить по-настоящему: claude-session и чат на диске
    new = world_proj(handed) / ".tide" / "arcs" / rec["arc"].split("/")[0] / "arcs" / \
        rec["arc"].split("/")[1]
    fields.set_field(new / "arc.md", "claude-session", "new-sid")
    _tx(cfg / "projects" / "-y" / "new-sid.jsonl",
        [{"type": "assistant", "timestamp": _utc(later), "message": {}}])
    assert cli.main(["handoffs", "multiples", "--no-send"]) == 1
    assert handed["ao"].calls == []
    assert cli.main(["handoffs", "multiples"]) == 1
    out = capsys.readouterr().out
    assert "в транскрипте после приёма" in out and "→ сказано: proj-24" in out
    assert "две головы" in handed["ao"].calls[0][1]


def test_old_stop_hook_reminds_role_again_after_interval(world, monkeypatch):
    from tests.test_context_watch_handed import _hand_over

    _hand_over(world)
    t0 = datetime(2026, 10, 9, 14, 22)
    sent = []
    monkeypatch.setattr(_ho, "two_heads_alarm",
                        lambda home, rec, **kw: sent.append(kw) or ["x"])
    assert "нить передана" in cw.handed_over_reason(world["sess"], SID, now=t0)
    assert cw.handed_over_reason(world["sess"], SID, now=t0 + timedelta(minutes=5)) == ""
    again = cw.handed_over_reason(world["sess"], SID, now=t0 + timedelta(minutes=16))
    assert "Две головы на нити нельзя" in again
    assert "только довести своего воркера и переслать итог новой" in again
    assert sent and "Stop-хук" in sent[0]["evidence"]
    assert cw.handed_over_reason(world["sess"], SID, now=t0 + timedelta(minutes=20)) == ""


def test_history_pairs_are_listed_but_not_alarmed():
    now = datetime(2026, 10, 9, 15, 0)
    assert _hq.recently_active({"active_at": "2026-10-09T14:45:00"}, now=now)
    assert not _hq.recently_active({"active_at": "2026-10-09T13:00:00"}, now=now)
    assert not _hq.recently_active({}, now=now)
