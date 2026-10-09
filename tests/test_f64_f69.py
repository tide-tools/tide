"""SOC-101 F64 и F69 (acceptance-07 §9 п.1 и п.6): «имя · NN →» в «следующем шаге»
и блок «решения» — статус из Linear, без дублей с меткой, без «статус: не указан»."""
from tide.launcher import seed_draft


# --- F64: « · NN» перед →, скобкой, запятой, концом — часть имени ----------------------

def test_f64_case_from_seed_is_whole():
    # S:142–143 сида main · 05 — закрыто a4655bc (F58), держим тестом
    got = seed_draft.block_next(["F58–F61 у handoffs · 04 → пара 14 · проверка"])
    assert got == ["- F58–F61 у handoffs · 04 → пара 14", "- проверка"]


def test_f64_any_word_before_number_before_arrow():
    got = seed_draft.block_next(["отдать нити · 04 → пара 14 · проверка"])
    assert got == ["- отдать нити · 04 → пара 14", "- проверка"]


def test_f64_number_before_bracket_comma_end():
    assert seed_draft.block_next(["сдать пульту · 4 (tide-stack-20) · дальше"]) == [
        "- сдать пульту · 4 (tide-stack-20)", "- дальше"]
    assert seed_draft.block_next(["спросить нить · 04, потом · шаг 2"]) == [
        "- спросить нить · 04, потом", "- шаг 2"]
    assert seed_draft.block_next(["итог у пульта · 05"]) == ["- итог у пульта · 05"]


def test_f64_real_separators_still_cut():
    assert seed_draft.block_next(["проверить · 10 тестов · шаг 1 · шаг 2"]) == [
        "- проверить", "- 10 тестов", "- шаг 1", "- шаг 2"]
    assert seed_draft.block_next(["в 12 · 12.10 созвон"]) == ["- в 12", "- 12.10 созвон"]


# --- F69: решения -------------------------------------------------------------------

LABEL_07 = ("- 07 Tencent — правило нити (src у записи, один писатель кодом, журнал "
            "правок обязателен) (пульс 17:29)")
REC_07 = ("- 07 — Опора для tide truth и памяти нитей — три принципа из TencentDB Agent "
          "Memory. (1) У каждого факта и решения есть src. (2) Правило «пишет только "
          "один» (context-keeper) держится кодом. (3) Каждая правка дописывает строку в "
          "append-only журнал правок. (правило, в силе)")
REC_01 = ("- 01 — SOC-100 ведёт board параллельно, движок tide — у handoffs (SOC-101) "
          "(not done)")


def test_f69_label_twin_of_thread_decision_is_folded():
    got = seed_draft.fold_decisions([LABEL_07, REC_01, REC_07])
    assert got == [REC_07, REC_01]


def test_f69_no_status_mark_is_not_printed():
    got = seed_draft.fold_decisions([
        "- контрольная пара — только обычная нить по порогу "
        + seed_draft.DECISION_NO_STATUS + " (пульс 17:29)"])
    assert got == ["- контрольная пара — только обычная нить по порогу (пульс 17:29)"]
    assert "не указан" not in "\n".join(got)


def test_f69_distinct_decisions_stay():
    lines = ["- 03 — замер памяти сессии — мерка приёмки (правило, в силе)",
             "- 04 — старую сессию tide только предлагает закрыть (правило, в силе)"]
    assert seed_draft.fold_decisions(lines) == lines


def test_f69_status_from_linear():
    live = {"SOC-100": ("Done", "completed"), "SOC-101": ("In Progress", "started")}
    got = seed_draft.mark_decision_linear([REC_01, REC_07], live)
    assert got[0] == ("- 01 — SOC-100 ведёт board параллельно, движок tide — у handoffs "
                      "(SOC-101) (done — в Linear: SOC-100 — Done; SOC-101 — In Progress)")
    assert "not done" not in got[0]
    assert got[1] == REC_07  # без задачи — как есть


def test_f69_open_issue_and_no_check():
    live = {"SOC-108": ("In Review", "started")}
    got = seed_draft.mark_decision_linear(["- 02 — SOC-108 (ask-gate) — у topics (not done)"],
                                          live)
    assert got == ["- 02 — SOC-108 (ask-gate) — у topics (в Linear: SOC-108 — In Review)"]
    # сверки не было — строка как есть, не выдумываем
    assert seed_draft.mark_decision_linear([REC_01], None) == [REC_01]
    assert seed_draft.mark_decision_linear(
        [REC_01], seed_draft.LinearUnavailable("нет сети")) == [REC_01]


def test_f69_seal_takes_decision_status_from_linear(monkeypatch):
    monkeypatch.setattr(seed_draft, "linear_fetch",
                        lambda keys: {"SOC-100": ("Done", "completed"),
                                      "SOC-101": ("In Progress", "started")})
    seed_draft._LINEAR_CACHE.clear()
    text = seed_draft.seal({seed_draft.DECISIONS: [REC_01]}, context=[])
    assert "(done — в Linear: SOC-100 — Done; SOC-101 — In Progress)" in text
    assert "(not done)" not in text
    seed_draft._LINEAR_CACHE.clear()
