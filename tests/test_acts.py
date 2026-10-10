"""tide.acts — акт: одна строка «что · пруф · адрес» в паспорте сессии.

Работа 66 п.1 целиком: единый писатель актов + двойная запись из вербов работы
(журнал файла пишется как прежде) — и ВИД на них: ``tide work show`` собирает
журнал из актов (см. tests/test_work.py), ``tide thread`` показывает ленту
ведущей сессии, длинный пруф уезжает файлом, а в акт идёт путь.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from tide import acts, cli, fields
from tide.arc import stream, work


@pytest.fixture
def session(tmp_project):
    """Нить с одной открытой сессией — паспорт, в который ложатся акты."""
    stream.new_thread(tmp_project, "release", goal="акты в одном месте")
    return stream.new_session(tmp_project, "release", "stroyka")


@pytest.fixture
def caller(tmp_project, session, monkeypatch):
    """Та же сессия, но ПРИПИСАННАЯ текущему чату — акт находит её сам."""
    fields.set_field(session / "arc.md", "claude-session", "sid-66")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-66")
    monkeypatch.chdir(tmp_project)
    return session


def _context(entry: Path) -> str:
    text = (entry / "arc.md").read_text(encoding="utf-8")
    return text.partition("## context")[2].split("\n## ", 1)[0]


# --- грамматика --------------------------------------------------------------

def test_act_line_is_what_proof_word_address(session):
    line = acts.write_act(
        session, "страница how-it-works написана",
        proof="200 через 40 с, коммит e987384", word="дай ссылку", ref="66.4",
        now=datetime(2026, 9, 2, 16, 17, 32))
    assert line == (
        "- 2026-09-02T16:17:32 — акт: страница how-it-works написана · "
        "пруф: 200 через 40 с, коммит e987384 · слово: «дай ссылку» · #66.4")
    assert line[2:] in _context(session)


def test_empty_fields_are_not_written(session):
    line = acts.write_act(session, "решение 37 принято")
    assert line.endswith("— акт: решение 37 принято")
    assert "пруф" not in line and "#" not in line


def test_address_is_last_and_needs_no_hash(session):
    line = acts.write_act(session, "работа взята", ref="66")
    assert line.endswith(" · #66")


def test_act_is_always_one_line(session):
    """Многострочный пруф схлопывается — акт однострочен по построению."""
    line = acts.write_act(session, "пункт 1 ✓ «сделано»",
                          proof="коротко для человека\n\n  и техника ниже", ref="66.1")
    assert "\n" not in line
    assert "пруф: коротко для человека и техника ниже" in line
    assert len(_context(session).strip().splitlines()) == 1


def test_too_long_act_refuses_and_says_where_to_put_it(session):
    with pytest.raises(ValueError, match="Положи длинное файлом в нить"):
        acts.write_act(session, "пункт 1 ✓", proof="x" * acts.ACT_MAX, ref="66.1")
    assert "акт:" not in _context(session)  # ничего не написалось


def test_fit_proof_trims_the_tail_to_the_ceiling(session):
    short = acts.fit_proof("пункт 1 ✓", "y" * 500, ref="66.1")
    line = acts.write_act(session, "пункт 1 ✓", proof=short, ref="66.1")
    assert len(line) <= acts.ACT_MAX
    assert short.endswith("…")


# --- адрес -------------------------------------------------------------------

def test_make_ref_reads_the_work_dir_name():
    assert acts.make_ref("66-akty-v-odnom-meste") == "66"
    assert acts.make_ref("66-akty-v-odnom-meste", 4) == "66.4"
    assert acts.make_ref("07-x") == "7"
    assert acts.make_ref("works") == ""


# --- чтение ------------------------------------------------------------------

def test_read_acts_is_newest_first(tmp_project, session):
    acts.write_act(session, "первое", now=datetime(2026, 9, 2, 10, 0, 0))
    acts.write_act(session, "второе", now=datetime(2026, 9, 2, 11, 0, 0))
    rows = acts.read_acts(session.parent.parent)
    assert [r.text.partition("акт: ")[2] for r in rows] == ["второе", "первое"]
    assert rows[0].at > rows[1].at
    assert rows[0].session == session


def test_read_acts_gathers_every_session_of_the_thread(tmp_project, session):
    other = stream.new_session(tmp_project, "release", "razdacha")
    acts.write_act(session, "стройка", now=datetime(2026, 9, 2, 10, 0, 0))
    acts.write_act(other, "раздача", now=datetime(2026, 9, 2, 12, 0, 0))
    rows = acts.read_acts(session.parent.parent)
    assert len(rows) == 2
    assert rows[0].session == other and rows[1].session == session


def test_read_acts_filters_by_address(session):
    acts.write_act(session, "работа взята", ref="66", now=datetime(2026, 9, 2, 9, 0, 0))
    acts.write_act(session, "пункт 4 ✓", ref="66.4", now=datetime(2026, 9, 2, 9, 30, 0))
    acts.write_act(session, "чужая работа", ref="65", now=datetime(2026, 9, 2, 9, 45, 0))
    acts.write_act(session, "без адреса", now=datetime(2026, 9, 2, 9, 50, 0))

    whole = acts.read_acts(session.parent.parent, ref="66")
    assert [r.ref for r in whole] == ["66.4", "66"]
    item = acts.read_acts(session.parent.parent, ref="#66.4")
    assert [r.ref for r in item] == ["66.4"]


def test_read_acts_of_a_thread_without_sessions_is_empty(tmp_project):
    stream.new_thread(tmp_project, "quiet", goal="ничего не делали")
    assert acts.read_acts(tmp_project / ".tide" / "arcs" / "01-@quiet") == []


# --- кто пишет ---------------------------------------------------------------

def test_caller_session_finds_the_pinned_session(tmp_project, caller):
    assert acts.caller_session(tmp_project) == caller


def test_caller_session_is_none_without_a_sid(tmp_project, session, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert acts.caller_session(tmp_project) is None


def test_caller_session_is_none_for_a_stranger_sid(tmp_project, session, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-chuzhoy")
    assert acts.caller_session(tmp_project) is None


# --- двойная запись из вербов работы -----------------------------------------

def _work(root, title="акты в одном месте"):
    cli.main(["work", "add", title])
    return work._find(root, "01")


def test_check_writes_the_act_and_nothing_else(tmp_project, caller):
    """Финал работы 66 п.1: место у жеста одно — паспорт сессии, не work.md."""
    _work(tmp_project)
    cli.main(["work", "checklist", "01", "Единый писатель актов"])
    cli.main(["work", "take", "01", "--word", "поехали"])
    cli.main(["work", "check", "01", "1", "--proof", "модуль acts.py + тесты"])

    passport = (work._find(tmp_project, "01") / "work.md").read_text(encoding="utf-8")
    assert "## журнал" not in passport
    assert "модуль acts.py" not in passport

    ctx = _context(caller)
    assert ("акт: пункт 1 ✓ «Единый писатель актов» · "
            "пруф: модуль acts.py + тесты · #1.1") in ctx


def test_the_human_word_gets_its_own_field_in_the_act(tmp_project, caller):
    _work(tmp_project)
    cli.main(["work", "take", "01", "--word", "берём, план согласован"])
    ctx = _context(caller)
    assert "слово: «берём, план согласован»" in ctx
    assert ctx.rstrip().endswith("#1")


def test_dispatch_and_close_land_as_acts_with_the_work_address(tmp_project, caller):
    _work(tmp_project)
    cli.main(["work", "take", "01", "--word", "да"])
    cli.main(["work", "dispatch", "01", "--to", "воркер стройки"])
    cli.main(["work", "close", "01", "--word", "принято"])
    ctx = _context(caller)
    assert "акт: строитель отправлен: воркер стройки · #1" in ctx
    assert "акт: закрыта по слову человека · слово: «принято» · #1" in ctx


def test_a_long_proof_never_breaks_the_verb(tmp_project, caller):
    _work(tmp_project)
    cli.main(["work", "checklist", "01", "Длинный пруф"])
    cli.main(["work", "take", "01", "--word", "да"])
    cli.main(["work", "check", "01", "1", "--proof", "подробности " * 60])

    # длинное не влезает в акт и не режется: полный пруф уезжает файлом (ниже),
    # а сам акт остаётся однострочным и по адресу
    act = [ln for ln in _context(caller).splitlines() if "пункт 1 ✓" in ln][0]
    assert len(act) <= acts.ACT_MAX and act.endswith("#1.1")
    assert "подробности подробности" in _spilled_text(caller)


# --- длинный пруф: файлом, а не обрезком --------------------------------------

def _proofs(entry: Path):
    d = entry / "workspace" / acts.PROOFS_DIRNAME
    return sorted(d.glob("*.md")) if d.is_dir() else []


def _spilled_text(entry: Path) -> str:
    """Всё, что уехало файлами пруфов, — одной простынёй."""
    return "\n".join(f.read_text(encoding="utf-8") for f in _proofs(entry))


def test_a_long_proof_goes_to_a_file_and_the_act_carries_the_path(tmp_project, caller):
    """Слово человека 02.09: одна строка, длинное — файлом. Обрезать нельзя:
    журнал стал ВИДОМ на акты, и обрезок остался бы единственной записью."""
    _work(tmp_project)
    cli.main(["work", "checklist", "01", "Длинный пруф"])
    cli.main(["work", "take", "01", "--word", "да"])
    cli.main(["work", "check", "01", "1", "--proof",
              "страница задеплоена, 200 через 40 с\n\n" + "техника " * 80])

    act = [ln for ln in _context(caller).splitlines() if "пункт 1 ✓" in ln][0]
    assert len(act) <= acts.ACT_MAX and act.endswith("· #1.1")
    # первая строка пруфа — та, что автор писал ЧЕЛОВЕКУ — осталась целой
    assert "пруф: страница задеплоена, 200 через 40 с · workspace/proofs/" in act

    files = _proofs(caller)
    assert len(files) == 1 and files[0].name.startswith("1.1-")
    assert str(files[0].relative_to(caller)) in act      # путь ведёт к файлу
    assert "техника техника" in files[0].read_text(encoding="utf-8")  # ничего не потеряно


def test_a_short_proof_writes_no_file(tmp_project, caller):
    _work(tmp_project)
    cli.main(["work", "checklist", "01", "Короткий пруф"])
    cli.main(["work", "take", "01", "--word", "да"])
    cli.main(["work", "check", "01", "1", "--proof", "коммит e987384"])
    assert _proofs(caller) == []


def test_file_proof_never_cuts_the_path(session):
    """Режется первая строка пруфа, а не путь: без пути запись никуда не ведёт."""
    got = acts.file_proof("пункт 4 ✓ «страница»", "ц" * 400,
                          "workspace/proofs/66.4-20260902-195413.md", ref="66.4")
    assert got.endswith("· workspace/proofs/66.4-20260902-195413.md")
    assert "…" in got
    assert len(acts.write_act(session, "пункт 4 ✓ «страница»",
                              proof=got, ref="66.4")) <= acts.ACT_MAX


# --- ведущая сессия нити ------------------------------------------------------

@pytest.fixture
def no_transcripts(tmp_path, monkeypatch):
    """Чатов claude на диске нет — так `head_session` падает на штамп паспорта."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))


