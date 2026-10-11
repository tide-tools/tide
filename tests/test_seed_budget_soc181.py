"""SOC-181 шаг б: сид передачи в бюджете знаков (аудит 060, раздел 5 п.1–9).

Каждый тест — пункт аудита. Отправка почты, ao send, linear-as-manager и TIDE_ALERT_CMD
подменены до первого вызова (фикстура ``_no_outbound``): тест ничего не шлёт наружу.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tide import fields, handoff_contract as contract
from tide.arc import stream, thread_layers as tl
from tide.launcher import handoff, seed_budget as sb, seed_compress as sc, seed_draft as sd

NOW = datetime(2026, 10, 11, 12, 0)


@pytest.fixture(autouse=True)
def _no_outbound(monkeypatch, tmp_path):
    """Ничего наружу: почта, алерт, ao send, linear-as-manager — подмена до первого прогона."""
    from tide import mail
    from tide.launcher import autohandoff

    def _boom(*a, **k):
        raise AssertionError("тест пытался отправить наружу: {0!r}".format(a[:1]))

    monkeypatch.setattr(mail, "send", _boom)
    monkeypatch.setattr(autohandoff, "send_alert", _boom)
    monkeypatch.setenv("TIDE_ALERT_CMD", str(tmp_path / "no-alert-cmd"))
    real_run, real_popen = subprocess.run, subprocess.Popen

    def _guard(fn):
        def inner(args, *a, **k):
            argv = args if isinstance(args, (list, tuple)) else str(args).split()
            head = Path(str(argv[0])).name if argv else ""
            if head in ("ao", "linear-as-manager", "tg-alert", "deliver-telegram.py"):
                raise AssertionError("тест позвал {0}".format(head))
            return fn(args, *a, **k)
        return inner

    monkeypatch.setattr(subprocess, "run", _guard(real_run))
    monkeypatch.setattr(subprocess, "Popen", _guard(real_popen))
    monkeypatch.setenv(sd.LINEAR_OFF_ENV, "1")


def _distil(**blocks) -> str:
    """Сид-сборка в форме контракта: блоки по ключам, остальные — прочерком."""
    out = ["# автохендофф — 03-main-3 · нить 01-@main", ""]
    for b in contract.BLOCKS:
        body = blocks.get(b.key) or [contract.empty_line(b)]
        out += ["## " + b.name, ""] + list(body) + [""]
    if blocks.get("tails"):
        out += ["## " + contract.TAILS_NAME, ""] + list(blocks["tails"]) + [""]
    return "\n".join(out)


def _build(distil, **kw):
    kw.setdefault("head", sb.Head(title="# p — main · 04 · нить 01-@main", goal="цель нити",
                                  who="ты — main · 04", manager="менеджер: m-1",
                                  links="правила нити: /x/thread-rules.md (читать на старте)"))
    kw.setdefault("now", NOW)
    return sb.build(distil, **kw)


def _headings(text):
    return [ln[3:] for ln in text.splitlines() if ln.startswith("## ")]


def _block(text, head):
    return text.partition("## " + head)[2].partition("\n## ")[0]


# --- п.1: бюджет в знаках, квоты, архив --------------------------------------------

def _big():
    return _distil(
        experience=["- {0:02d}.10 10:00 — урок про шаг {1} — {2}".format(10, i, "длинно " * 30)
                    for i in range(60)],
        awaiting=["- вопрос {0} к Грише tg {1} ⭐A".format(i, 1000 + i) for i in range(20)],
        expectation=["- 10.10 10:{0:02d} — «цитата Гриши номер {0} про доску и про то, как "
                     "он хочет видеть работу»".format(i) for i in range(50)],
        decisions=["- решение {0} (пульс 10:00)".format(i) for i in range(30)],
        next=["- следующий шаг"],
    )


def test_budget_in_chars_role_and_archive_takes_the_rest():
    got = _build(_big(), budget=sb.ROLE_BUDGET, archive_path="/a/seed-archive.md")
    assert got.report["chars"] <= sb.ROLE_BUDGET == 12_000
    assert len(got.text) <= sb.ROLE_BUDGET
    assert "архив сида: /a/seed-archive.md" in got.text   # одна строка-ссылка
    arch = sd.sections(got.archive)
    assert len(contract.find_body(arch, "experience")) > 10   # не влезшее — целиком в архиве
    assert len(sd.live_lines(contract.find_body(arch, "decisions"))) == 30  # решения — архив


def test_manager_budget_is_16k_by_whois_and_field_overrides(tmp_project, monkeypatch, tmp_path):
    thread = stream.new_thread(tmp_project, "main", goal="нить менеджера")
    mf = tmp_path / "manager.json"
    mf.write_text(json.dumps({"ao_session": "m-1", "thread": "main"}), encoding="utf-8")
    monkeypatch.setenv("TIDE_MANAGER_FILE", str(mf))
    monkeypatch.setenv("TIDE_MANAGER_THREAD", "{0}/main".format(tmp_project.name))
    from tide import whois
    monkeypatch.setattr(whois, "_project_root", lambda home, project: tmp_project)
    assert sb.is_manager_thread(thread) and sb.budget_for(thread) == 16_000
    other = stream.new_thread(tmp_project, "ao-control", goal="операционка")
    assert not sb.is_manager_thread(other) and sb.budget_for(other) == 12_000
    fields.set_field(stream.passport_path(other), contract.SEED_BUDGET_FIELD, "8000")
    assert sb.budget_for(other) == 8000
    got = _build(_big(), budget=16_000, manager=True)
    assert 12_000 < got.report["chars"] <= 16_000


def test_persona_is_outside_the_budget_and_reported():
    persona = "## личность — для творческих задач\n\n" + "вкус и голос. " * 600
    got = _build(_big(), budget=12_000, persona=persona)
    assert got.report["persona"] == len(persona.strip())
    assert got.report["chars"] <= 12_000 < len(got.text)
    assert got.text.index("## личность") < got.text.index("## сегодня")


# --- п.2: порядок блоков; решения, журнал, окружение, Linear — только ссылкой --------

def test_block_order_and_reference_only_blocks():
    distil = _distil(
        main=["- SOC-5 — ведёт нить"], next=["- шаг 1"], inflight=["- воркер жив"],
        awaiting=["- выбор вида ⭐B"], expectation=["- 11.10 10:00 — «делай красиво»"],
        experience=["- 11.10 09:00 — урок"], entry_map=["- /p/plan.md — план"],
        decisions=["- 01 — решение"], journal=["- 2026-10-11T09:00 — пульс"],
        environment=["- прод v1"], linear=["- SOC-5 — https://l/SOC-5 — Todo"])
    got = _build(distil)
    heads = _headings(got.text)
    want = ["кто ты и цель нити", "сегодня — обещания, сдано, где мы", "следующий шаг",
            "в работе сейчас", "почта", "мои открытые SOC", "ждёт Гришу (несёт менеджер)",
            "слова Гриши", "опыт", "карта входа"]
    assert heads == want
    for gone in ("решения", "сырой журнал", "окружение", "задачи Linear"):
        assert gone not in heads
    arch = sd.sections(got.archive)
    assert sd.live_lines(contract.find_body(arch, "decisions")) == ["- 01 — решение"]
    assert "- прод v1" in contract.find_body(arch, "environment")


# --- п.3: наследование со сроком; без пометок в теле --------------------------------

def _seed_file(d: Path, awaiting):
    (d / "input").mkdir(parents=True, exist_ok=True)
    (d / "input" / sd.INPUT_SEED_NAME).write_text(
        "## ждёт Гришу\n\n" + "\n".join(awaiting) + "\n", encoding="utf-8")


def test_inherited_item_lives_one_handoff_then_archive(tmp_path):
    thread = tmp_path / "01-@main"
    s1, s2, born = (thread / "arcs" / n for n in ("01-main-1", "02-main-2", "03-main-3"))
    _seed_file(s1, ["- старый вопрос про бренд ⭐A (пульс 10:00)"])
    _seed_file(s2, ["- старый вопрос про бренд ⭐A", "- новый вопрос про кит ⭐B"])
    distil = _distil(awaiting=["- свежий вопрос про доску ⭐A (пульс 11:00)",
                               "- старый вопрос про бренд ⭐A (из прошлого сида)",
                               "- новый вопрос про кит ⭐B (из прошлого сида)"])
    got = _build(distil, thread_entry=thread, origin=s2, born=born)
    body = _block(got.text, "ждёт Гришу")
    assert "свежий вопрос про доску" in body and "новый вопрос про кит" in body
    assert "старый вопрос про бренд" not in body                # прожил передачу — в архив
    assert "старый вопрос про бренд" in "\n".join(
        contract.find_body(sd.sections(got.archive), "awaiting"))
    assert "(из прошлого сида)" not in got.text and "(пульс " not in got.text


def test_dates_once_per_group_not_per_line():
    distil = _distil(experience=["- 11.10 09:00 — урок раз", "- 11.10 08:00 — урок два",
                                 "- 10.10 07:00 — урок три"])
    body = _block(_build(distil).text, "опыт")
    assert body.count("11.10:") == 1 and body.count("10.10:") == 1
    assert "- урок раз" in body and "11.10 09:00" not in body


# --- п.4: одна цитата — одна строка с первой датой ----------------------------------

def test_quote_dedup_across_wrappers_keeps_first_date():
    distil = _distil(expectation=[
        "- 09.10 19:00 — Гриша 09.10: «Все сразу выкатывай без моего да» (tg 3733)",
        "- 10.10 02:15 — ночью: «все сразу выкатывай без моего да» · «опросы — в Telegram, всегда»",
        "- 11.10 01:00 — 10.10: «Все сразу выкатывай… без моего да!» (tg 3733, 10.10 02:15)"])
    body = _block(_build(distil).text, "слова Гриши")
    assert body.lower().count("выкатывай") == 1
    # строка цитаты — под её первой датой (09.10), новые дни — выше
    assert body.index("10.10:") < body.index("09.10:") < body.lower().index("выкатывай")
    assert "опросы — в Telegram, всегда" in body
    assert "tg 3733" not in body and "Гриша 09.10:" not in body


def test_expectation_layer_capped_and_placeholder_not_printed():
    lines = ["- 10.10 10:{0:02d} — «слово номер {0} про очень важное и длинное»".format(i)
             for i in range(60)] + ["- <тред: the work-line this thread carries>"]
    got = _build(_distil(expectation=lines), budget=16_000)
    words = got.text.partition("## слова Гриши")[2].partition("\n## ")[0]
    assert len(words) <= sb.EXPECTATION_MAX + 50
    assert "<тред:" not in got.text and "the work-line" not in got.text


# --- п.5: почта — только непрочитанное от самого Гриши ------------------------------

def test_mail_is_only_real_grisha_and_agent_mail_by_count():
    distil = _distil(
        unanswered=["- 2026-10-11T02:21:02 · Гриша · tg · сделай карту доски",
                    "- 2026-10-11T02:45:12 · Гриша · ao · [from tide-stack-46] почта 041 от "
                    "tg-pult: решение — tide mail read music/main 041"],
        inflight=["- почта: непрочитанное письмо 110 от tide-stack-46: итог — tide mail read "
                  "management/main 110",
                  "- почта: ещё непрочитанных 3 — tide mail ls management/main",
                  "- воркер жив"])
    got = _build(distil)
    mail = _block(got.text, "почта")
    assert "сделай карту доски" in mail and "[from tide-stack-46]" not in got.text
    assert "писем от сессий непрочитано: 4 — tide mail ls management/main" in mail
    assert "почта: непрочитанное письмо" not in _block(got.text, "в работе сейчас")
    assert "неотвеченные реплики" not in got.text


# --- п.6: окружение не копируется из сида в сид -------------------------------------

def test_environment_not_inherited_from_input_seed(tmp_project):
    thread = stream.new_thread(tmp_project, "env", goal="нить")
    sess = stream.new_session(tmp_project, "env", "env-1", goal="шаг")
    (sess / "input").mkdir(exist_ok=True)
    (sess / "input" / sd.INPUT_SEED_NAME).write_text(
        "## окружение\n\n- прод v0.28.2, main=e872edf\n", encoding="utf-8")
    bodies = sd.draft_bodies(tmp_project, sess)
    assert bodies[sd.ENVIRONMENT] == []
    assert "e872edf" not in sd.render_draft(bodies)


# --- п.7: «ждёт Гришу» — ≤7, без приёмки, с SOC/кто несёт/рекомендацией --------------

def test_awaiting_at_most_seven_without_review_items():
    items = ["- SOC-{0} вопрос номер {0} — предлагаю вариант {0}".format(100 + i) for i in range(10)]
    items += ["- SOC-97 на проверке (In Review) — предлагаю: посмотреть и сказать слово через "
              "менеджера; Done ставит проверяющий",
              "- работа 12 на проверке — проверь и закрой словом"]
    got = _build(_distil(awaiting=items))
    body = [ln for ln in _block(got.text, "ждёт Гришу").splitlines() if ln.startswith("- ")]
    assert len(body) == 7
    assert all("[несёт менеджер; SOC-1" in ln and "рекомендация:" in ln for ln in body)
    assert "проверь и закрой" not in got.text and "на проверке (In Review)" not in got.text
    arch = "\n".join(contract.find_body(sd.sections(got.archive), "awaiting"))
    assert "SOC-97 на проверке" in arch and "SOC-109" in arch   # 8-й и дальше — в архиве
    mgr = _build(_distil(awaiting=["- третья подписка ⭐A"]), manager=True)
    assert "## ждёт Гришу (несу я)" in mgr.text and "[несу я; без SOC; ⭐A]" in mgr.text


def test_awaiting_tag_is_idempotent_across_handoffs():
    once = _build(_distil(awaiting=["- SOC-5 вид кнопки ⭐B"])).text
    line = next(ln for ln in _block(once, "ждёт Гришу").splitlines() if ln.startswith("- "))
    twice = _build(_distil(awaiting=[line + " (из прошлого сида)"])).text
    again = next(ln for ln in _block(twice, "ждёт Гришу").splitlines() if ln.startswith("- "))
    assert again == line and again.count("[несёт") == 1


# --- п.8: шапка — несколько строк, правила одной ссылкой ----------------------------

def test_header_is_short_and_rules_are_one_link(tmp_project, monkeypatch):
    from tide import escalate_rules, whois
    monkeypatch.setattr(whois, "manager_address", lambda default="": "management-14")
    monkeypatch.setenv("TIDE_AGREEMENTS_DIR", str(tmp_project / "agr"))
    monkeypatch.setenv("TIDE_DECISIONS_FILE", str(tmp_project / "no.md"))
    thread = stream.new_thread(tmp_project, "main", goal="главная нить")
    stream.new_session(tmp_project, "main", "main-3", goal="шаг")
    new = stream.new_session(tmp_project, "main", "main-4", goal="шаг")
    fields.set_field(new / "arc.md", "from", "main-3")
    seed = handoff._with_throughline(thread, new, _distil(next=["- шаг"]))
    head = _block(seed, "кто ты и цель нити").strip().splitlines()
    assert len([ln for ln in head if not ln.startswith("- ")]) <= 6
    assert escalate_rules.rules_line() in seed
    for rule in (escalate_rules.MAIL_RULE, escalate_rules.THROTTLE_RULE,
                 escalate_rules.ESCALATE_RULE, escalate_rules.MODEL_RULE):
        assert rule not in seed


# --- п.9: журнал слоёв — отдельным файлом; миграция с --dry-run ---------------------

GOAL = """# main-thread — <тред: the work-line this thread carries>

