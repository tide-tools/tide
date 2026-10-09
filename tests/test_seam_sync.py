"""SOC-143: шов-синк «сделано / дальше / что решить» и почта нити в сиде."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tide import cli, fields, handoff_queue as hq, mail, registry, roster, seam_sync as ss
from tide.arc import stream
from tide.launcher import autohandoff, seed_draft as sd

SEED = """## главное

- цель нити

## где мы

- слой нити
- 2026-10-09T18:00:45 — ПЕРЕДАЧА. Где мы: пакеты Е и Ж сданы (cdc0434 F56; ab3ba1b F57); пара 12 не принята. Дальше: F60
- итог сессии: довести SOC-101

## открытые хвосты

- F60, F59, F63 не начаты (пульс 18:00)
- 4 старых сигнала стопа на доске management открыты (пульс 14:06)
- воркер «F49 ложная тревога» жив в старой сессии — см. «в работе прямо сейчас»

## ждёт Гришу

- нет (решения у менеджера) (пульс 18:00)
- SOC-129 на проверке (In Review) — предлагаю: посмотреть

## следующий шаг

- принять итоги сабагентов (F55+F58, F49) и разослать хеши
- автозакрытие старой после живой пары

## в работе прямо сейчас

- воркер «F49 ложная тревога» — жив в старой сессии (с 17:43)
- (полные тексты входящих — транскрипт /x.jsonl)

## карта входа

- что-то
"""

PASSPORT = """# 02-handoffs-2

title: proj — handoffs · 02
from: handoffs-1

