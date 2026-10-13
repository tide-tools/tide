"""SOC-286 эксп. 2: шов-синк видит план новой и ПЕРЕДАЧУ бюджетного сида; dry-run
автохендоффа показывает строку старт-гейта.

Живой случай (12-@continuity, 01 → 02): сверка писала «не названо и не снято (4)» и
«ПЕРЕДАЧИ в сиде нет», хотя новая назвала пункты в ``--next``/``goal``, а ПЕРЕДАЧА
лежала в ``input/seed-archive.md``; ``handoff --auto --dry-run`` строку старт-гейта не
показывал (она собиралась только в run_handoff).
"""

from __future__ import annotations

from tide import cli, handoff_queue as hq, seam_sync as ss
from tide.launcher import handoff

from tests.test_autohandoff import SID, _prime, world  # noqa: F401 — фикстура

# сид бюджетного сборщика (seed_budget LAYOUT): «сегодня — …, где мы», ПЕРЕДАЧА — отсылкой
SEED = """# management — continuity · 02 · нить 12-@continuity

## сегодня — обещания, сдано, где мы

13.10:
- 02:05 — передача: см. архив «где мы»
- 02:03 — фикс ref 93ffbe3

## следующий шаг

- замер reception_score по 02-сессии
- строка в журнал
- ao send менеджеру main · 31 о ветке

## открытые хвосты

- первым делом новая сессия меряет свой приём скриптом по своей арке и сравнивает с baseline (медиана 7 ходов, 9.6–14.1k токенов)
"""

ARCHIVE = """# архив сида

## главное

- x

## где мы

- 2026-10-13T02:01 — эксп 1 в ветке
- 2026-10-13T02:05:05 — ПЕРЕДАЧА. Сделано: эксп 1 в ветке e1; фикс ref. Дальше: замер

## решения

- y
"""

PASSPORT = """# 02-continuity-2

goal: Замерить свой приём хендоффа против baseline, записать в журнал, сдать итог
status: active

## cursor — resume here
мяч у меня: SOC-286, жду замер помощника

## context
- 2026-10-13T02:06:11 — нить принята — шов приёма закрыт механикой
- 2026-10-13T02:06:33 — Ожидание: меряю свой приём reception_score по арке 02 и сравниваю с baseline (медиана 7 ходов, 9.6–14.1k) · Ждёт Гришу: нет · Нельзя: пушить
## next
замер reception_score по 02 · строка в output/journal.md · ao send + tide mail менеджеру main · 31 о ветке e1
"""


def _status(rep):
    return {r["text"][:20]: r["status"] for r in rep["items"]}


# --- 1. план новой: goal, ## cursor, ## next ------------------------------------

def test_passport_plan_reads_goal_cursor_next_not_context():
    plan = ss.passport_plan(PASSPORT)
    assert any(p.startswith("Замерить свой приём") for p in plan)
    assert "мяч у меня: SOC-286, жду замер помощника" in plan
    assert any("о ветке e1" in p for p in plan)
    assert not any("Ожидание:" in p for p in plan)  # пульсы — не план


def test_passport_plan_skips_placeholders():
    text = "# a\n\ngoal: <one line>\n\n## cursor — resume here\n<где остановились>\n## next\n<шаги>\n"
    assert ss.passport_plan(text) == []


def test_next_section_names_item_missing_from_pulses():
    only_ctx = PASSPORT.split("## next")[0].replace(
        "goal: Замерить свой приём хендоффа против baseline, записать в журнал, сдать итог",
        "goal: x")
    only_ctx = only_ctx.replace("мяч у меня: SOC-286, жду замер помощника", "")
    rep = ss.reconcile(SEED, only_ctx)
    assert _status(rep)["ao send менеджеру ma"] == "missing"
    rep = ss.reconcile(SEED, PASSPORT)
    assert _status(rep)["ao send менеджеру ma"] == "named"