def test_head_session_is_the_newest_accepted_one(tmp_project, session, no_transcripts):
    fields.set_field(session / "arc.md", "claude-session", "sid-a")
    later = stream.new_session(tmp_project, "release", "razdacha")
    fields.set_field(later / "arc.md", "claude-session", "sid-b")
    assert acts.head_session(session.parent.parent) == later


def test_a_session_with_a_real_chat_leads_over_a_bare_stamp(tmp_project, session,
                                                            no_transcripts, tmp_path):
    """Призрак: подъём оффера штампует sid ДО того, как сессия заговорит."""
    fields.set_field(session / "arc.md", "claude-session", "sid-a")
    ghost = stream.new_session(tmp_project, "release", "prizrak")
    fields.set_field(ghost / "arc.md", "claude-session", "sid-ghost")
    chats = tmp_path / "claude-home" / "projects" / str(tmp_project).replace(
        "/", "-").replace(".", "-")
    chats.mkdir(parents=True)
    (chats / "sid-a.jsonl").write_text("{}\n", encoding="utf-8")
    assert acts.head_session(session.parent.parent) == session


def test_a_dismissed_session_does_not_lead(tmp_project, session, no_transcripts):
    fields.set_field(session / "arc.md", "claude-session", "sid-a")
    left = stream.new_session(tmp_project, "release", "otpushchennaya")
    fields.set_field(left / "arc.md", "claude-session", "sid-b")
    fields.set_field(left / "arc.md", "dismissed", "2026-09-02T20:00")
    assert acts.head_session(session.parent.parent) == session