goal: Главная нить проекта
status: active
kind: thread

## where we are
### ожидание Гриши
- 10.10 10:00 — «к пятнице»

## журнал слоёв
### где мы — заменено 10.10 12:00
- 10.10 11:00 — старое
"""


def test_tidy_goal_dry_run_then_apply(tmp_path):
    g = tmp_path / "main-goal.md"
    g.write_text(GOAL, encoding="utf-8")
    rep = tl.tidy_goal(g, dry_run=True)
    assert rep["placeholder"] and rep["placeholder_fix"] and rep["journal_lines"] == 2
    assert g.read_text(encoding="utf-8") == GOAL and not tl.goal_log_path(g).exists()
    rep = tl.tidy_goal(g, dry_run=False)
    text = g.read_text(encoding="utf-8")
    assert rep["applied"] and "## журнал слоёв" not in text and "<тред:" not in text
    assert text.startswith("# main-thread — Главная нить проекта")
    assert "- 10.10 10:00 — «к пятнице»" in text
    log = tl.goal_log_path(g).read_text(encoding="utf-8")
    assert "### где мы — заменено 10.10 12:00" in log and "- 10.10 11:00 — старое" in log
    assert (tmp_path / "main-goal.md.bak-soc181").read_text(encoding="utf-8") == GOAL
    assert tl.tidy_goal(g, dry_run=False)["applied"] is False   # второй раз — менять нечего


def test_goal_tidy_all_requires_dry_run(monkeypatch):
    class A:
        every, goal_dry, target, as_json = True, False, "", False
    with pytest.raises(tl.LayersError):
        tl.cmd_goal_tidy(A())


def test_where_layer_journal_goes_to_log_file(tmp_project):
    thread = stream.new_thread(tmp_project, "lay", goal="нить")
    goal = stream.passport_path(thread)
    tl.write_layers(goal, {"where": "раз"}, now=datetime(2026, 10, 9, 8, 0))
    tl.write_layers(goal, {"where": "два"}, now=datetime(2026, 10, 9, 9, 0))
    assert tl.JOURNAL_HEAD not in goal.read_text(encoding="utf-8")
    assert "— раз" in tl.goal_log_path(goal).read_text(encoding="utf-8")


# --- приём и аудит: ушедшее в архив — не потеря -------------------------------------

def test_reception_does_not_count_archived_as_lost():
    prev = ("## решения\n\n- 07 — главная правится живой\n\n## ждёт Гришу\n\n"
            "- выбор вида tg 1352 ⭐A\n- третья подписка tg 1367\n")
    distil = _distil(decisions=["- 07 — главная правится живой"],
                     awaiting=["- выбор вида tg 1352 ⭐A"] +
                     ["- пункт {0} tg {1}".format(i, 2000 + i) for i in range(8)] +
                     ["- третья подписка tg 1367"])
    got = _build(distil)
    assert "третья подписка" not in got.text           # девятый пункт — в архиве
    assert sd.dropped_decisions(prev, got.text, archive=got.archive) == []
    assert sd.dropped_awaiting(prev, got.text, archive=got.archive) == []
    assert sd.dropped_awaiting(prev, got.text)          # без архива — была бы «потеря»


def test_audit_and_board_read_the_archive(tmp_path):
    from tide.board import live_projection as lp
    d = tmp_path / "input"
    d.mkdir()
    (d / sd.INPUT_SEED_NAME).write_text("## следующий шаг\n\n- шаг\n", encoding="utf-8")
    (d / sb.ARCHIVE_NAME).write_text("## решения\n\n- 01 — решение\n", encoding="utf-8")
    assert "решение" in sb.read_archive(d / sd.INPUT_SEED_NAME)
    secs = lp._seed_sections(str(d / sd.INPUT_SEED_NAME))
    assert secs["решения"] == ["", "- 01 — решение"] and "следующий шаг" in secs


# --- «сегодня»: срок сессии, обещания с часами, курсор --------------------------------

def test_today_has_eta_promises_and_overdue(tmp_path):
    sess = tmp_path / "02-main-2"
    sess.mkdir()
    (sess / "arc.md").write_text(
        "# 02-main-2\n\neta: 2026-10-11T11:30:00-03:00\ndeliverable: сид 16 тыс.\n\n"
        "## cursor — resume here\nпишу тесты\n\n## context\n"
        "- 2026-10-11T09:00:00 — отчёт менеджеру к 10:30; кадры к 14:00\n"
        "- 2026-10-11T09:30:00 — сдал шаг А, коммит abc123\n", encoding="utf-8")
    got = _build(_distil(next=["- шаг"]), origin=sess)
    today = _block(got.text, "сегодня")
    assert "курсор прошлой сессии: пишу тесты" in today
    assert "срок сессии: к 11:30 — сид 16 тыс. — просрочено" in today
    assert "обещано к 10:30 (сказано 09:00): отчёт менеджеру к 10:30 — просрочено" in today
    assert "обещано к 14:00" in today and "кадры к 14:00 — просрочено" not in today
    assert "09:30 — сдал шаг А, коммит abc123" in today
    assert today.count("11.10:") == 1


# --- мои открытые SOC: назначенные этой нити, In Progress/Todo ------------------------

def test_my_soc_only_assigned_in_progress_or_todo():
    assert sd.parse_taker("Назначено: tide-stack — board · 11") == ("tide-stack", "board")

    class Live(dict):
        takers = {"SOC-1": [("p", "main")], "SOC-2": [("p", "other")], "SOC-3": [("p", "main")]}
    live = Live({"SOC-1": ("In Progress", "started"), "SOC-2": ("Todo", "unstarted"),
                 "SOC-3": ("In Review", "started"), "SOC-4": ("Todo", "unstarted")})
    mine = sd.mine_issues(["SOC-1", "SOC-2", "SOC-3", "SOC-4"], live, ["p", "main"])
    assert mine == ["SOC-1", "SOC-3"]
    lines = sd.block_links("SOC-1 SOC-3 SOC-4", live=live, mine=mine)
    got = _build(_distil(linear=lines))
    soc = _block(got.text, "мои открытые SOC")
    assert "SOC-1 — In Progress" in soc and "SOC-3" not in soc and "SOC-4" not in soc


# --- свободный текст передачи: шапка + бюджет разделами -------------------------------

def test_free_text_distil_is_cut_by_sections_into_archive():
    text = "# итог\n\n" + "".join("## раздел {0}\n\n{1}\n\n".format(i, "текст " * 400)
                                  for i in range(10))
    kept, gone = sb.fit_free(text, 12_000)
    assert len(kept) <= 12_000 and "## раздел 9" in gone and kept.startswith("# итог")