def test_goal_names_journal_item():
    rep = ss.reconcile(SEED, PASSPORT)
    assert _status(rep)["строка в журнал"] == "named"


def test_retell_fillers_do_not_block_a_paraphrased_tail():
    rep = ss.reconcile(SEED, PASSPORT)
    assert _status(rep)["первым делом новая с"] == "named"
    assert rep["gaps"] == 0


def test_drop_in_next_counts():
    text = PASSPORT.replace("## next\n", "## next\nснимаю: строка в журнал — замер в минус · ")
    rows = {r["text"]: r for r in ss.reconcile(SEED, text)["items"]}
    assert rows["строка в журнал"]["status"] == "dropped"


# --- 2. ПЕРЕДАЧА: заголовок «сегодня …», регистр, архив сида ---------------------

def test_where_block_found_under_budget_heading():
    assert ss._block(SEED, ss.WHERE_HEADS)[0].startswith("02:05 — передача")


def test_passing_lowercase_in_seed_is_found():
    seed = SEED.replace("см. архив «где мы»", "Сделано: замер; журнал")
    line, body = ss.passing_line(seed)
    assert line.startswith("02:05 — передача") and body.startswith("Сделано:")
    assert ss.done_lines(seed) == ["замер", "журнал"]


def test_passing_word_inside_text_is_not_a_passing_line():
    seed = SEED.replace("02:03 — фикс ref 93ffbe3", "02:03 — тёплая передача нити обсуждалась")
    seed = seed.replace("- 02:05 — передача: см. архив «где мы»\n", "")
    assert ss.passing_line(seed) == ("", "")


def test_archive_ref_falls_back_to_seed_archive():
    line, body = ss.passing_line(SEED, ARCHIVE)
    assert "ПЕРЕДАЧА" in line and body.startswith("Сделано:")
    assert ss.done_lines(SEED, archive_text=ARCHIVE) == ["эксп 1 в ветке e1", "фикс ref"]
    assert str(ss.passing_at(SEED, ARCHIVE)) == "2026-10-13 02:05:05"
    assert ss.passing_at(SEED) is None  # без архива штампа нет — дата не выдумывается


def test_check_reads_archive_next_to_seed(tmp_path):
    sess = tmp_path / ".tide" / "arcs" / "12-@continuity" / "arcs" / "02-continuity-2"
    (sess / "input").mkdir(parents=True)
    (sess / "input" / "handoff-seed.md").write_text(SEED, encoding="utf-8")
    assert ss.read_archive(sess / "input" / "handoff-seed.md") == ""
    (sess / "input" / "seed-archive.md").write_text(ARCHIVE, encoding="utf-8")
    assert ss.read_archive(sess / "input" / "handoff-seed.md") == ARCHIVE
    rep = ss.reconcile(SEED, PASSPORT, archive_text=ARCHIVE)
    out = ss.render(rep)
    assert "ПЕРЕДАЧИ в сиде нет" not in out and "эксп 1 в ветке e1" in out


# --- 3. dry-run автохендоффа: строка старт-гейта --------------------------------

def test_start_gate_preview_uses_future_session_placeholder(world):  # noqa: F811
    line = handoff.start_gate_preview(world["thread"])
    goal_cmd, offload_cmd = hq.start_gate_commands(world["thread"].name, "<02-seam>")
    assert goal_cmd in line and offload_cmd in line


def test_auto_dry_run_prints_start_gate_and_writes_nothing(world, monkeypatch, capsys):  # noqa: F811
    _prime(world, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    before = sorted(str(p) for p in world["proj"].rglob("*"))
    assert cli.main(["handoff", "continue", "--auto", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "в шапке сида будет: старт-гейт" in out
    assert "tide arc set-goal <02-seam> -p {0}".format(world["thread"].name) in out
    assert sorted(str(p) for p in world["proj"].rglob("*")) == before
    assert hq.list_offers(world["home"]) == []
