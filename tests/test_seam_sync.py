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
    monkeypatch.setattr(ss, "AUTO_SINCE", "2000-01-01T00:00:00")
    monkeypatch.chdir(proj)
    sent = []
    monkeypatch.setattr(autohandoff, "_ao_send", lambda h, t: sent.append((h, t)) or 0)
    monkeypatch.setattr(autohandoff, "send_alert", lambda text, **kw: 0)
    monkeypatch.setattr(ss, "_ao_alive", lambda h: True)   # живость — без живого ao
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
    assert items["awaiting"] == []   # In Review — Done ставит менеджер, не Гриша


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
    for head in ("сделано (по ПЕРЕДАЧЕ старой):", "дальше (новая назвала в 3 пульсах после приёма):",
                 "снято новой:", "не названо и не снято (1):", "что решить (ждёт Гришу):"):
        assert head in text
    assert "- [следующий шаг] автозакрытие старой после живой пары" in text


def test_dry_run_writes_and_sends_nothing(world):
    _offer(world)
    before = (world["new"] / "arc.md").read_text(encoding="utf-8")
    rep = ss.check(world["new"], home=world["home"], dry_run=True, n=3, commits=[],
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
    to = ss.confirmer(world["home"], rec, world["proj"], alive=lambda h: h != "proj-41",
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
    assert ss._get(passport, ss.SYNC_GAPS) == "0"   # все пульсы после приёма
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
    assert code == 0 and "не названо и не снято (0)" in out and "dry-run" in out
    assert cli.main(["handoff", "sync", "check", str(world["new"]), "--dry-run",
                     "--pulses", "3"]) == 2
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
    assert head[-5].startswith("правила: работа → в workspace/ своей арки")
    # срезы окна п.2–3 (tg 1223): итог сабагента короткий, ao send — только сигнал
    from tide import escalate_rules
    assert head[-4] == escalate_rules.SUBAGENT_RESULT_RULE
    assert head[-3] == escalate_rules.MAIL_RULE
    assert "только сигнал" in head[-3] and "tide mail send <проект>/<нить>" in head[-3]
    assert sum("почта:" in ln for ln in head) == 1  # ужесточили, не продублировали
    # SOC-157: опыт сразу + «не справляешься — tide escalate»
    from tide import escalate
    assert head[-2] == escalate.EXP_RULE and head[-1] == escalate.ESCALATE_RULE


# --- поправки живой пары 04→05 ----------------------------------------------------

ORDER = ("поручено мне — от tide-stack-20-9e в 13:27: Срочный баг от continuity-audit · 02 "
         "(tide-stack-20) для handoffs · 04, по поручению менеджера. Автопередача встаёт…")


def _ctx(*lines):
    return "# x\n\n## context\n" + "".join(
        "- 2026-10-09T19:{0:02d}:00 — {1}\n".format(i, ln) for i, ln in enumerate(lines))


def test_order_dropped_by_sender_id():
    seed = "## в работе прямо сейчас\n\n- " + ORDER + "\n"
    for pulse in ("снимаю: поручение tide-stack-20-9e 13:27 (ложный стоп) — сделано SOC-121",
                  "снимаю: поручено мне — от tide-stack-20-9e в 13:27, срочный баг — сделан как SOC-121"):
        (row,) = ss.reconcile(seed, _ctx(pulse))["items"]
        assert row["status"] == "dropped", pulse
    # чужой id (tide-stack-2) не снимает поручение tide-stack-20-9e
    (row,) = ss.reconcile(seed, _ctx("снимаю: поручение tide-stack-2 — сделано"))["items"]
    assert row["status"] == "missing"
    assert "tide-stack-20-9e" in ss.ids(ORDER)


def test_all_pulses_after_take_by_default():
    rep = ss.reconcile(SEED, PASSPORT)
    assert len(rep["pulses"]) == 4 and rep["gaps"] == 0


def test_done_after_passing_moves_items_out_of_next():
    seed = SEED.replace("- автозакрытие старой после живой пары",
                        "- автозакрытие старой после F49 и живой пары")
    commits = ["0e8dcd8 fix(handoff): SOC-101 F60 — входящие", "b76d40a fix: SOC-101 F59",
               "c8b6ad4 fix: SOC-101 F63", "a2bc46b fix: SOC-101 F49 — две головы"]
    rep = ss.reconcile(seed, PASSPORT, commits=commits)
    by = {r["text"]: r for r in rep["items"]}
    assert by["F60, F59, F63 не начаты"]["status"] == "done"
    assert "F60 0e8dcd8" in by["F60, F59, F63 не начаты"]["by"]
    assert by["воркер «F49 ложная тревога» — жив в старой сессии (с 17:43)"]["status"] == "done"
    # F49 — условие («после F49»), не работа пункта
    assert by["автозакрытие старой после F49 и живой пары"]["status"] != "done"
    # F55+F58 не сделаны — пункт остаётся в «дальше»
    assert by["принять итоги сабагентов (F55+F58, F49) и разослать хеши"]["status"] == "named"
    text = ss.render(rep)
    assert "сделано и закрыто после передачи" in text and "F63 c8b6ad4" in text


def test_done_from_pulse_hash_and_word():
    assert ss.done_in_line("закрыто F60 tide@0e8dcd8") == {"f60": "0e8dcd8"}
    assert ss.done_in_line("tide@cdc0434 F56, ab3ba1b F57") == {"f56": "cdc0434",
                                                                "f57": "ab3ba1b"}
    assert set(ss.done_in_line("закрыто F56/F60/F63", "п")) == {"f56", "f60", "f63"}
    assert ss.done_in_line("F61/F62 сданы") == {"f61": "сделано", "f62": "сделано"}
    assert ss.done_in_line("F60 следом; хеш 0e8dcd8 у SOC-121") == {}
    assert ss.f_ids("F55–F58") == {"f55", "f56", "f57", "f58"}


def test_closed_orders_info_letter_and_pulse_names_sender():
    info = ("поручено мне — от tide-stack-26 в 17:55: спасибо. Нить continuity-audit "
            "передана, теперь её ведёт continuity-audit · 04")
    ask = "поручено мне — от tide-stack-30 в 17:55: новые дефекты, проверь"
    seed = "## в работе прямо сейчас\n\n- {0}\n- {1}\n".format(info, ask)
    rep = ss.reconcile(seed, _ctx("смотрю доску"))
    st = {r["text"][:40]: r["status"] for r in rep["items"]}
    assert st[info[:40]] == "closed" and st[ask[:40]] == "missing"
    rep = ss.reconcile(seed, _ctx("ответила tide-stack-30 по дефектам"))
    assert all(r["status"] == "closed" for r in rep["items"])
    # «жду от tide-stack-30» — ожидание, не запись письма (F60)
    rep = ss.reconcile(seed, _ctx("запросила у tide-stack-30, жду к 15:15"))
    assert [r["status"] for r in rep["items"]][1] != "closed"


def test_awaiting_only_grisha_decisions():
    seed = """## следующий шаг

- src tg:N по схеме context-keeper

## ждёт Гришу

- src tg:N по схеме context-keeper (из следующего шага)
- SOC-99 на проверке (In Review) — предлагаю: посмотреть; Done ставит проверяющий
- SOC-57 шаг 5 — ждёт Гришу: выбрать A или B
- ❓ Грише: SOC-101 на проверке (In Review) — согласен ли с объёмом
"""
    assert ss.seed_items(seed)["awaiting"] == [
        "SOC-57 шаг 5 — ждёт Гришу: выбрать A или B",
        "❓ Грише: SOC-101 на проверке (In Review) — согласен ли с объёмом"]


# --- автозапуск и напоминание ---------------------------------------------------

def _fresh_world(world, pulses=2):
    now = datetime.now().replace(microsecond=0)
    lines = ["- {0} — нить принята — шов приёма закрыт механикой".format(
        (now - timedelta(minutes=5)).isoformat())]
    lines += ["- {0} — пульс {1}: итоги сабагентов F55+F58 и F49, F60 F59 F63".format(
        (now - timedelta(minutes=4 - i)).isoformat(), i) for i in range(pulses)]
    (world["new"] / "arc.md").write_text(
        "# 02-handoffs-2\n\ntitle: proj — handoffs · 02\nfrom: handoffs-1\n\n## context\n"
        + "\n".join(lines) + "\n", encoding="utf-8")
    rec = _offer(world)
    hq._set_field(rec["path"], "taken-at", (now - timedelta(minutes=6)).isoformat())
    return hq._parse(rec["path"])


def test_auto_sync_on_third_pulse_once(world, monkeypatch, capsys):
    _fresh_world(world, pulses=1)
    monkeypatch.setenv(ss.REPO_ENV, "")
    monkeypatch.setattr(ss, "confirmer", lambda *a, **k: {
        "ao_session": "proj-41", "role": "старая", "why": "старая жива"})
    inbox = world["tdir"] / "inbox"
    ref = world["new"].name
    assert cli.main(["offload", ref, "второй пульс: смотрю доску"]) == 0
    assert not inbox.exists() and ss._get(world["new"] / "arc.md", ss.SYNC) == ""
    assert cli.main(["offload", ref, "третий пульс: автозакрытие старой после живой пары"]) == 0
    assert "шов-синк ушёл сам — письмо 001" in capsys.readouterr().out
    assert (inbox / "001-от-shov-sink.md").is_file()
    assert ss._get(world["new"] / "arc.md", ss.SYNC) == ss.WAITING
    assert [to for to, _ in world["sent"]] == ["proj-41"]
    assert cli.main(["offload", ref, "четвёртый пульс"]) == 0
    assert sorted(x.name for x in inbox.glob("*.md")) == ["001-от-shov-sink.md"]
    assert len(world["sent"]) == 1


def test_auto_sync_on_drop_pulse_and_never_breaks_offload(world, monkeypatch):
    rec = _fresh_world(world, pulses=0)
    passport = world["new"] / "arc.md"
    assert not ss.due(world["new"], rec)
    with passport.open("a", encoding="utf-8") as fh:
        fh.write("- {0} — снимаю: сигналы стопа — нить board\n".format(
            datetime.now().replace(microsecond=0).isoformat()))
    assert ss.due(world["new"], rec)

    def boom(*a, **k):
        raise RuntimeError("почта легла")

    monkeypatch.setattr(ss, "check", boom)
    assert cli.main(["offload", world["new"].name, "пульс"]) == 0   # offload цел
    assert ss._get(passport, ss.SYNC) == ""                        # повторит на следующем


def test_auto_sync_skips_old_take_and_new_mode(world):
    rec = _fresh_world(world, pulses=3)
    assert ss.due(world["new"], rec)
    late = datetime.now() + timedelta(hours=ss.AUTO_FRESH_HOURS, minutes=1)
    assert not ss.due(world["new"], rec, now=late)        # приём старше 2 ч — история
    assert ss.AUTO_FRESH_HOURS <= 2


def test_auto_sync_never_before_feature_on(world, monkeypatch):
    rec = _fresh_world(world, pulses=3)
    monkeypatch.setattr(ss, "AUTO_SINCE", (datetime.now() + timedelta(minutes=1)).isoformat())
    assert not ss.due(world["new"], rec)                   # приём до включения — не трогаем
    assert ss.auto_check(world["new"], home=world["home"]) is None
    assert not (world["tdir"] / "inbox").exists()
    assert not ss.due(world["new"], dict(rec, mode="new"))


def test_remind_manager_once_after_30_min(world):
    _fresh_world(world, pulses=3)
    passport = world["new"] / "arc.md"
    now = datetime.now().replace(microsecond=0)
    ss._set(passport, ss.SYNC, ss.WAITING)
    ss._set(passport, ss.SYNC_TO, "старая proj-41 (старая жива)")
    ss._set(passport, ss.SYNC_LETTER, "001")
    ss._set(passport, ss.SYNC_AT, (now - timedelta(minutes=10)).isoformat())
    got = []
    alert = lambda text, **kw: got.append((text, kw)) or 0  # noqa: E731
    assert ss.remind(world["home"], alert=alert) == []           # 10 мин — рано
    ss._set(passport, ss.SYNC_AT, (now - timedelta(minutes=ss.NO_ANSWER_MIN + 5)).isoformat())
    (text,) = ss.remind(world["home"], alert=alert)
    assert got[0][1]["route"] == "manager"
    assert "нет ответа 35 мин от старая proj-41" in text and "\n" not in text
    assert ss.remind(world["home"], alert=alert) == []           # одна на пару
    assert len(got) == 1 and ss._get(passport, ss.SYNC_REMINDED)


def test_confirmer_is_manager_when_new_is_main(world, tmp_path):
    rec = _offer(world)
    ss._set(world["new"] / "arc.md", "claude-session", "sid-h2")   # новая = proj-42
    mg = lambda: {"ao_session": "mgmt-20"}  # noqa: E731
    # адрес main проекта = адрес самой новой (proj-42) — сам себе не подтверждает
    to = ss.confirmer(world["home"], rec, world["proj"], alive=lambda h: h != "proj-41",
                      main_of=lambda r: {"ao_session": "proj-42"}, new_dir=world["new"],
                      manager_of=mg)
    assert to["ao_session"] == "mgmt-20" and to["role"] == "менеджер"
    assert "новая — сама main проекта" in to["why"]
    to = ss.confirmer(world["home"], rec, world["proj"], alive=lambda h: h != "proj-41",
                      main_of=lambda r: {"ao_session": "proj-main"}, new_dir=world["new"],
                      manager_of=mg)
    assert to["ao_session"] == "proj-main"
    main_new = tmp_path / "x" / "31-@main" / "arcs" / "05-main-5"
    assert ss._is_main_thread(main_new.parents[1]) and not ss._is_main_thread(world["tdir"])


def test_letter_says_who_confirms_and_why(world):
    _offer(world)
    rep = ss.check(world["new"], home=world["home"], commits=[], who={
        "ao_session": "proj-main", "role": "main проекта", "why": "старая proj-41 закрыта"})
    body = (world["tdir"] / "inbox" / "001-от-shov-sink.md").read_text(encoding="utf-8")
    assert ("подтверждаешь ты — main проекта proj-main, потому что старая proj-41 закрыта"
            in body)
    assert rep["text"].splitlines()[1].startswith("подтверждаешь ты — ")


def _stamp_to(world, to):
    ss.check(world["new"], home=world["home"], who=to)
    world["sent"].clear()


def test_new_cannot_confirm_itself(world):
    """Живой дефект context-keeper-4: sync-to main tide-stack-31, ok поставила сама новая."""
    _stamp_to(world, {"ao_session": "proj-31", "role": "main проекта", "why": "старая закрыта"})
    passport = world["new"] / "arc.md"
    ss._set(passport, "claude-session", "sid-h2")          # новая = proj-42 по реестру
    assert "proj-42" in ss.new_self(world["new"])
    with pytest.raises(ss.SeamSyncError, match="подтверждает proj-31, не ты"):
        ss.answer(world["new"], ss.OK, by="proj-42")
    with pytest.raises(ss.SeamSyncError):                  # и под именем нить·сессия
        ss.answer(world["new"], ss.FIX, "всё не так", by="handoffs·02-handoffs-2")
    assert ss._get(passport, ss.SYNC) == ss.WAITING and not ss._get(passport, ss.SYNC_ANSWER)


def test_new_found_by_title_when_no_registry(world):
    passport = world["new"] / "arc.md"
    ss._set(passport, "title", "proj — handoffs · 02 · proj-77")
    assert ss.new_self(world["new"])[0] == "proj-77"
    _stamp_to(world, {"ao_session": "proj-31", "role": "main проекта", "why": "-"})
    with pytest.raises(ss.SeamSyncError):
        ss.answer(world["new"], ss.OK, by="proj-77")


def test_stranger_accepted_with_mark_human_plain(world):
    _stamp_to(world, {"ao_session": "proj-31", "role": "main проекта", "why": "старая закрыта"})
    out = ss.answer(world["new"], ss.OK, by="proj-9", me=["proj-42"])
    assert out["answer"] == "proj-9 (не назначенный: proj-9 вместо proj-31)"
    assert ss.status(world["new"])[ss.SYNC_ANSWER] == out["answer"]
    out = ss.answer(world["new"], ss.OK, by="proj-31", me=["proj-42"])
    assert out["answer"] == "proj-31"
    out = ss.answer(world["new"], ss.OK, me=["proj-42"])   # без AO_SESSION_ID — человек
    assert out["answer"] == "человек"


def test_assigned_of():
    assert ss.assigned_of("main проекта tide-stack-31 (старая tide-stack-22 закрыта)") == "tide-stack-31"
    assert ss.assigned_of("старая proj-41 (старая жива)") == "proj-41"
    assert ss.assigned_of("main проекта без адреса (нет адреса)") == ""
    assert ss.assigned_of("") == ""


def test_reset_drops_answer_back_to_waiting(world):
    _stamp_to(world, {"ao_session": "proj-31", "role": "main проекта", "why": "-"})
    passport = world["new"] / "arc.md"
    ss.answer(world["new"], ss.OK, by="proj-9", me=["proj-42"])
    ss._set(passport, ss.SYNC_REMINDED, "2026-10-09T19:00:00")
    inbox = sorted((world["tdir"] / "inbox").iterdir())
    rc = cli.main(["handoff", "sync", "reset", str(world["new"]), "--why", "ok поставила сама новая"])
    assert rc == 0
    assert ss._get(passport, ss.SYNC) == ss.WAITING
    for key in (ss.SYNC_ANSWER, ss.SYNC_ANSWER_AT, ss.SYNC_REMINDED):
        assert ss._get(passport, key) == ""
    assert ss._get(passport, ss.SYNC_LETTER) == "001"     # прежнее письмо остаётся
    text = passport.read_text(encoding="utf-8")
    ctx = text.split("## context", 1)[1]
    assert "ответ снят — было sync: ok, ответ proj-9" in ctx and "ok поставила сама новая" in ctx
    assert ctx.strip().splitlines()[-1].startswith("- ") and "ответ снят" in ctx.strip().splitlines()[-1]
    assert sorted((world["tdir"] / "inbox").iterdir()) == inbox and world["sent"] == []
    with pytest.raises(ss.SeamSyncError):
        ss.reset(world["new"], "  ")


# --- подтверждать некому: адресат к main проекта, иначе самоподтверждение ---------

def _resolver(main, manager):
    def resolve(*a, **k):
        return ss.confirmer(*a, main_of=lambda r: {"ao_session": main},
                            manager_of=lambda: {"ao_session": manager}, **k)
    return resolve


def test_confirmer_nobody_when_new_is_main_and_manager(world):
    """Живой случай management/main: старая management-23 закрыта, новая management-24 —
    сама main и сам менеджер → подтверждать некому."""
    rec = _offer(world)
    ss._set(world["new"] / "arc.md", "claude-session", "sid-h2")   # новая = proj-42
    to = ss.confirmer(world["home"], rec, world["proj"], alive=lambda h: h != "proj-41",
                      main_of=lambda r: {"ao_session": "proj-42"}, new_dir=world["new"],
                      manager_of=lambda: {"ao_session": "proj-42"})
    assert to["role"] == ss.NOBODY and to["ao_session"] == ""
    assert "proj-41 закрыта" in to["why"] and "сам менеджер" in to["why"]
    assert "подтверждать некому" in ss.addressee_line(to)
    # main проекта закрыт и менеджер закрыт — тоже некому
    to = ss.confirmer(world["home"], rec, world["proj"],
                      alive=lambda h: h not in ("proj-41", "proj-main", "mgmt-20"),
                      main_of=lambda r: {"ao_session": "proj-main"}, new_dir=world["new"],
                      manager_of=lambda: {"ao_session": "mgmt-20"})
    assert to["role"] == ss.NOBODY and "main проекта proj-main закрыт" in to["why"]
    # main закрыт, менеджер жив — менеджер
    to = ss.confirmer(world["home"], rec, world["proj"],
                      alive=lambda h: h not in ("proj-41", "proj-main"),
                      main_of=lambda r: {"ao_session": "proj-main"}, new_dir=world["new"],
                      manager_of=lambda: {"ao_session": "mgmt-20"})
    assert to["ao_session"] == "mgmt-20" and to["role"] == "менеджер"


def test_self_confirm_when_nobody_to_confirm(world):
    """management/main: sync-to назначен менеджеру, а менеджер — сама новая (до починки — тупик)."""
    _stamp_to(world, {"ao_session": "proj-42", "role": "менеджер",
                      "why": "старая proj-41 закрыта; новая — сама main проекта"})
    passport = world["new"] / "arc.md"
    ss._set(passport, "claude-session", "sid-h2")
    out = ss.answer(world["new"], ss.OK, by="proj-42", alive=lambda h: h != "proj-41",
                    resolve=_resolver("proj-42", "proj-42"))
    assert out["sync"] == "ok"
    assert out["answer"].startswith("proj-42 (самоподтверждение: подтверждать некому — ")
    assert ss._get(passport, ss.SYNC_TO).startswith(ss.NOBODY + " без адреса (")
    assert "адресат сменился" in passport.read_text(encoding="utf-8")
    assert world["sent"] == []                       # пинговать некого
    assert mail.unread(world["tdir"]) == []          # письмо сверки прочитано


def test_dead_old_moves_to_main_then_self_when_main_is_new(world):
    """tide-stack/handoffs: sync-to остался на старой, а она закрылась — новая не может
    подтвердить, адресат переходит к main проекта (пинг ему); main — сама новая → некому."""
    _stamp_to(world, {"ao_session": "proj-41", "role": "старая", "why": "старая жива"})
    passport = world["new"] / "arc.md"
    ss._set(passport, "claude-session", "sid-h2")
    dead_old = lambda h: h != "proj-41"  # noqa: E731
    with pytest.raises(ss.SeamSyncError, match="подтверждает proj-31, не ты.*адресат сменился"):
        ss.answer(world["new"], ss.OK, by="proj-42", alive=dead_old,
                  resolve=_resolver("proj-31", "mgmt-20"))
    assert ss.assigned_of(ss._get(passport, ss.SYNC_TO)) == "proj-31"
    assert ss._get(passport, ss.SYNC) == ss.WAITING
    (to, ping), = world["sent"]
    assert to == "proj-31" and "tide handoff sync ok" in ping
    out = ss.answer(world["new"], ss.OK, by="proj-31", alive=dead_old)
    assert out["answer"] == "proj-31"
    # вариант: старая мертва, main проекта — сама новая, менеджер закрыт → самоподтверждение
    _stamp_to(world, {"ao_session": "proj-41", "role": "старая", "why": "старая жива"})
    out = ss.answer(world["new"], ss.OK, by="proj-42",
                    alive=lambda h: h not in ("proj-41", "mgmt-20"),
                    resolve=_resolver("proj-42", "mgmt-20"))
    assert "самоподтверждение: подтверждать некому" in out["answer"]
    assert "менеджер mgmt-20 закрыт" in out["answer"]


def test_live_old_still_refuses_new(world):
    """Обычный случай: старая жива и это не новая — отказ новой как был, адрес не трогаем."""
    _stamp_to(world, {"ao_session": "proj-41", "role": "старая", "why": "старая жива"})
    passport = world["new"] / "arc.md"
    ss._set(passport, "claude-session", "sid-h2")
    with pytest.raises(ss.SeamSyncError, match="подтверждает proj-41, не ты"):
        ss.answer(world["new"], ss.OK, by="proj-42", resolve=_resolver("proj-42", "proj-42"))
    assert ss._get(passport, ss.SYNC_TO) == "старая proj-41 (старая жива)"
    assert world["sent"] == [] and ss._get(passport, ss.SYNC) == ss.WAITING


def test_remind_skips_dead_old_and_pings_main(world):
    _fresh_world(world, pulses=3)
    passport = world["new"] / "arc.md"
    now = datetime.now().replace(microsecond=0)
    ss._set(passport, ss.SYNC, ss.WAITING)
    ss._set(passport, ss.SYNC_TO, "старая proj-41 (старая жива)")
    ss._set(passport, ss.SYNC_LETTER, "001")
    ss._set(passport, ss.SYNC_AT, (now - timedelta(minutes=ss.NO_ANSWER_MIN + 5)).isoformat())
    got = []
    (text,) = ss.remind(world["home"], alert=lambda t, **kw: got.append(t) or 0,
                        alive=lambda h: h != "proj-41", resolve=_resolver("proj-31", "mgmt-20"))
    assert "от main проекта proj-31" in text and "от старая proj-41" not in text
    assert [to for to, _ in world["sent"]] == ["proj-31"]
