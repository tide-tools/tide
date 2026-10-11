"""SOC-101 пакет Е — сборщик сида по приёмке acceptance-04 (§9).

C12/F40 открытые хвосты целиком · F37 имя «· NN (ao-id)» · F38 «предлагаю» при пункте ·
F39 опыт без дублей · F42 патчи plan.md · F43 закрытые в Linear помечены ·
F23 окружение со словом · C4 статус решения · C1 цитаты Гриши. Сеть не трогается.
"""

from __future__ import annotations

from tide import handoff_contract as hc
from tide.arc import stream
from tide.launcher import autohandoff as ah
from tide.launcher import reception, seed_draft

# Живые пульсы 03-handoffs-3 (сокращены до сути) — на них нашли C12/F40.
LIVE_CTX = [
    "- 2026-10-09T08:25:39 — Пакет Г сдан (handoffs · 02): 230b37b; pytest 2849. "
    "Открыто: tide verify --portable красный и до нас (132 находки, «Гриш»).",
    "- 2026-10-09T08:49:31 — Д2 сдан: 82d24a5 F36. Не вживую: пересылка queued_command не "
    "проверена; taken-at оставлен механикой; launcher/menu.py не тронут. Д1 ещё работает "
    "— итог Д1 не получен. Лимит использования исчерпан.",
    "- 2026-10-09T08:52:35 — Д1 сдан: 3389076 F30, f64d90f C4; pytest 2884 passed. Хвосты: "
    "warn_missing_map в offload.py при пульсе ПЕРЕДАЧА; сверка решений-обещаний с Linear "
    "(C4, вторая половина).",
    "- 2026-10-09T10:57:56 — ПЕРЕДАЧА handoffs · 03 → 04. Ждёт Гришу: SOC-57 шаг 5 — "
    "переносить ли cost_day в tide; предлагаю — перенести, когда board перестанет звать "
    "его скриптом. Решения: TG Грише о каждом приёме, выключатель TIDE_ACCEPT_NOTIFY; "
    "правило §5 менеджера в силе.",
]


def _block(seed: str, name: str) -> str:
    return seed.partition("## {0}\n\n".format(name))[2].partition("\n## ")[0].strip()


# --- C12/F40: открытые хвосты --------------------------------------------------------

def test_tails_come_whole_from_labels_freshest_first():
    tails = seed_draft.block_tails(LIVE_CTX, seed_draft.labeled_pulses(LIVE_CTX)["tails"])
    assert tails == [
        "- warn_missing_map в offload.py при пульсе ПЕРЕДАЧА (пульс 08:52)",
        "- сверка решений-обещаний с Linear (C4, вторая половина) (пульс 08:52)",
        "- не вживую: пересылка queued_command не проверена; taken-at оставлен механикой; "
        "launcher/menu.py не тронут (пульс 08:49)",
        "- tide verify --portable красный и до нас (132 находки, «Гриш») (пульс 08:25)",
    ]


def test_tails_fall_back_to_marked_phrases_without_labels():
    ctx = ["- 2026-10-09T08:00:00 — сделал А. Тест test_x красный на чужом файле.",
           "- 2026-10-09T08:10:00 — ПЕРЕДАЧА. Всё сдано. Пересылка не проверена вживую."]
    assert seed_draft.block_tails(ctx) == [
        "- Пересылка не проверена вживую (пульс 08:10) (маркер в пульсе)",
        "- Тест test_x красный на чужом файле (пульс 08:00) (маркер в пульсе)",
    ]
    assert seed_draft.block_tails(["- 2026-10-09T08:00:00 — всё сдано"]) == []


def test_tails_section_follows_where_and_is_never_empty():
    seed = seed_draft.render_draft({seed_draft.TAILS: []})
    heads = [ln[3:] for ln in seed.splitlines() if ln.startswith("## ")]
    assert heads[heads.index("где мы") + 1] == hc.TAILS_NAME
    assert _block(seed, hc.TAILS_NAME) == "— нет\n" + hc.TAILS_EMPTY
    assert "хвостов в пульсах не названо" in _block(seed, hc.TAILS_NAME)
    assert reception.open_tails(seed) == []  # приём читает прочерк как «хвостов нет»
    # мерка доски и таблица скилла его не знают — раздел вне контракта
    assert hc.TAILS_NAME not in hc.NAMES and not hc.missing(heads)


def test_live_seed_carries_tails_whole(tmp_project, monkeypatch):
    monkeypatch.delenv("TIDE_DIALOG_LOG", raising=False)
    stream.new_thread(tmp_project, "handoffs", goal="шов передачи")
    sess = stream.new_session(tmp_project, "handoffs", "s1", goal="довести SOC-101")
    arc = sess / "arc.md"
    arc.write_text(arc.read_text(encoding="utf-8").replace(
        "## context\n", "## context\n" + "\n".join(LIVE_CTX) + "\n", 1), encoding="utf-8")
    seed = ah.build_auto_seed(sess)
    tails = _block(seed, hc.TAILS_NAME)
    assert "tide verify --portable красный и до нас (132 находки, «Гриш»)" in tails
    assert "queued_command не проверена; taken-at оставлен механикой" in tails
    assert "сверка решений-обещаний с Linear (C4, вторая половина)" in tails


