"""SOC-181, SOC-179 — личность нити в сиде и чистка оперативки (слово Гриши через менеджера,
письма 046/053/054): статус заменяется, закрытое и дубли выкинуты, потолок 40 КБ, общий
файл граблей — ссылкой. Только временные каталоги; почта и алерты не зовутся (conftest
снимает TIDE_ALERT_CMD и менеджера, здесь — ещё и Linear подменён)."""

from __future__ import annotations

import os
import time
from datetime import datetime

import pytest

from tide import fields, grisha_decisions as gd, handoff_contract as hc
from tide.arc import stream
from tide.launcher import seed_compress as sc, seed_draft as sd

NOW = datetime(2026, 10, 11, 3, 0)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Ни одного похода в Linear, Keychain и к менеджеру."""
    monkeypatch.setenv(sd.LINEAR_OFF_ENV, "1")
    from tide import whois
    monkeypatch.setattr(whois, "manager_address", lambda default="": "management-29")
    sd._LINEAR_CACHE.clear()
    yield
    sd._LINEAR_CACHE.clear()


def _thread(tmp_project, monkeypatch):
    monkeypatch.setenv(gd.AGREEMENTS_ENV, str(tmp_project / "agr"))
    monkeypatch.setenv(gd.DECISIONS_ENV, str(tmp_project / "no-journal.md"))
    thread = stream.new_thread(tmp_project, "main", goal="главная нить")
    stream.new_session(tmp_project, "main", "main-3", goal="шаг")
    new = stream.new_session(tmp_project, "main", "main-4", goal="шаг")
    fields.set_field(new / "arc.md", "from", "main-3")
    return thread, new


# --- 1. личность нити -------------------------------------------------------------

PERSONA = ("правлено: 2026-10-11, main · 19\n\n# Кто я — менеджер студии\n\n"
           "Эта страница дополняет правила.\n\n## 1. Вкус\n\n- «простота и красота»\n")


def test_persona_block_goes_after_rules_before_the_distil(tmp_project, monkeypatch):
    from tide.launcher import handoff

    thread, new = _thread(tmp_project, monkeypatch)
    (thread / sc.PERSONA_FILE).write_text(PERSONA, encoding="utf-8")
    seed = handoff._with_throughline(thread, new, "## главное\n\n- шаг\n")
    head, _sep, distil = seed.partition("\n---\n")
    rules_at = head.index(gd.gotchas_line())
    persona_at = head.index(sc.PERSONA_HEAD)
    assert rules_at < persona_at                       # после правил
    assert distil.lstrip().startswith("## главное")    # до оперативки
    body = head[persona_at:]
    for line in ("правлено: 2026-10-11, main · 19", "Эта страница дополняет правила.",
                 "- «простота и красота»", "### Кто я — менеджер студии", "#### 1. Вкус"):
        assert line in body                            # целиком, заголовки опущены
    # ни один заголовок личности не читается блоком сида
    assert [h for h in sd.sections(seed) if not h.startswith(("нить", "личность", "главное"))] == []
    assert "правила: работа → в workspace/ своей арки" in head  # правила не заменены


def test_no_persona_file_no_block(tmp_project, monkeypatch):
    from tide.launcher import handoff

    thread, new = _thread(tmp_project, monkeypatch)
    seed = handoff._with_throughline(thread, new, "## главное\n\n- шаг\n")
    assert sc.PERSONA_HEAD not in seed
    assert sc.persona_block(thread) == ""
    (thread / sc.PERSONA_FILE).write_text("  \n", encoding="utf-8")
    assert sc.persona_block(thread) == ""


def test_gotchas_line_next_to_agreements(tmp_project, monkeypatch):
    monkeypatch.setenv(gd.AGREEMENTS_ENV, str(tmp_project / "agr"))
    monkeypatch.setenv(gd.GOTCHAS_ENV, str(tmp_project / "gotchas.md"))
    lines = gd.seed_text("нет-такого").splitlines()
    assert lines == [gd.agreements_line(),
                     "грабли всех проектов: {0} (читать на старте)".format(
                         tmp_project / "gotchas.md")]


# --- 2а. статус заменяется, а не дописывается -------------------------------------

LIVE_DONE = {"SOC-106": ("Done", "completed"), "SOC-156": ("In Review", "started")}


def test_linear_tail_is_one_per_line_after_many_builds():
    ln = "- 01 — контекст держит context-keeper (SOC-106) (правило, в силе)"
    for _ in range(5):  # сборка за сборкой: наследование, потом статус из Linear
        ln = sd.inherit_decisions([ln])[0]
        ln = sd.mark_decision_linear([ln], LIVE_DONE)[0]
    assert ln.count("в Linear:") == 1 and ln.count(sd.INHERITED_MARK) == 1
    stacked = ("- SOC-156 смотреть (в Linear: SOC-156 — In Review) "
               "(в Linear: SOC-156 — In Review) (из прошлого сида) (в Linear: SOC-156 — Todo)")
    got = sd.mark_decision_linear(sd.inherit_decisions([stacked]), LIVE_DONE)[0]
    assert got == "- SOC-156 смотреть (из прошлого сида) (в Linear: SOC-156 — In Review)"


def test_closed_tag_is_one_per_line():
    ln = "- шаг SOC-106 (SOC-106 — Done в Linear) (SOC-106 — закрыта в Linear)"
    for _ in range(3):
        ln = sd.mark_closed([ln], LIVE_DONE)[0]
    assert ln == "- шаг SOC-106 (SOC-106 — Done в Linear)"


def test_awaiting_inherited_mark_does_not_pile_up():
    item = "- 1352 слой (пульс 23:15) (из прошлого сида) (SOC-106 — Done в Linear)"
    out = sd.inherit_awaiting([item])
    assert out == ["- 1352 слой (пульс 23:15) (из прошлого сида)"]


# --- 2б. закрытое и отвеченное — вон из сида --------------------------------------

def test_closed_decision_dropped_rule_stays_summary_has_numbers(monkeypatch):
    monkeypatch.setattr(sd, "linear_states", lambda keys: dict(LIVE_DONE))
    bodies = {sd.DECISIONS: ["- SOC-106 заведена и сдана (пульс 02:02)",
                             "- 01 — правило про SOC-106 (правило, в силе)",
                             "- выкат только по да Гриши (пульс 02:02)"],
              sd.SEED_IN_PATH: ["/tmp/prev/handoff-seed.md"]}
    text = sd.seal(bodies, context=[])
    dec = sd.live_lines(sd.contract.find_body(sd.sections(text), "decisions"))
    assert "- SOC-106 заведена и сдана (пульс 02:02)" not in "\n".join(dec)
    assert any(ln.startswith("- 01 — правило про SOC-106") for ln in dec)
    assert "- выкат только по да Гриши (пульс 02:02)" in dec
    assert dec[-1].startswith("- снято из сида (закрыто в Linear): 1 — SOC-106 — на диске: "
                              "/tmp/prev/handoff-seed.md")
    # приём не считает снятое потерей
    prev = "## решения\n\n- SOC-106 заведена и сдана (пульс 02:02) (из прошлого сида)\n"
    assert sd.dropped_decisions(prev, text) == []


def test_answered_awaiting_by_journal_and_by_word(tmp_path):
    journal = tmp_path / "decisions.md"
    journal.write_text("## D-007 · 2026-10-11 01:00\n- вопрос-tg: 1356\n- ответ: «да»\n",
                       encoding="utf-8")
    nums = sc.journal_question_nums(journal)
    assert nums == {"1356"}
    assert sc.is_answered("- 1356 ритуал 8.10 (⭐A как есть) (пульс 23:15)", nums)
    assert sc.is_answered("- ❓ tg 1356 ритуал", nums)
    assert not sc.is_answered("- 1358 меню Samurai (пульс 02:02)", nums)
    assert sc.is_answered("- 1203 имя студии (знак C + A — ответил)", set())
    # пункт про несколько вопросов, отвечен один — остаётся
    assert not sc.is_answered("- 1203 имя (— ответил), tg 1205 выкат, tg 1207 ночь", set())
    prev = "## ждёт Гришу\n\n- 1356 ритуал 8.10 (пульс 23:15) (из прошлого сида)\n"
    new = ("## ждёт Гришу\n\n- 1358 меню\n"
           + sc.dropped_line(1, ["tg 1356"], "отвечено или закрыто в Linear", "x") + "\n")
    assert sd.dropped_awaiting(prev, new) == []


def test_unanswered_keeps_only_grisha_without_reply():
    lines = [
        "- 2026-10-11T01:54:13 · Гриша · tg · Чет жестко много токенов",
        "- 2026-10-11T01:55:09 · агент · reply · Да, много.",
        "- 2026-10-11T01:56:40 · Гриша · tg · Аудит можно?",
        "- 2026-10-11T01:57:53 · агент · reply · Да, запустил.",
        "- 2026-10-11T02:03:00 · Гриша · ao · [from management-28] почта 046 от main",
        "- 2026-10-11T02:05:00 · Гриша · tg · А где результат?",
    ]
    out = sc.only_unanswered(lines, dialog="/x/dialog.md")
    assert out[0] == lines[-1]
    assert out[1].startswith("- отвечено и снято из сида: 2 (последняя 01:56 «Аудит можно?»)")
    assert "пересылок сессий снято: 1" in out[1] and "/x/dialog.md" in out[1]
    done = sc.only_unanswered(lines[:4])
    assert len(done) == 1 and done[0].startswith(hc.EMPTY)


# --- 2в. опыт: дубли склеены, общий файл не копируется, свежее + частое ---------------

def test_experience_duplicates_folded_by_meaning():
    groups = [
        ("pulse", ["- голова не может env, подстановки и kill — через помощника (пульс 20:57)"]),
        ("layer", ["- 09.10 20:57 — Голова: без «env», подстановок, обратных кавычек и kill "
                   "— через помощника · финальный текст хода уходит Грише"]),
        ("seed", ["- голова не может env, подстановки и kill — через помощника (пульс 20:57) "
                  "(из прошлого сида)",
                  "- финальный текст хода уходит Грише — не заканчивать ход без текста "
                  "(пульс 02:02)"]),
    ]
    stats: dict = {}
    out = sc.fold_experience(groups, now=NOW, stats=stats)
    lessons = [ln for ln in out if not ln.startswith("- старше суток")]
    assert stats["raw"] == 5 and stats["merged"] == 3
    assert len(lessons) == 2
    assert sum("голов" in ln.lower() for ln in lessons) == 1
    assert sum("финальный текст" in ln for ln in lessons) == 1


def test_experience_drops_lessons_of_the_shared_gotchas_file():
    gotchas = ["- голова (role-gate) без env-префиксов, переменных, sed, подстановок, "
               "обратных кавычек, kill — через помощника на Sonnet"]
    out = sc.fold_experience(
        [("layer", ["- 09.10 20:57 — голова: без env, подстановок и kill — через помощника",
                    "- 11.10 02:00 — мой урок нити про доску"])], now=NOW, gotchas=gotchas)
    assert out == ["- 11.10 02:00 — мой урок нити про доску"]


def test_experience_fresh_all_old_top_and_pointer():
    def lesson(i):  # три своих слова на урок — разные уроки не похожи
        return "q{0:02d}x r{0:02d}y s{0:02d}z".format(i)

    layer = ["- 11.10 0{0}:00 — свежий {1}".format(i, lesson(i)) for i in range(3)]
    layer += ["- 08.10 10:{0:02d} — старый {1}".format(i, lesson(i + 10)) for i in range(15)]
    layer += ["- 08.10 11:00 — частый урок про перезапуск демона и потерю связи"] * 3
    out = sc.fold_experience([("layer", layer)], now=NOW, top=4, full="слой.md")
    assert out[:3] == ["- 11.10 02:00 — свежий " + lesson(2),
                       "- 11.10 01:00 — свежий " + lesson(1),
                       "- 11.10 00:00 — свежий " + lesson(0)]
    assert out[3] == sc.OLD_HEAD
    assert out[4] == "  - 08.10 11:00 — частый урок про перезапуск демона и потерю связи"
    assert len([ln for ln in out if ln.startswith("  - ")]) == 4
    assert out[-1] == "- (опыт: ещё 12 строк старше суток — полностью: слой.md)"


def test_experience_age_from_the_first_seed_that_had_it(tmp_project):
    thread = stream.new_thread(tmp_project, "board", goal="доска")
    old = stream.new_session(tmp_project, "board", "board-1", goal="шаг")
    new = stream.new_session(tmp_project, "board", "board-2", goal="шаг")
    for sess in (old, new):
        f = sess / "input" / sd.INPUT_SEED_NAME
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("## опыт\n\n- давний урок без даты\n\n## окружение\n\n- доска :8452\n",
                     encoding="utf-8")
    two_days = time.time() - 2 * 86400
    os.utime(old / "input" / sd.INPUT_SEED_NAME, (two_days, two_days))
    hist = sc.SeedHistory(thread)
    out = sc.fold_experience([("seed", ["- давний урок без даты (из прошлого сида)"])],
                             history=hist, top=0, full="слой")
    assert out == ["- (опыт: ещё 1 строк старше суток — полностью: слой)"]
    # 2г: окружение старше суток не переносится
    env = sc.fresh_environment(["- доска :8452"], hist)
    assert len(env) == 1 and env[0].startswith(hc.EMPTY + " окружение старше суток")


# --- 2е. потолок ------------------------------------------------------------------

def _big_seed() -> str:
    def block(name, lines):
        return "## {0}\n\n{1}\n".format(name, "\n".join(lines))
    head = ("## нить (throughline — держи за шагом)\nидёт к: цель\nправила: работа → в workspace/\n"
            "\n" + sc.PERSONA_HEAD + "\n\nличность " + "вкус " * 300 + "\n---\n")
    parts = [
        head,
        block("главное", ["- главное " + "х" * 300]),
        block("ожидание Гриши", ["- 11.10 02:00 — свежее слово Гриши",
                                 "- 01.10 10:00 — старое слово " + "о" * 2000]),
        block("где мы", ["- где " + "г" * 400]),
        block("открытые хвосты", ["- хвост"]),
        block("опыт", ["- свежий урок", sc.OLD_HEAD] + ["  - старый урок {0} ".format(i)
                                                         + "у" * 300 for i in range(12)]),
        block("решения", ["- 01 — правило нити"] + ["- решение {0} ".format(i) + "р" * 300
                                                    + " (из прошлого сида)" for i in range(20)]),
        block("задачи Linear", ["- (статусы — из Linear на момент сборки)",
                                "- SOC-1 — https://linear.app/x/issue/SOC-1 — Todo",
                                "- закрыты в Linear — не открытые задачи нити:"]
              + ["  - SOC-{0} — https://linear.app/x/issue/SOC-{0} — закрыта (Done)".format(i)
                 for i in range(2, 60)]),
        block("ждёт Гришу", ["- да на выкат (пульс 02:02) (из прошлого сида)"]),
        block("следующий шаг", ["- дальше"]),
        block("в работе прямо сейчас", ["- воркер"]),
        block("неотвеченные реплики Гриши",
              ["- 2026-10-11T01:00:00 · агент · reply · " + "а" * 500,
               "- 2026-10-11T01:01:00 · Гриша · tg · где результат?"]),
        block("карта входа", ["- путь"]),
        block("окружение", ["- доска"]),
        block("сырой журнал", ["- 2026-10-11T00:{0:02d}:00 — шаг ".format(i) + "ж" * 400
                               for i in range(40)]),
    ]
    return "\n".join(parts)


PROTECTED = ("нить", "личность", "главное", "где мы", "открытые хвосты", "ждёт гришу",
             "следующий шаг", "в работе прямо сейчас")


def test_cap_keeps_the_limit_and_the_untouchable_blocks():
    text = _big_seed()
    limit = 12_000
    assert len(text.encode()) > 3 * limit
    steps: list = []
    out = sc.cap_seed(text, limit=limit, passport="/p/arc.md", prev_seed="/p/seed.md",
                      goal_file="/t/goal.md", now=NOW, report=steps)
    assert len(out.encode()) <= limit
    before, after = sd.sections(text), sd.sections(out)
    for head in before:
        if head.startswith(PROTECTED):
            # ждёт Гришу: только пометка «(из прошлого сида)» снята как повторный хвост
            want = [sc.strip_status_tails(ln) for ln in before[head]] \
                if head.startswith("ждёт") else before[head]
            assert after[head] == want, head
    assert ("личность " + "вкус " * 300).strip() in out
    assert "- 11.10 02:00 — свежее слово Гриши" in out          # свежее ожидание цело
    assert "- 2026-10-11T01:01:00 · Гриша · tg · где результат?" in out  # слова Гриши
    assert steps[0].startswith("журнал:")                       # порядок среза
    assert sc.CAPPED_MARK in out and "/p/arc.md" in out          # срез — со ссылкой


def test_cap_cuts_only_as_much_as_needed():
    text = _big_seed()
    journal_only = len(text.encode()) - 15_000  # хватит одного журнала
    steps: list = []
    out = sc.cap_seed(text, limit=journal_only, passport="/p/arc.md", report=steps)
    assert len(steps) == 1 and steps[0].startswith("журнал:")
    assert "  - старый урок 0" in out and "решение 19" in out    # дальше не резали
    small = "## главное\n\n- x\n"
    assert sc.cap_seed(small, limit=hc.SEED_MAX_BYTES) == small


def test_ceiling_key_lives_in_the_contract():
    assert hc.SEED_MAX_BYTES == 40_000
    assert sc.SEED_MAX_BYTES == hc.SEED_MAX_BYTES  # одно место: модуль берёт из контракта


def test_capped_seed_through_the_throughline(tmp_project, monkeypatch):
    from tide.launcher import handoff

    thread, new = _thread(tmp_project, monkeypatch)
    (thread / sc.PERSONA_FILE).write_text(PERSONA, encoding="utf-8")
    monkeypatch.setattr(sc, "SEED_MAX_BYTES", 12_000)
    distil = _big_seed().partition("\n---\n")[2]
    real = sc.cap_seed
    monkeypatch.setattr(sc, "cap_seed", lambda text, **kw: real(text, limit=12_000, **kw))
    seed = handoff._with_throughline(thread, new, distil)
    assert len(seed.encode()) <= 12_000
    assert sc.PERSONA_HEAD in seed and "- «простота и красота»" in seed
    assert "main-3" in seed  # ссылки среза — на прошлую сессию нити
