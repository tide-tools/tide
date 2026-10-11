"""SOC-181 п.13: у проекта с трекером заказчика (mitehq — MIT, его правила) сборщик
сида не подмешивает SOC-строки шаблона: в «задачи Linear» и «ждёт Гришу» едут только
задачи SOC самой нити, а номер, попавший в текст почтой (чужой SOC-112), — нет."""

from __future__ import annotations

from tide.launcher import seed_draft


class _Live(dict):
    takers = {}


def _seal(bodies, live, monkeypatch):
    monkeypatch.setattr(seed_draft, "linear_fetch", lambda keys: live)
    seed_draft._LINEAR_CACHE.clear()
    try:
        return seed_draft.seal(bodies, context=[])
    finally:
        seed_draft._LINEAR_CACHE.clear()


def _block(text, name):
    return text.split("## " + name, 1)[1].split("\n## ", 1)[0]


def _bodies(customer):
    return {
        seed_draft.THREAD_SELF: ["mitehq", "bidbook"],
        seed_draft.CUSTOMER_TRACKER: ["1"] if customer else [],
        seed_draft.LINEAR_SOURCES: [("цели нити", "- задача нити SOC-126"),
                                    ("прошлого сида", "- SOC-112 — x — In Review")],
        seed_draft.WHERE: ["- поручено мне: доска SOC-112 читает механически; MIT-71 в работе"],
        seed_draft.AWAITING: ["- SOC-112 на проверке (In Review) — предлагаю: посмотреть"],
    }


def _live():
    live = _Live({"SOC-112": ("In Review", "started"), "SOC-126": ("In Review", "started"),
                  "SOC-140": ("In Progress", "started")})
    live.takers = {"SOC-140": [("mitehq", "bidbook")]}
    return live


def test_customer_tracker_seed_carries_only_own_soc(monkeypatch):
    bodies = _bodies(customer=True)
    bodies[seed_draft.INFLIGHT] = ["- SOC-140 взят этой нитью"]
    text = _seal(bodies, _live(), monkeypatch)
    lin = _block(text, seed_draft.LINEAR)
    assert "SOC-126" in lin                      # названа в цели нити
    assert "SOC-140" in lin                      # «Взял: mitehq — bidbook»
    assert "SOC-112" not in lin                  # чужая: пришла почтой
    aw = _block(text, seed_draft.AWAITING)
    assert "SOC-126 на проверке" in aw
    assert not [ln for ln in aw.splitlines() if "SOC-112 на проверке" in ln]
    assert "MIT-71" in _block(text, seed_draft.WHERE)   # трекер заказчика не трогаем


def test_own_tracker_project_keeps_the_old_behaviour(monkeypatch):
    text = _seal(_bodies(customer=False), _live(), monkeypatch)
    assert "SOC-112" in _block(text, seed_draft.LINEAR)


def test_customer_tracker_is_mitehq_by_default_and_config_wins(tmp_path):
    proj = tmp_path / "mitehq"
    (proj / ".tide" / "canon").mkdir(parents=True)
    assert seed_draft.customer_tracker(proj)
    (proj / ".tide" / "canon" / "config").write_text("lang=ru\ntracker=own\n", encoding="utf-8")
    assert not seed_draft.customer_tracker(proj)
    other = tmp_path / "music"
    (other / ".tide" / "canon").mkdir(parents=True)
    assert not seed_draft.customer_tracker(other)
    (other / ".tide" / "canon" / "config").write_text("tracker=customer\n", encoding="utf-8")
    assert seed_draft.customer_tracker(other)


def test_own_issues_reads_goal_and_takers():
    live = _live()
    got = seed_draft.own_issues(["SOC-112", "SOC-126", "SOC-140"], live,
                                ["mitehq", "bidbook"], "цель: SOC-126")
    assert got == ["SOC-126", "SOC-140"]