## context
- 2026-10-09T18:04:36 — нить принята — шов приёма закрыт механикой
- 2026-10-09T18:06:55 — Приняла нить: итоги сабагентов F55+F58 и F49 — жду, разошлю хеши; F60, F59, F63 — следом.
- 2026-10-09T18:08:10 — снимаю: сигналы стопа management — не наша нить, их ведёт board
- 2026-10-09T18:10:00 — третий пульс: смотрю доску
- 2026-10-09T18:30:00 — поздний пульс: автозакрытие старой после живой пары
"""


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
    stream.new_thread(proj, "handoffs", goal="передачи")
    old = stream.new_session(proj, "handoffs", "handoffs-1", claude_session="sid-h1")
    new = stream.new_session(proj, "handoffs", "handoffs-2", claude_session="sid-h2")
    (new / "arc.md").write_text(PASSPORT, encoding="utf-8")
    (new / "input").mkdir(exist_ok=True)
    (new / "input" / "handoff-seed.md").write_text(SEED, encoding="utf-8")
    registry.record(home, "sid-h1", "proj-41", "handoffs")
    registry.record(home, "sid-h2", "proj-42", "handoffs")
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv(mail.WATCH_ENV, "0")
    monkeypatch.delenv("TIDE_ALERT_CMD", raising=False)
    monkeypatch.delenv(mail.AO_SESSION_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv(ss.PULSES_ENV, raising=False)
    monkeypatch.chdir(proj)
    sent = []
    monkeypatch.setattr(autohandoff, "_ao_send", lambda h, t: sent.append((h, t)) or 0)
    monkeypatch.setattr(autohandoff, "send_alert", lambda text, **kw: 0)
    return {"home": home, "proj": proj, "old": old, "new": new, "sent": sent,
            "tdir": new.parents[1]}


def _offer(world):
    seed = world["new"] / "input" / "handoff-seed.md"
    path = hq.offer(world["home"], "handoffs", arc="{0}/{1}".format(
        world["tdir"].name, world["new"].name), project="proj", seed=str(seed),
        from_session="sid-h1")
    rec = hq._parse(path)
    hq._set_field(rec["path"], "status", hq.STATUS_TAKEN)
    hq._set_field(rec["path"], "taken-at", "2026-10-09T18:04:00")
    return hq._parse(rec["path"])


def test_seed_items_skip_holes_pointers_and_inflight_echo():
    items = ss.seed_items(SEED)
    assert items["next"] == ["принять итоги сабагентов (F55+F58, F49) и разослать хеши",
                             "автозакрытие старой после живой пары"]
    assert items["inflight"] == ["воркер «F49 ложная тревога» — жив в старой сессии (с 17:43)"]
    assert items["tails"] == ["F60, F59, F63 не начаты",
                              "4 старых сигнала стопа на доске management открыты"]
    assert items["awaiting"] == ["SOC-129 на проверке (In Review) — предлагаю: посмотреть"]


def test_done_from_passing_pulse_keeps_parentheses():
    assert ss.done_lines(SEED) == ["пакеты Е и Ж сданы (cdc0434 F56; ab3ba1b F57)",
                                   "пара 12 не принята"]
    assert ss.done_lines("## где мы\n\n- итог сессии: довести X\n") == ["довести X"]


def test_mentions_by_ids_and_words():
    assert ss.mentions("F60, F59, F63 не начаты", "F60 F63 и F59 дальше")[0]
    assert not ss.mentions("F60, F59, F63 не начаты", "сделала F60")[0]
    assert ss.mentions("SOC-129 на проверке", "SOC-99/101/129 на проверке")[0]  # серия номеров
    assert ss.mentions("автозакрытие старой после живой пары",
                       "автозакрытия старой ждём живую пару")[0]
    assert not ss.mentions("дубли опыта разными словами", "не дублировать board")[0]


def test_reconcile_first_pulses_named_dropped_missing():
    rep = ss.reconcile(SEED, PASSPORT, n=3)
    by = {r["text"]: r for r in rep["items"]}
    assert [p[0] for p in rep["pulses"]] == ["2026-10-09T18:06:55", "2026-10-09T18:08:10",
                                             "2026-10-09T18:10:00"]
    assert by["принять итоги сабагентов (F55+F58, F49) и разослать хеши"]["status"] == "named"
    assert by["F60, F59, F63 не начаты"]["status"] == "named"
    drop = by["4 старых сигнала стопа на доске management открыты"]
    assert drop["status"] == "dropped" and "board" in drop["why"]
    # назван только поздним (четвёртым) пульсом — в первых трёх его нет
    assert by["автозакрытие старой после живой пары"]["status"] == "missing"
    assert rep["gaps"] == 1
    assert ss.reconcile(SEED, PASSPORT, n=4)["gaps"] == 0
    text = ss.render(rep, title="t")
    for head in ("сделано (по ПЕРЕДАЧЕ старой):", "дальше (новая назвала в первых 3 пульсах):",
                 "снято новой:", "не названо и не снято (1):", "что решить (ждёт Гришу):"):
        assert head in text
    assert "- [следующий шаг] автозакрытие старой после живой пары" in text


def test_dry_run_writes_and_sends_nothing(world):
    _offer(world)
    before = (world["new"] / "arc.md").read_text(encoding="utf-8")
    rep = ss.check(world["new"], home=world["home"], dry_run=True,
                   who={"ao_session": "x", "role": "старая", "why": "тест"})
    assert rep["gaps"] == 1 and "handoffs-1 → 02-handoffs-2" in rep["text"]
    assert (world["new"] / "arc.md").read_text(encoding="utf-8") == before
    assert world["sent"] == [] and not (world["tdir"] / "inbox").exists()
    assert not (world["new"] / "workspace" / ss.REPORT_NAME).exists()


def test_confirmer_old_alive_else_main(world):
    rec = _offer(world)
    to = ss.confirmer(world["home"], rec, world["proj"], alive=lambda h: True,
                      main_of=lambda r: {"ao_session": "proj-main"})
    assert to == {"ao_session": "proj-41", "role": "старая", "why": "старая жива"}
    to = ss.confirmer(world["home"], rec, world["proj"], alive=lambda h: False,
                      main_of=lambda r: {"ao_session": "proj-main"})
    assert to["ao_session"] == "proj-main" and to["role"] == "main проекта"
    assert "proj-41 закрыта" in to["why"]


def test_check_mails_list_pings_confirmer_and_stamps_passport(world):
    _offer(world)
    rep = ss.check(world["new"], home=world["home"],
                   who={"ao_session": "proj-41", "role": "старая", "why": "старая жива"})
    assert rep["letter"] == "001"
    letter = world["tdir"] / "inbox" / "001-от-shov-sink.md"
    assert letter.is_file() and "что решить (ждёт Гришу):" in letter.read_text(encoding="utf-8")
    (to, ping), = world["sent"]
    assert to == "proj-41" and len(ping.encode("utf-8")) < 500
    assert "tide mail read" in ping and "tide handoff sync ok" in ping
    passport = world["new"] / "arc.md"
    assert ss._get(passport, ss.SYNC) == ss.WAITING
    assert ss._get(passport, ss.SYNC_GAPS) == "1"
    assert ss._get(passport, ss.SYNC_LETTER) == "001"
    assert (world["new"] / "workspace" / ss.REPORT_NAME).is_file()


def test_ok_acks_letter_and_status_shows_it(world):
    ss.check(world["new"], home=world["home"], who={"ao_session": "", "role": "main проекта",
                                                     "why": "нет адреса"})
    assert world["sent"] == []                       # адреса нет — пинга нет, письмо лежит
    out = ss.answer(world["new"], ss.OK, by="proj-41")
    assert out["sync"] == "ok"
    assert mail.unread(world["tdir"]) == []          # письмо сверки прочитано (квитанция)
    st = ss.status(world["new"])
    assert st[ss.SYNC] == "ok" and st[ss.SYNC_ANSWER] == "proj-41"


def test_fix_mails_correction_to_new(world):
    ss.check(world["new"], home=world["home"], who={"ao_session": "", "role": "-", "why": "-"})
    out = ss.answer(world["new"], ss.FIX, "F63 уже сделан — tide@abc1234", by="proj-41")
    assert out["sync"] == "fix" and out["letter"] == "002"
    body = (world["tdir"] / "inbox" / "002-от-shov-sink.md").read_text(encoding="utf-8")
    assert "поправка от proj-41" in body and "F63 уже сделан" in body
    (to, _ping), = world["sent"]                     # поправка — пингом ведущей нити
    assert to == mail._holder_ao(world["tdir"])
    assert ss.status(world["new"])[ss.SYNC_ANSWER].startswith("proj-41: F63")
    with pytest.raises(ss.SeamSyncError):
        ss.answer(world["new"], ss.FIX, "  ")


def test_status_no_answer_after_timeout(world):
    passport = world["new"] / "arc.md"
    ss._set(passport, ss.SYNC, ss.WAITING)
    ss._set(passport, ss.SYNC_AT, "2026-10-09T18:00:00")
    late = datetime(2026, 10, 9, 18, 0) + timedelta(minutes=ss.NO_ANSWER_MIN + 1)
    assert ss.status(world["new"], now=late)[ss.SYNC] == ss.NO_ANSWER
    early = datetime(2026, 10, 9, 18, 10)
    assert ss.status(world["new"], now=early)[ss.SYNC] == ss.WAITING


def test_cli_handoff_sync_alias(world, capsys):
    assert ss.alias_argv(["handoff", "sync", "ok"]) == ["handoffs", "sync", "ok"]
    assert ss.alias_argv(["handoff", "x"]) == ["handoff", "x"]
    code = cli.main(["handoff", "sync", "check", str(world["new"]), "--dry-run"])
    out = capsys.readouterr().out
    assert code == 2 and "не названо и не снято (1)" in out and "dry-run" in out
    assert cli.main(["handoff", "sync", "status", str(world["new"])]) == 0
    assert "не сверялась" in capsys.readouterr().out


# --- почта нити в сиде ---------------------------------------------------------

def test_unread_mail_lines_in_seed(world):
    assert sd.thread_mail(world["tdir"]) == []
    mail.send("handoffs", "Первая строка письма\nтело", sender="manager-7", do_ping=False,
              watch=False)
    mail.send("handoffs", "Второе\n", sender="main-5", do_ping=False, watch=False)
    lines = sd.thread_mail(world["tdir"])
    assert lines == [
        "- почта: непрочитанное письмо 001 от manager-7: Первая строка письма — "
        "tide mail read proj/handoffs 001",
        "- почта: непрочитанное письмо 002 от main-5: Второе — tide mail read proj/handoffs 002",
    ]
    mail.read("handoffs", "001", by="t")
    assert len(sd.thread_mail(world["tdir"])) == 1
    assert sd.thread_mail(world["tdir"], limit=0) == [
        "- почта: ещё непрочитанных 1 — tide mail ls proj/handoffs"]


def test_unread_mail_reaches_inflight_block(world):
    mail.send("handoffs", "Срочно: проверь F68\n", sender="manager-7", do_ping=False,
              watch=False)
    bodies = sd.draft_bodies(world["proj"], world["old"])
    assert any("письмо 001 от manager-7: Срочно: проверь F68" in ln
               for ln in bodies[sd.INFLIGHT])


def test_seed_header_tells_to_use_thread_mail(tmp_project, monkeypatch):
    from tide import whois
    from tide.launcher import handoff

    monkeypatch.setattr(whois, "manager_address", lambda default="": "management-14")
    thread = stream.new_thread(tmp_project, "main", goal="главная нить")
    stream.new_session(tmp_project, "main", "main-3", goal="шаг")
    new = stream.new_session(tmp_project, "main", "main-4", goal="шаг")
    fields.set_field(new / "arc.md", "from", "main-3")
    head = handoff._with_throughline(thread, new, "# сид").split("\n---\n")[0].splitlines()
    assert head[-2].startswith("правила: работа → в workspace/ своей арки")
    assert head[-1] == ("почта: длинное или важное другой нити — "
                        "tide mail send <проект>/<нить>, не ao send")
