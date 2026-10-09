"""SOC-101 пакет Д1 — доделки сборщика сида по приёмке acceptance-03 (пара 10).

F28 доставки очередью · F29 окно входящих до ПЕРЕДАЧИ · F30 статусы из Linear ·
F31 сжатие журнала · F32 шапка · F33 неотвеченные · F34 курсор до ПЕРЕДАЧИ ·
C11 карта входа · C4 правила без «not done». Сеть не трогается.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tide.launcher import seed_draft


@pytest.fixture
def msk(monkeypatch):
    """Пульсы — местное время −03, транскрипт — UTC."""
    import time

    monkeypatch.setenv("TZ", "Etc/GMT+3")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _queued(ts: str, prompt: str) -> dict:
    return {"type": "attachment", "timestamp": ts,
            "attachment": {"type": "queued_command", "prompt": prompt,
                           "commandMode": "prompt"}}


def _user(ts: str, text: str) -> dict:
    return {"type": "user", "timestamp": ts, "message": {"content": text}}


# --- F28: доставки очередью ------------------------------------------------------

def test_queued_deliveries_are_incoming_orders(tmp_path, msk):
    t = tmp_path / "t.jsonl"
    rows = [
        _queued("2026-10-09T11:25:30Z", "<ao-session-delivery id=\"s:1\">\n[from tide-stack-12] "
                "Пакет Г СДАН: 230b37b F22 … 29cfac7 F27, pytest 2849\n</ao-session-delivery>"),
        _queued("2026-10-09T11:25:47Z", "<cross-session-message from=\"uds:x\" "
                "from-name=\"tide-stack-20-9e\">приёмка идёт</cross-session-message>"),
        _queued("2026-10-09T11:25:50Z", "<task-notification><task-id>a1</task-id>"
                "</task-notification>"),
        {"type": "queue-operation", "timestamp": "2026-10-09T11:25:30Z",
         "content": "<ao-session-delivery>[from tide-stack-12] Пакет Г СДАН</ao-session-delivery>"},
        # та же доставка ещё и репликой — один раз
        _user("2026-10-09T11:25:31Z", "<ao-session-delivery id=\"s:1\">\n[from tide-stack-12] "
              "Пакет Г СДАН: 230b37b F22 … 29cfac7 F27, pytest 2849\n</ao-session-delivery>"),
        _queued("2026-10-09T11:25:55Z", "<ao-session-delivery>[from m-1] Your claude.ai usage "
                "limit has reset.</ao-session-delivery>"),
    ]
    t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    out = seed_draft.incoming_orders(t, datetime(2026, 10, 9, 8, 0))
    assert out == [
        "- поручено мне — от tide-stack-20-9e в 08:25: приёмка идёт",
        "- поручено мне — от tide-stack-12 в 08:25: Пакет Г СДАН: 230b37b F22 … 29cfac7 F27, "
        "pytest 2849",
    ]


# --- F29: окно от пульса перед ПЕРЕДАЧЕЙ -----------------------------------------

CTX = [
    "- 2026-10-09T08:10:00 — сдала промер",
    "- 2026-10-09T08:22:00 — остаток F23/F16, F27 у handoffs · 03",
    "- 2026-10-09T08:26:26 — ПЕРЕДАЧА. В полёте: коммит truth у handoffs · 03",
]


def test_window_starts_at_the_pulse_before_the_handover():
    assert seed_draft.incoming_since(CTX) == datetime(2026, 10, 9, 8, 22)
    # ПЕРЕДАЧА не последняя — окно всё равно от пульса перед ней
    assert seed_draft.incoming_since(CTX + ["- 2026-10-09T08:27:00 — шлю номер"]) == \
        datetime(2026, 10, 9, 8, 22)
    # ПЕРЕДАЧИ нет — от предпоследнего пульса
    assert seed_draft.incoming_since(CTX[:2]) == datetime(2026, 10, 9, 8, 10)
    assert seed_draft.incoming_since(CTX[:1]) is None


def test_report_a_minute_before_handover_reaches_the_seed(tmp_path, msk, monkeypatch):
    sid = "aaaa-1"
    d = tmp_path / "projects" / "-x"
    d.mkdir(parents=True)
    (d / "{0}.jsonl".format(sid)).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
        _queued("2026-10-09T11:21:00Z", "<ao-session-delivery>[from a] старое</ao-session-delivery>"),
        _queued("2026-10-09T11:25:30Z", "<ao-session-delivery>[from tide-stack-12] Пакет Г "
                "СДАН: 230b37b … 29cfac7</ao-session-delivery>"),
    ]), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    sess = tmp_path / "s"
    sess.mkdir()
    (sess / "arc.md").write_text("# s\n\nclaude-session: {0}\n".format(sid), encoding="utf-8")
    out = seed_draft.session_incoming(sess, CTX)
    # окно — с приёма сессии (F29 жизнь): «старое» 08:21 не закрыто итогом и не в пульсе
    assert out == ["- поручено мне — от tide-stack-12 в 08:25: Пакет Г СДАН: 230b37b … 29cfac7",
                   "- поручено мне — от a в 08:21: старое"]


# --- F34: пульсы курсора старше ПЕРЕДАЧИ помечены ---------------------------------

def test_cursor_pulses_older_than_handover_are_marked():
    lines = seed_draft.block_cursor(["передаю нить"], CTX + ["- 2026-10-09T08:27:00 — шлю номер"])
    assert lines[0] == "передаю нить"
    assert lines[1] == "- 2026-10-09T08:27:00 — шлю номер"
    assert lines[2].startswith("- 2026-10-09T08:26:26 — ПЕРЕДАЧА.")
    assert not lines[2].endswith(seed_draft.BEFORE_PASS)
    assert lines[3] == "- 2026-10-09T08:22:00 — остаток F23/F16, F27 у handoffs · 03 (до ПЕРЕДАЧИ)"
    assert lines[4].endswith("сдала промер (до ПЕРЕДАЧИ)")
    # без ПЕРЕДАЧИ — без пометок
    assert not any(seed_draft.BEFORE_PASS in ln for ln in seed_draft.block_cursor([], CTX[:2]))


# --- C11: карта входа из метки «Карта:» ---------------------------------------------

PASS_WITH_MAP = ("- 2026-10-09T08:26:26 — ПЕРЕДАЧА. Слои освежены. Карта: /abs/plan.md — "
                 "главное и кому сдавать; /abs/topic.md — что ждёт Гриша; /abs/arc.md — "
                 "курсор и пульсы. Опыт: не резать по «;»")


def test_map_label_becomes_entry_map_items():
    labels = seed_draft.labeled_pulses([PASS_WITH_MAP])
    assert labels["entry_map"] == [
        "- /abs/plan.md — главное и кому сдавать (пульс 08:26)",
        "- /abs/topic.md — что ждёт Гриша (пульс 08:26)",
        "- /abs/arc.md — курсор и пульсы (пульс 08:26)",
    ]
    assert labels["experience"] == ["- не резать по «;» (пульс 08:26)"]
    got = seed_draft.block_entry_map(["- старая карта"], labels["entry_map"], ["- собранная"])
    assert got == labels["entry_map"]          # карта ПЕРЕДАЧИ — без каркаса и наследства
    assert not seed_draft.pass_without_map([PASS_WITH_MAP])


def test_pass_without_map_warns_and_collects(tmp_project, capsys):
    import io

    from tide.arc import stream

    ctx = ["- 2026-10-09T08:26:26 — ПЕРЕДАЧА. В полёте: ничего"]
    assert seed_draft.pass_without_map(ctx)
    assert not seed_draft.pass_without_map(["- 2026-10-09T08:20:00 — шаг"])  # нет ПЕРЕДАЧИ
    buf = io.StringIO()
    assert seed_draft.warn_missing_map(ctx, stream=buf)
    assert "нет метки «Карта:»" in buf.getvalue()
    thread = stream.new_thread(tmp_project, "kart", goal="нить с картой")
    (thread / "plan.md").write_text("# plan\n", encoding="utf-8")
    sess = stream.new_session(tmp_project, "kart", "s1", goal="шаг")
    got = seed_draft.collected_map(tmp_project, sess, thread)
    assert len(got) == 3 and all(seed_draft.COLLECTED in ln for ln in got)
    assert got[0].startswith("- {0} — «главное»".format((thread / "plan.md").resolve()))
    assert got[-1].startswith("- {0} — паспорт прошлой сессии".format(
        (sess / "arc.md").resolve()))
    # наследство: собранная карта прошлого сида не переходит дальше
    assert seed_draft.block_entry_map(got, (), ["- новая " + seed_draft.COLLECTED])[0] == \
        "- новая " + seed_draft.COLLECTED


# --- F30: статусы задач Linear — из Linear ------------------------------------------

@pytest.fixture
def linear(monkeypatch):
    """Подменённый клиент Linear: сеть не трогаем, вызовы считаем."""
    seed_draft._LINEAR_CACHE.clear()
    calls = []
    states = {"SOC-39": ("Done", "completed"), "SOC-100": ("Done", "completed"),
              "SOC-101": ("In Progress", "started"), "SOC-117": ("Backlog", "backlog"),
              "SOC-90": ("Canceled", "canceled")}

    def fetch(keys):
        calls.append(list(keys))
        return {k: v for k, v in states.items() if k in keys}

    monkeypatch.setattr(seed_draft, "linear_fetch", fetch)
    monkeypatch.setenv(seed_draft.LINEAR_URL_ENV, "https://l.example/{key}")
    monkeypatch.delenv(seed_draft.LINEAR_OFF_ENV, raising=False)
    yield calls
    seed_draft._LINEAR_CACHE.clear()


def test_statuses_come_from_linear_and_closed_go_down(linear):
    text = "SOC-100 в In Review; SOC-39 — In Review; SOC-101, SOC-117, SOC-90, SOC-5"
    live = seed_draft.linear_states(seed_draft.linear_keys(text))
    assert seed_draft.block_links(text, live=live) == [
        "- (статусы — из Linear на момент сборки)",
        "- SOC-5 — https://l.example/SOC-5 — в Linear не найдена",
        "- SOC-101 — https://l.example/SOC-101 — In Progress",
        "- SOC-117 — https://l.example/SOC-117 — Backlog",
        "- закрыты в Linear — не открытые задачи нити:",
        "  - SOC-39 — https://l.example/SOC-39 — закрыта (Done)",
        "  - SOC-90 — https://l.example/SOC-90 — закрыта (Canceled)",
        "  - SOC-100 — https://l.example/SOC-100 — закрыта (Done)",
    ]
    # кэш: повторная сборка с тем же/меньшим набором — без нового запроса
    seed_draft.linear_states(["SOC-100", "SOC-39"])
    assert len(linear) == 1


def test_linear_down_falls_back_to_text_with_a_mark(linear, monkeypatch):
    def down(keys):
        raise seed_draft.LinearUnavailable("Linear не ответил (URLError)")

    monkeypatch.setattr(seed_draft, "linear_fetch", down)
    text = "SOC-100 в In Review, SOC-101"
    live = seed_draft.linear_states(seed_draft.linear_keys(text))
    assert isinstance(live, seed_draft.LinearUnavailable)
    assert seed_draft.block_links(text, live=live) == [
        "- (статус не сверен с Linear: Linear не ответил (URLError); рядом — из текста пульсов)",
        "- SOC-100 — https://l.example/SOC-100 — In Review (из пульса, не сверен)",
        "- SOC-101 — https://l.example/SOC-101",
    ]
    # любой сбой клиента — тоже откат, не падение
    seed_draft._LINEAR_CACHE.clear()
    monkeypatch.setattr(seed_draft, "linear_fetch", lambda keys: 1 / 0)
    assert isinstance(seed_draft.linear_states(["SOC-1"]), seed_draft.LinearUnavailable)


def test_no_network_under_pytest_and_offline_switch(monkeypatch):
    seed_draft._LINEAR_CACHE.clear()
    assert seed_draft.linear_states(["SOC-1"]) is None  # клиент не подменён — сети нет
    monkeypatch.setenv(seed_draft.LINEAR_OFF_ENV, "1")
    monkeypatch.setattr(seed_draft, "linear_fetch", lambda keys: {"SOC-1": ("Done", "completed")})
    assert isinstance(seed_draft.linear_states(["SOC-1"]), seed_draft.LinearUnavailable)


def test_fetch_builds_one_query_per_team(monkeypatch):
    import io
    import urllib.request

    sent = []

    def urlopen(req, timeout):
        sent.append((json.loads(req.data), req.headers, timeout))
        return io.BytesIO(json.dumps({"data": {"issues": {"nodes": [
            {"identifier": "SOC-7", "state": {"name": "Todo", "type": "unstarted"}}]}}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(seed_draft, "_linear_key", lambda: "k")
    assert seed_draft.linear_fetch(["SOC-7", "SOC-8"]) == {"SOC-7": ("Todo", "unstarted")}
    assert len(sent) == 1 and sent[0][0]["variables"] == {"team": "SOC", "nums": [7.0, 8.0]}
    assert sent[0][2] <= 5


# --- F31: сжатие журнала не рвёт имена и правила ------------------------------------

@pytest.mark.parametrize("line, want", [
    ("- 2026-10-09T07:55:00 — context-keeper · 03 сдала мелочи truth. Дальше коммит",
     "- 07:55 — context-keeper · 03 сдала мелочи truth."),
    ("- 2026-10-09T07:35:00 — менеджер теперь management-14; --deliverable 16–20 знаков",
     "- 07:35 — менеджер теперь management-14; --deliverable 16–20 знаков"),
    ("- 2026-10-09T06:28:00 — board · 03: мобильная v2 · потом макет",
     "- 06:28 — board · 03: мобильная v2"),
    ("- 2026-10-09T06:30:00 — сдано в tide@1.0.51 и v2.2. Потом ещё",
     "- 06:30 — сдано в tide@1.0.51 и v2.2."),
])
def test_gist_keeps_session_names_and_rules(line, want):
    assert seed_draft.pulse_gist(line, limit=200) == want


# --- F32: шапка сида — кто ты и кому отчитываешься ----------------------------------

def test_seed_header_says_who_you_are_and_live_manager(tmp_project, monkeypatch):
    from tide import fields, whois
    from tide.arc import stream
    from tide.launcher import handoff

    monkeypatch.setattr(whois, "manager_address", lambda default="": "management-14")
    thread = stream.new_thread(
        tmp_project, "main", goal="главная нить tide-stack: ведёт работу проекта и управляет "
        "нитями handoffs и board, отчитывается менеджеру ao-control")
    old = stream.new_session(tmp_project, "main", "main-3", goal="шаг")
    new = stream.new_session(tmp_project, "main", "main-4", goal="шаг")
    fields.set_field(new / "arc.md", "from", "main-3")
    head = handoff._with_throughline(thread, new, "# сид").split("\n---\n")[0].splitlines()
    assert head[1] == ("идёт к: главная нить tide-stack: ведёт работу проекта и управляет "
                       "нитями handoffs и board")
    assert head[2] == "ты — main · {0}, продолжаешь после main · {1}".format(
        new.name.split("-")[0], old.name.split("-")[0])
    assert head[3] == "отчитываешься менеджеру: management-14 (tide whois manager на момент сборки)"
    assert "ao-control" not in "\n".join(head) and "продолжаешь сессию" not in "\n".join(head)


# --- F33: журнал есть, реплик Гриши нет ---------------------------------------------

def test_unanswered_says_journal_exists_but_no_words(tmp_project, monkeypatch):
    from tide.arc import dialog, stream

    monkeypatch.delenv(dialog.LOG_ENV, raising=False)
    thread = stream.new_thread(tmp_project, "dlg", goal="нить")
    assert seed_draft.block_unanswered(thread) == []          # журнала нет — пусто
    dialog.dialog_path(thread).write_text(
        "# dialog\n\n- 2026-10-09T05:51:19 · Гриша · ao · Your claude.ai usage limit has "
        "reset. Continue the task you were working on when the limit was reached.\n",
        encoding="utf-8")
    assert seed_draft.block_unanswered(thread) == [
        "— реплик Гриши нет (журнал есть; уведомлений харнесса отброшено: 1)"]
    with open(dialog.dialog_path(thread), "a", encoding="utf-8") as fh:
        fh.write("- 2026-10-09T06:00:00 · Гриша · ao · смерджи PR\n")
    assert seed_draft.block_unanswered(thread)[-1].endswith("смерджи PR")


# --- C4: у правил нет «not done» ----------------------------------------------------

def test_rule_decisions_are_not_marked_not_done():
    recs = [{"num": "03", "what": "топик = родитель", "status": "accepted", "kind": "rule"},
            {"num": "01", "what": "раскатать контракт", "status": "accepted",
             "kind": "commitment"}]
    assert seed_draft.block_decisions(recs) == [
        "- 03 — топик = родитель (правило, в силе)",
        "- 01 — раскатать контракт (not done)",
    ]