# --- F40: журнал не режет посреди слова и берёт бюджет ------------------------------

def test_clip_words_never_cuts_a_hash():
    line = "- 08:52 — Д1 сдан: 1b85e41 F28+F29, 6ff8b3e F34, 3388ac0 C11, 3389076 F30, 0e3741d"
    got = seed_draft.clip_words(line, 72)
    assert len(got) <= 72 and got.endswith("…")
    assert got[:-1].split()[-1] in line.split()  # последнее слово — целое


def test_journal_uses_the_budget_not_a_fixed_72():
    ctx = ["- 2026-10-09T07:{0:02d}:00 — шаг {0}: ".format(i) + "слово " * 30
           for i in range(12)]
    ctx = [c.rstrip() for c in ctx]
    full = sum(len(c) + 1 for c in ctx[:7])
    out = seed_draft.block_journal(ctx, budget=int(full * 0.8), source="arc.md")
    body = [ln for ln in out if not ln.startswith("- (сжато")]
    assert len(body) == 7                          # все шаги дошли
    assert max(len(ln) for ln in body) > seed_draft.JOURNAL_GIST  # не жёсткие 72
    assert sum(len(ln) + 1 for ln in out) <= int(full * 0.8)
    for ln in body:
        assert ln.endswith("…") and ln[:-1].split()[-1] == "слово"


# --- F37: «имя · NN (ao-id)» не рвётся ----------------------------------------------

def test_next_keeps_name_with_ao_id():
    got = seed_draft.block_next(["дать номер оффера continuity-audit · 02 (tide-stack-20) · "
                                 "принять вердикт · итог main · 04 (tide-stack-24)"])
    assert got == ["- дать номер оффера continuity-audit · 02 (tide-stack-20)",
                   "- принять вердикт", "- итог main · 04 (tide-stack-24)"]


# --- F38: «предлагаю» остаётся при пункте -------------------------------------------

def test_recommendation_stays_with_its_item():
    got = seed_draft.labeled_pulses(LIVE_CTX)["awaiting"]
    assert got == ["- SOC-57 шаг 5 — переносить ли cost_day в tide; предлагаю — перенести, "
                   "когда board перестанет звать его скриптом (пульс 10:57)"]


# --- C4: статус решения из метки ----------------------------------------------------

def test_label_decisions_carry_a_status():
    got = seed_draft.labeled_pulses(LIVE_CTX)["decisions"]
    assert got == [
        "- TG Грише о каждом приёме, выключатель TIDE_ACCEPT_NOTIFY (статус: не указан) "
        "(пульс 10:57)",
        "- правило §5 менеджера в силе (пульс 10:57)",
    ]


# --- F39: опыт без дублей -----------------------------------------------------------

def test_experience_folds_retold_lines():
    lines = [
        "- tide offload запускать из /x/tide-stack — из каталога tide он ищет не там (пульс 10:57)",
        "- ao send только --session X --message Y (пульс 10:57)",
        "- `tide offload` запускать из /x/tide-stack.",
        "- `ao send` — только `--session X --message Y`.",
        "- Голова не может python.",
        "- голова не может python (пульс 07:48)",
        "- совсем другой урок",
    ]
    got = seed_draft.block_experience([], lines)[:-1]  # без подсказки куратора
    assert got == [lines[0], lines[1], lines[4], lines[6]]


def test_fold_prefers_the_fuller_line_in_place():
    short = "- параллельным сабагентам делить файлы"
    long_ = "- Параллельным сабагентам делить файлы поимённо и гонять pytest (пульс 10:57)"
    assert seed_draft.fold_lines([short, "- другое", long_]) == [long_, "- другое"]


# --- F42: патчи plan.md -------------------------------------------------------------

def test_plan_patches_reach_main(tmp_project):
    thread = stream.new_thread(tmp_project, "handoffs", goal="шов")
    (thread / "plan.md").write_text(
        "# план\n\n## главное — читать первым\n- SOC-101 — шов\n\n- [ ] 4. замер | x | y\n\n"
        "## патчи\n\n- v2 · 09.10:\n  - шаг 4 · уточнение Гриши: расход = токены + "
        "субагенты; контекст и расход не смешивать\n", encoding="utf-8")
    main = seed_draft.block_main(thread)
    assert main[0] == "- SOC-101 — шов"
    assert any(ln.startswith("- патчи плана") and "## патчи" in ln for ln in main)
    assert "контекст и расход не смешивать" in "\n".join(main)


