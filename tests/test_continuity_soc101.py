"""SOC-101 — непрерывность хендоффа: находки F1–F9 continuity-audit + SOC-72.

Каждая новая сессия должна получать три слоя — ожидание Гриши, где мы, опыт — и
ничего из накопленного на диске не должно тонуть молча по дороге в сид.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tide import doctor, fields, offload, registry
from tide.arc import candidate, decision, dialog, stream, templates
from tide.hooks import install
from tide.launcher import autohandoff as ah, seed_draft


@pytest.fixture
def scene(tmp_project, monkeypatch):
    """Нить с целью и сессия в ней, журнал пульта не задан."""
    monkeypatch.delenv(dialog.LOG_ENV, raising=False)
    thread = stream.new_thread(tmp_project, "shov", goal="шов хендоффа без потерь")
    sess = stream.new_session(tmp_project, "shov", "s1", goal="довести сид")
    return {"root": tmp_project, "thread": thread, "sess": sess}


def _passport(entry: Path) -> Path:
    return stream.passport_path(entry)


def _set_where(thread: Path, body: str) -> None:
    pp = _passport(thread)
    text = pp.read_text(encoding="utf-8")
    head, _, _ = text.partition("## where we are\n")
    pp.write_text(head + "## where we are\n" + body, encoding="utf-8")


def _seed_in(sess: Path, text: str) -> None:
    p = sess / "input" / seed_draft.INPUT_SEED_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# --- F1: шаблон нити ------------------------------------------------------------

def test_f1_thread_h1_from_goal_and_three_layer_where(scene):
    text = _passport(scene["thread"]).read_text(encoding="utf-8")
    assert text.startswith("# shov-thread — шов хендоффа без потерь\n")
    assert "<тред:" not in text
    where = text.partition("## where we are\n")[2]
    assert [ln[4:] for ln in where.splitlines() if ln.startswith("### ")] == \
        ["ожидание Гриши", "где мы", "опыт"]
    assert "сделано · идёт · ждёт Гришу" in where


def test_f1_thread_without_goal_keeps_the_visible_placeholder():
    assert "<тред:" in templates.thread_goal_md("x")


# --- F2: где мы нити + итог сессии дословно ------------------------------------

def test_f2_auto_seed_carries_where_we_are_and_summary_verbatim(scene):
    _set_where(scene["thread"], "### ожидание Гриши\nчтобы хендофф не ломался (09.10)\n\n"
               "### где мы\n- сделано: журнал слов\n- ждёт Гришу: подпись F1\n\n"
               "### опыт\n<грабли>\n")
    pp = scene["sess"] / "arc.md"
    text = pp.read_text(encoding="utf-8")
    start = text.index("## summary\n") + len("## summary\n")
    end = text.index("## cursor")
    pp.write_text(text[:start] + "Сделал фильтр; не сделал посев.\n\n" + text[end:],
                  encoding="utf-8")
    seed = ah.build_auto_seed(scene["sess"])
    where = seed.partition("## где мы — нить\n")[2].partition("\n## ")[0]
    assert "### ожидание Гриши\nчтобы хендофф не ломался (09.10)" in where
    assert "- ждёт Гришу: подпись F1" in where
    assert "### опыт" not in where  # только заглушка — слой не печатаем
    assert "Сделал фильтр; не сделал посев." in seed.partition("## итог прошлой сессии")[2]


def test_f2_fresh_summary_placeholder_is_not_content(scene):
    seed = ah.build_auto_seed(scene["sess"])
    body = seed.partition("## итог прошлой сессии\n\n")[2].partition("\n\n")[0]
    assert body == seed_draft.EMPTY


# --- F3/F4: опыт и карта входа наследуются ---------------------------------------

def test_f3_f4_experience_and_entry_map_inherited_from_the_input_seed(scene):
    _seed_in(scene["sess"], "# сид\n\n## опыт\n\n- грабли: ao send в exited-сессию молчит\n"
             "- —  <!-- курируй -->\n\n## карта входа\n\n- 1. plan.md нити — шаги\n")
    candidate.new_candidate(scene["root"], "dialog-noise", from_arc=scene["sess"].name,
                            body="Шум пульта в посеве. Детали.")
    seed = ah.build_auto_seed(scene["sess"])
    exp = seed.partition("## опыт\n\n")[2].partition("\n## ")[0]
    assert "- грабли: ao send в exited-сессию молчит" in exp
    assert "- кандидат 01 — Шум пульта в посеве." in exp  # суть, не голый номер (SOC-74)
    assert "<!--" not in exp
    entry = seed.partition("## карта входа\n\n")[2].partition("\n## ")[0]
    assert entry.splitlines()[0] == "- 1. plan.md нити — шаги"
    assert "автохендофф: читай паспорт прошлой сессии" in entry


# --- F5: журнал не тонет, но ≤ 30% тела ------------------------------------------

def _raw_share(seed: str) -> float:
    """Доля сырого журнала в сиде: строки пульса в курсоре + секция журнала."""
    cur = seed.partition("## курсор\n")[2].partition("\n## ")[0]
    journal = seed.partition("## журнал сессии — старше курсора\n")[2].partition("\n## ")[0]
    raw = sum(len(ln) + 1 for ln in cur.splitlines() if ln.startswith("- 20")) + len(journal)
    return raw / len(seed)


def test_f5_older_pulse_survives_within_the_share(scene):
    # замер до правки (main · 01 → 02): из 36 пульс-строк дошли 5, журнал занял 68% сида
    _set_where(scene["thread"], "### где мы\n" + "".join(
        "- сделано: пункт {0} доведён до приёмки и сдан\n".format(i) for i in range(30)))
    for i in range(36):
        offload.write_pulse(scene["sess"], note="шаг {0:02d}: ".format(i) + "детали " * 30)
    seed = ah.build_auto_seed(scene["sess"])
    assert _raw_share(seed) <= 0.30
    journal = seed.partition("## журнал сессии — старше курсора\n\n")[2].partition("\n## ")[0]
    assert "шаг 30" in journal                       # самое свежее из старого — дошло сжатым
    reached = sum(1 for i in range(36) if "шаг {0:02d}".format(i) in seed)
    assert reached > 5
    assert "не вошло:" in journal and "arc.md (## context)" in journal  # остальное не молча


def test_f5_journal_keeps_everything_when_it_fits():
    ctx = ["- {0}".format(i) for i in range(8)]
    out = seed_draft.block_journal(ctx, budget=10_000)
    assert out == ["- 2", "- 1", "- 0"]   # старше хвоста из 5, свежим сверху, без указателя


# --- F6: хвост диалога — только своей нити ----------------------------------------

def test_f6_pult_log_is_filtered_by_thread(scene, tmp_control_home, monkeypatch):
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    registry.record(tmp_control_home, "sid-1", "proj-3", str(scene["sess"]))
    log = scene["root"] / "pult.jsonl"
    rows = [
        {"ts": "2026-10-09T05:00:00+00:00", "dir": "in", "kind": "tg", "ao_session": "proj-3",
         "text": "своей нити по AO-сессии"},
        {"ts": "2026-10-09T05:01:00+00:00", "dir": "in", "kind": "linear",
         "route": "proj/shov", "text": "своей нити по route"},
        {"ts": "2026-10-09T05:02:00+00:00", "dir": "out", "kind": "notice",
         "ao_session": "music-4", "text": "пинг чужой сессии"},
        {"ts": "2026-10-09T05:03:00+00:00", "dir": "out", "kind": "alert", "text": "без адреса"},
    ]
    log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                   encoding="utf-8")
    monkeypatch.setenv(dialog.LOG_ENV, str(log))
    bodies = [ln.split(" · ", 3)[3] for ln in dialog.tail(scene["thread"])]
    assert bodies == ["своей нити по AO-сессии", "своей нити по route"]


# --- F7: статусы решений и нить-предшественница -----------------------------------

def test_f7_done_by_proof_or_closed_work_and_predecessor_decisions(scene, tmp_project):
    decision.add_decision(tmp_project, "журнал у пульта", thread_ref="shov")
    decision.add_decision(tmp_project, "лестница 200/300/500", thread_ref="shov")
    decision.add_decision(tmp_project, "посев по нити", thread_ref="shov")
    log = decision.decisions_file(scene["thread"])
    text = log.read_text(encoding="utf-8")
    parts = text.split("\n## ")
    parts[1] = parts[1].replace("proof: —", "proof: живой прогон 09.10", 1)
    parts[2] = parts[2].replace("work: —", "work: 66", 1)
    log.write_text("\n## ".join(parts), encoding="utf-8")
    wd = tmp_project / ".tide" / "arcs" / "works" / "__66-zhurnal__"
    wd.mkdir(parents=True)
    (wd / "work.md").write_text("# Работа 66\n\nkind: work\nstatus: done\n", encoding="utf-8")
    # нить-предшественница со своим решением
    old = stream.new_thread(tmp_project, "ao-and-order", goal="порядок в AO")
    decision.add_decision(tmp_project, "старая нить решила X", thread_ref="ao-and-order")
    fields.set_field(_passport(scene["thread"]), "from", old.name)
    out = "\n".join(seed_draft.draft_bodies(tmp_project, scene["sess"])["решения"])
    assert "журнал у пульта (done по пруфу)" in out
    assert "лестница 200/300/500 (done: работа 66 закрыта)" in out
    assert "посев по нити (not done)" in out
    assert "из нити-предшественницы {0}".format(old.name) in out
    assert "старая нить решила X (not done)" in out


def test_f7_predecessor_from_the_first_sessions_from(scene, tmp_project):
    old = stream.new_thread(tmp_project, "ao-and-order", goal="порядок в AO")
    stream.new_session(tmp_project, "ao-and-order", "ao-and-order-3")
    nt = stream.new_thread(tmp_project, "main", goal="главная")
    first = stream.new_session(tmp_project, "main", "main", from_ref="ao-and-order-3")
    assert seed_draft.predecessor_thread(tmp_project, nt) == old
    assert seed_draft.predecessor_thread(tmp_project, scene["thread"]) is None
    assert first.exists()


# --- F8: «дальше» не рвёт имена сессий --------------------------------------------

def test_f8_next_keeps_session_names_whole():
    out = seed_draft.block_next([
        "принять у ao-and-order · 04 · спросить tide-stack — handoffs · 01 · tide-stack-8"
        " · закрыть · 3 шага"])
    assert out == ["- принять у ao-and-order · 04",
                   "- спросить tide-stack — handoffs · 01 · tide-stack-8",
                   "- закрыть", "- 3 шага"]


# --- F9: tide doctor видит AO-worktree без хуков -----------------------------------

def test_f9_doctor_lists_ao_worktrees_without_tide_hooks(tmp_path):
    root = tmp_path / "worktrees"
    good = root / "music" / "music-8"
    bad = root / "music" / "music-4"
    plain = root / "other" / "other-1"   # не tide-проект — не судим
    for wt in (good, bad, plain):
        (wt / ".claude").mkdir(parents=True)
    for wt in (good, bad):
        (wt / ".tide").mkdir()
    install.install_hooks(good)
    (bad / ".claude" / "settings.json").write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": install.SESSION_START_CMD}]}]
    }}))
    res = doctor.check_ao_worktree_hooks(root)
    assert res.status == doctor.STATUS_WARN
    assert "music/music-4 (нет: handoff-confirm, context-watch)" in res.detail
    assert "music-8" not in res.detail and "other" not in res.detail
    install.install_hooks(bad)
    assert doctor.check_ao_worktree_hooks(root).status == doctor.STATUS_OK


# --- SOC-72: оба номера в имени ---------------------------------------------------

def test_soc72_title_carries_the_ao_id_once():
    t = "tide-stack — handoffs · 01"
    assert stream.with_ao_id(t, "tide-stack-8") == "tide-stack — handoffs · 01 · tide-stack-8"
    assert stream.with_ao_id(t + " · tide-stack-8", "tide-stack-8") == t + " · tide-stack-8"
    assert stream.with_ao_id(t + " · tide-stack-6", "tide-stack-8") == t + " · tide-stack-8"
    assert stream.with_ao_id(t, "") == t


def test_soc72_session_start_stamps_passport_and_renames_ao(scene, monkeypatch):
    from tide.hooks import session_start

    sid = "aaaaaaaa-0000-4000-8000-000000000001"
    pp = scene["sess"] / "arc.md"
    fields.set_field(pp, "claude-session", sid)
    fields.set_field(pp, "title", "proj — shov · 01")
    monkeypatch.setenv("AO_SESSION_ID", "proj-5")

    class _Ao:
        renamed = None

        def session(self, h):
            return {"displayName": "proj — shov · 01"}

        def rename(self, h, name):
            _Ao.renamed = (h, name)
            return True

    got = session_start._ao_name_by_title(scene["root"], sid, adapter=_Ao())
    assert got == "proj — shov · 01 · proj-5"
    assert fields.read_field(pp, "title") == "proj — shov · 01 · proj-5"
    assert _Ao.renamed == ("proj-5", "proj — shov · 01 · proj-5")


# --- приёмка: SOC-N со ссылками ---------------------------------------------------

def test_linear_issues_in_the_seed_get_links_once(scene, monkeypatch):
    monkeypatch.delenv(seed_draft.LINEAR_URL_ENV, raising=False)
    offload.write_pulse(scene["sess"], note="SOC-62 сдан; жду SOC-101 и SOC-62 ещё раз",
                        cursor="приёмка SOC-101")
    seed = ah.build_auto_seed(scene["sess"])
    links = seed.partition("## задачи Linear\n\n")[2].splitlines()
    assert links == ["- SOC-62 — https://linear.app/socaseinpoint/issue/SOC-62",
                     "- SOC-101 — https://linear.app/socaseinpoint/issue/SOC-101"]
    monkeypatch.setenv(seed_draft.LINEAR_URL_ENV, "https://l.example/i/{key}")
    assert seed_draft.block_links("см. SOC-7, ABC-1") == ["- SOC-7 — https://l.example/i/SOC-7"]
