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