# --- F43: закрытые в Linear помечены ------------------------------------------------

def test_closed_issues_are_marked_where_they_read_as_live():
    live = {"SOC-57": ("Done", "completed"), "SOC-101": ("In Progress", "started")}
    got = seed_draft.mark_closed(["- SOC-57 шаг 5 — cost_day", "- SOC-101 шов"], live)
    assert got == ["- SOC-57 шаг 5 — cost_day (SOC-57 — Done в Linear)", "- SOC-101 шов"]
    assert seed_draft.mark_closed(["- SOC-57"], None) == ["- SOC-57"]


def test_seal_marks_main_and_awaiting(monkeypatch):
    monkeypatch.setattr(seed_draft, "linear_fetch",
                        lambda keys: {"SOC-57": ("Done", "completed")})
    seed_draft._LINEAR_CACHE.clear()
    text = seed_draft.seal({seed_draft.MAIN: ["- рядом SOC-57 (стоимость)"],
                            seed_draft.AWAITING: ["- SOC-57 шаг 5"]}, context=[])
    assert "- рядом SOC-57 (стоимость) (SOC-57 — Done в Linear)" in text
    # SOC-181: «ждёт Гришу» про закрытую задачу — вон из сида, одной строкой с номером
    assert "- SOC-57 шаг 5" not in text
    assert "- снято из сида (отвечено или закрыто в Linear): 1 — SOC-57" in text
    seed_draft._LINEAR_CACHE.clear()


# --- F23: окружение не голым прочерком ----------------------------------------------

def test_environment_bare_dash_is_not_inherited():
    assert seed_draft.block_environment(["—", ""]) == []
    seed = seed_draft.render_draft({seed_draft.ENVIRONMENT: seed_draft.block_environment(["—"])})
    assert _block(seed, "окружение").startswith("— окружение не унаследовано")
    assert seed_draft.block_environment(["- ao: tide-stack-25"]) == ["- ao: tide-stack-25"]


# --- C1: ожидание Гриши из цитат пульсов ---------------------------------------------

def test_expectation_falls_back_to_dated_grisha_quotes():
    ctx = ["- 2026-10-09T07:48:00 — Принял. Мерка C1 — слова Гриши: «если работа идет, она "
           "четко передавалась между сессиями». Дальше коммит.",
           "- без штампа: Гриша сказал «это без даты, не берём»",
           "- 2026-10-09T07:50:00 — просто пульс без цитаты"]
    quotes = seed_draft.grisha_quotes(ctx)
    assert quotes == ["- Мерка C1 — слова Гриши: «если работа идет, она четко передавалась "
                      "между сессиями» (цитата из пульса 2026-10-09 07:48)"]
    assert seed_draft.block_expectation([], None, [], quotes) == quotes
    # слой нити сильнее цитат, метка пульса — тоже
    assert seed_draft.block_expectation(["- слой"], None, [], quotes) == ["- слой"]
    assert seed_draft.block_expectation([], None, ["- метка"], quotes) == ["- метка"]
    assert seed_draft.block_expectation([], None, [], []) == []


def test_short_expectation_label_is_read():
    got = seed_draft.labeled_pulses(["- 2026-10-09T07:00:00 — ПЕРЕДАЧА. Ожидание: «чтобы "
                                     "работа передавалась» (tg 3119)"])
    assert got["expectation"] == ["- «чтобы работа передавалась» (tg 3119) (пульс 07:00)"]


def test_tails_label_is_not_glued_to_expectation():
    got = hc.pulse_labels("ПЕРЕДАЧА. Ожидание Гриши: «чтобы передавалось». Хвосты: A; B")
    assert got == {"expectation": ["«чтобы передавалось»"], "tails": ["A; B"]}
    got = hc.pulse_labels("ПЕРЕДАЧА. Ожидание: «так». Хвосты: нет")
    assert got == {"expectation": ["«так»"], "tails": ["нет"]}


def test_tails_none_is_an_honest_dash_and_overrides_older():
    ctx = ["- 2026-10-09T08:00:00 — Хвосты: старый хвост",
           "- 2026-10-09T09:00:00 — ПЕРЕДАЧА. Хвосты: нет"]
    tails = seed_draft.block_tails(ctx, seed_draft.labeled_pulses(ctx)["tails"])
    assert tails == ["— нет", "(хвостов нет — так сказано в пульсе 09:00)"]
    seed = seed_draft.render_draft({seed_draft.TAILS: tails})
    assert reception.open_tails(seed) == []
    full = seed_draft.render_draft({seed_draft.TAILS: seed_draft.block_tails(
        LIVE_CTX, seed_draft.labeled_pulses(LIVE_CTX)["tails"])})
    assert len(reception.open_tails(full)) == 4