def test_a_thread_without_sessions_has_no_head(tmp_project):
    stream.new_thread(tmp_project, "quiet", goal="ничего не делали")
    assert acts.head_session(tmp_project / ".tide" / "arcs" / "01-@quiet") is None


# --- лента актов на экране нити ----------------------------------------------

def test_thread_screen_shows_the_acts_of_the_leading_session(tmp_project, caller,
                                                             no_transcripts):
    from tide.arc import thread_screen

    _work(tmp_project)
    cli.main(["work", "take", "01", "--word", "берём"])
    cli.main(["work", "dispatch", "01", "--to", "строитель B"])
    out = thread_screen.render(tmp_project, "release")

    block = out.partition("\nacts · ")[2].partition("\n\n")[0]
    # три жеста, а не два: «ответственная нить → …» тоже стал актом — до финала
    # работы 66 он жил ТОЛЬКО строкой файла и с погашенной записью пропал бы
    assert block.startswith("3 from {0}".format(caller.name))
    rows = [ln.strip() for ln in block.splitlines()[1:]]
    assert rows[0].endswith("акт: строитель отправлен: строитель B · #1")  # новое сверху
    assert "взята в работу" in rows[1]
    assert "ответственная нить → 01-@release" in rows[2]
    assert out.index("acts · ") < out.index("where to look")


