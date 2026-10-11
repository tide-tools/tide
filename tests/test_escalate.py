"""SOC-157: `tide escalate` — письмо main проекта, без квитанции 30 мин — тревога менеджеру."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tide import cli, escalate, fields, mail, registry, roster
from tide.arc import stream
from tide.launcher import autohandoff, seed


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
    stream.new_thread(proj, "main", goal="главная")
    m = stream.new_session(proj, "main", "main-1", claude_session="sid-m1")
    fields.set_field(m / "arc.md", "title", "proj — main · 01")
    registry.record(home, "sid-m1", "proj-5", "main")
    stream.new_thread(proj, "worker", goal="работа")
    stream.new_session(proj, "worker", "worker-1", claude_session="sid-w1")
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv(mail.WATCH_ENV, "0")
    monkeypatch.delenv("TIDE_ALERT_CMD", raising=False)
    monkeypatch.setenv(mail.AO_SESSION_ENV, "proj-9")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.chdir(proj)
    sent, alerts = [], []
    monkeypatch.setattr(autohandoff, "_ao_send", lambda h, t: sent.append((h, t)) or 0)
    monkeypatch.setattr(autohandoff, "send_alert",
                        lambda text, **kw: alerts.append((text, kw)) or 0)
    return {"proj": proj, "sent": sent, "alerts": alerts,
            "main": mail.thread_dir("main")}


def _esc(**kw):
    return escalate.escalate("тест падает на CI", "перезапуск дважды, локально зелёный",
                             "доступ к логам CI", thread="worker", **kw)


def test_letter_lands_in_main_with_contract_fields(world):
    out = _esc()
    assert out["role"] == "main проекта" and out["num"] == "001"
    text = (world["main"] / "inbox" / "001-от-proj-9.md").read_text(encoding="utf-8")
    assert "from: proj-9" in text
    body = text.split("---\n\n", 1)[1]
    assert body.startswith("эскалация: тест падает на CI\n")
    assert "Почему: тест падает на CI" in body
    assert "Пробовал: перезапуск дважды, локально зелёный" in body
    assert "Нужно: доступ к логам CI" in body
    assert "нить proj/worker" in body
    (to, ping), = world["sent"]
    assert to == "proj-5" and "эскалация: тест падает на CI" in ping
    rec = mail.letter(world["main"], "1")
    assert rec["kind"] == escalate.KIND and rec["alert_after"] == escalate.ACK_TIMEOUT_MIN


def test_all_three_fields_required(world):
    with pytest.raises(escalate.EscalateError):
        escalate.escalate("что", "", "нужно", thread="worker")


def test_no_ack_after_threshold_alerts_manager(world):
    _esc()
    rec = mail.letter(world["main"], "1")
    sent_at = mail._parse_iso(rec["sent"])
    before = sent_at + timedelta(minutes=escalate.ACK_TIMEOUT_MIN - 1)
    # до порога ретраи почты ещё идут (15 мин), тревоги нет
    assert mail.step(world["main"], "1", now=before) in ("wait", "retry")
    assert world["alerts"] == []
    after = sent_at + timedelta(minutes=escalate.ACK_TIMEOUT_MIN)
    assert mail.step(world["main"], "1", now=after) == "alert"
    (text, kw), = world["alerts"]
    assert kw["route"] == "manager"
    assert kw.get("urgent") is True     # SOC-179: эскалация — сразу, не в пачку пульта
    assert "эскалация 001" in text and "тест падает на CI" in text
    assert mail.step(world["main"], "1", now=after) == "done"   # один раз
    assert len(world["alerts"]) == 1


def test_ack_means_no_alert(world):
    _esc()
    mail.read("main", "1", by="proj-5")
    rec = mail.letter(world["main"], "1")
    late = mail._parse_iso(rec["sent"]) + timedelta(hours=2)
    assert mail.step(world["main"], "1", now=late) == "acked"
    assert world["alerts"] == []


def test_plain_letter_keeps_old_retry_window(world):
    mail.send("main", "обычное", sender="x")
    rec = mail.letter(world["main"], "1")
    assert rec["alert_after"] is None
    at = mail._parse_iso(rec["sent"]) + timedelta(minutes=escalate.ACK_TIMEOUT_MIN)
    assert mail.step(world["main"], "1", now=at) != "alert"
    assert world["alerts"] == []


def test_main_itself_escalates_to_manager_thread(world, monkeypatch, tmp_path):
    from tide import whois

    monkeypatch.setenv(whois.MANAGER_THREAD_ENV, "proj/boss")
    stream.new_thread(world["proj"], "boss", goal="менеджер")
    out = escalate.escalate("что", "пробовал", "нужно", thread="main")
    assert out["role"] == "менеджер" and out["to"].endswith("boss")


def test_cli_escalate(world, capsys):
    rc = cli.main(["escalate", "--what", "застрял", "--tried", "две попытки",
                   "--need", "решение", "--thread", "worker"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "эскалация 001 → proj/main (main проекта)" in out
    assert (world["main"] / "inbox" / "001-от-proj-9.md").is_file()


def test_rules_carry_thresholds_and_land_in_seed():
    r = escalate.seed_rules()
    assert 'tide thread layer --exp "…"' in r and "tide escalate" in r
    assert "{0} неудачные попытки".format(escalate.FAILED_ATTEMPTS) in r
    assert "{0} мин без прогресса".format(escalate.STALL_MIN) in r
    s = seed.build_seed(project_name="proj", arc_ref="worker-1", thread_name="worker",
                        arc_text="x")
    assert escalate.ESCALATE_RULE in s and escalate.EXP_RULE in s


# --- закрытие: tide escalate --close NNN (SOC-157, просьба доски) ------------------

def _closed_lines(tdir):
    return [e for e in mail.events(tdir) if e.get("ev") == mail.CLOSED_EV]


def test_close_writes_one_event_line(world):
    _esc()
    out = escalate.close("1", word="логи выдал main", thread="worker")
    assert out["already"] is False and out["to"] == "proj/main"
    (ev,) = _closed_lines(world["main"])
    assert ev["num"] == "001" and ev["by"] == "proj-9" and ev["word"] == "логи выдал main"
    assert mail._parse_iso(ev["at"]) is not None
    assert mail.letter(world["main"], "1")["closed"] == ev


def test_close_default_addressing_from_session(world, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-w1")
    _esc()
    out = escalate.close(1)          # без --thread: нить сессии → её main
    assert out["to"] == "proj/main" and len(_closed_lines(world["main"])) == 1


def test_close_explicit_box_thread(world):
    _esc()
    out = escalate.close("001", thread="proj/main")
    assert out["to"] == "proj/main" and len(_closed_lines(world["main"])) == 1


def test_close_twice_is_noop(world, capsys):
    _esc()
    assert cli.main(["escalate", "--close", "1", "--word", "решено", "--thread", "worker"]) == 0
    assert "закрыта" in capsys.readouterr().out
    assert cli.main(["escalate", "--close", "1", "--thread", "worker"]) == 0
    assert "уже закрыта" in capsys.readouterr().out
    assert len(_closed_lines(world["main"])) == 1


def test_close_unknown_num_errors(world):
    _esc()
    with pytest.raises(escalate.EscalateError, match="письма 007 нет"):
        escalate.close("7", thread="worker")
    assert _closed_lines(world["main"]) == []


def test_close_plain_letter_is_not_escalation(world):
    mail.send("main", "обычное письмо", sender="x")
    with pytest.raises(escalate.EscalateError, match="не эскалация"):
        escalate.close("1", thread="worker")
    assert _closed_lines(world["main"]) == []


def test_closed_escalation_never_alerts(world):
    _esc()
    escalate.close("1", thread="worker")
    rec = mail.letter(world["main"], "1")
    late = mail._parse_iso(rec["sent"]) + timedelta(hours=2)
    assert mail.step(world["main"], "1", now=late) == "closed"
    assert world["alerts"] == [] and world["sent"][1:] == []   # ни тревоги, ни повтора
    assert mail.pending(world["main"]) == []                    # ожидальщику ждать нечего


def test_close_after_alert_does_not_repeat(world):
    _esc()
    rec = mail.letter(world["main"], "1")
    after = mail._parse_iso(rec["sent"]) + timedelta(minutes=escalate.ACK_TIMEOUT_MIN)
    assert mail.step(world["main"], "1", now=after) == "alert"
    out = escalate.close("1", thread="worker")
    assert out["alerted"] is True
    assert mail.step(world["main"], "1", now=after + timedelta(hours=1)) == "closed"
    assert len(world["alerts"]) == 1


def test_cli_close_flags(world, capsys):
    with pytest.raises(SystemExit):
        cli.main(["escalate", "--help"])
    h = capsys.readouterr().out
    assert "--close NNN" in h and "--word" in h
    assert cli.main(["escalate", "--close", "1", "--what", "x"]) != 0
    assert cli.main(["escalate", "--what", "x"]) != 0