def test_a_long_act_keeps_its_address_on_the_screen(tmp_project, caller,
                                                    no_transcripts):
    from tide.arc import thread_screen

    _work(tmp_project)
    cli.main(["work", "checklist", "01", "Пункт"])
    cli.main(["work", "take", "01", "--word", "да"])
    cli.main(["work", "check", "01", "1", "--proof", "подробность " * 20])
    row = [ln for ln in thread_screen.render(tmp_project, "release").splitlines()
           if "пункт 1 ✓" in ln][0]
    assert row.rstrip().endswith("· #1.1")     # адрес режется последним
    assert "…" in row


def test_a_thread_without_acts_says_nothing_about_them(tmp_project, session):
    from tide.arc import thread_screen

    assert "acts · " not in thread_screen.render(tmp_project, "release")


def test_no_caller_session_means_no_act_and_no_failure(tmp_project, session, monkeypatch):
    """Верб зовут из скрипта или с чужой машины — акта нет, и это не падение.

    Цена одного места названа вслух: без сессии жест не записывается НИГДЕ.
    Верб при этом делает своё дело до конца — статус, чеклист, паспорт, — и
    возвращает 0; молчит только лента.
    """
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.chdir(tmp_project)
    _work(tmp_project)
    assert cli.main(["work", "take", "01", "--word", "да"]) == 0
    assert cli.main(["work", "dispatch", "01", "--to", "воркер"]) == 0

    passport = (work._find(tmp_project, "01") / "work.md").read_text(encoding="utf-8")
    assert "status: taken" in passport                  # работа сделана
    assert "воркер" not in passport                     # а записи о жесте нет
    assert "акт:" not in _context(session)
