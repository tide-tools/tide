"""по-ходовая выгрузка (cand 40) — tide offload + Stop-хук offload-nudge."""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

import pytest

from tide import cli, fields, offload
from tide.arc import stream


@pytest.fixture
def session(tmp_project):
    stream.new_thread(tmp_project, "hygiene", goal="keep the seam clean")
    return stream.new_session(tmp_project, "hygiene", "otliv")


# --- the offload write -------------------------------------------------------

def test_offload_appends_context_and_stamps(tmp_project, session):
    p1 = offload.offload(tmp_project, "otliv", note="выбрали светофор в doctor")
    p2 = offload.offload(tmp_project, "otliv", note="пороги: мягкие дефолты")
    assert p1 == p2 == session / "arc.md"
    text = p2.read_text(encoding="utf-8")
    ctx = text.partition("## context")[2]
    assert "выбрали светофор" in ctx and "пороги: мягкие" in ctx
    assert ctx.index("выбрали") < ctx.index("пороги")      # entries accrue in order
    assert "<session memory" not in text                    # placeholder dropped
    assert (fields.read_field(p2, "offloaded-at") or "").startswith("20")


def test_offload_cursor_replaces_section(tmp_project, session):
    offload.offload(tmp_project, "otliv", cursor="стою на подшаге 3, дальше пороги")
    text = (session / "arc.md").read_text(encoding="utf-8")
    body = text.partition("## cursor — resume here")[2].partition("## ")[0]
    assert "подшаге 3" in body
    assert "<where this session left off" not in body


def test_offload_requires_something_to_write(tmp_project, session):
    with pytest.raises(offload.OffloadError, match="nothing to write"):
        offload.offload(tmp_project, "otliv")


def test_offload_unknown_session_lists_open(tmp_project, session):
    with pytest.raises(offload.OffloadError, match="no open session.*otliv"):
        offload.offload(tmp_project, "ghost", note="x")


# --- ambiguous slug across threads must RAISE, not corrupt a stranger (cand 85) ---

def _two_threads_same_session_slug(tmp_project):
    stream.new_thread(tmp_project, "alpha", goal="a-goal")
    a = stream.new_session(tmp_project, "alpha", "work")
    stream.new_thread(tmp_project, "beta", goal="b-goal")
    b = stream.new_session(tmp_project, "beta", "work")
    return a, b  # both dirs are '01-work', in different threads


def test_offload_ambiguous_slug_raises_with_thread_options(tmp_project):
    a, b = _two_threads_same_session_slug(tmp_project)
    with pytest.raises(offload.OffloadError, match="ambiguous"):
        offload.offload(tmp_project, "work", note="must not silently land anywhere")
    # neither passport was touched — no silent corruption
    assert "must not silently land" not in (a / "arc.md").read_text(encoding="utf-8")
    assert "must not silently land" not in (b / "arc.md").read_text(encoding="utf-8")


def test_offload_thread_qualified_resolves_the_right_session(tmp_project):
    a, b = _two_threads_same_session_slug(tmp_project)
    offload.offload(tmp_project, "beta/work", note="lands in beta only")
    assert "lands in beta only" in (b / "arc.md").read_text(encoding="utf-8")
    assert "lands in beta only" not in (a / "arc.md").read_text(encoding="utf-8")


def test_offload_single_slug_still_resolves_plainly(tmp_project, session):
    # a slug unique across all threads keeps working without qualification
    offload.offload(tmp_project, "otliv", note="plain")
    assert "plain" in (session / "arc.md").read_text(encoding="utf-8")


# --- words != disk guard: pulse says closed while the thread is open (cand 80) ---

def test_closure_word_warning_when_thread_open(tmp_project, session):
    warn = offload._closure_word_warning(session / "arc.md", "нить закрыта — всё влито в main")
    assert warn and "ОТКРЫТА" in warn and "arc close" in warn


def test_closure_word_warning_silent_without_marker(tmp_project, session):
    assert offload._closure_word_warning(session / "arc.md", "подчистил мёртвый код") is None


# --- nudge fires on a BLIND session: work in a nested repo, arc workspace idle (cand 87) ---

def test_nudge_fires_on_transcript_activity_without_workspace(tmp_project, session):
    fields.set_field(session / "arc.md", "claude-session", "sid-live")
    passport = session / "arc.md"
    stale = time.time() - offload.NUDGE_WINDOW_SECONDS - 60
    os.utime(passport, (stale, stale))                       # passport old, workspace empty
    reason = offload.nudge_reason(tmp_project, "sid-live",
                                  now=time.time(), activity_m=time.time())
    assert reason and "доска слепа" in reason                # agent-active signal caught it


def test_nudge_silent_without_any_work_signal(tmp_project, session):
    fields.set_field(session / "arc.md", "claude-session", "sid-live")
    # no workspace movement, no transcript activity → nothing owed
    assert offload.nudge_reason(tmp_project, "sid-live", activity_m=0.0) is None


def test_cli_offload_roundtrip(tmp_project, session, monkeypatch, capsys):
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["offload", "otliv", "--cursor", "тут", "решение", "принято"])
    assert rc == 0
    assert "offloaded" in capsys.readouterr().out
    text = (session / "arc.md").read_text(encoding="utf-8")
    assert "решение принято" in text


# --- the Stop-hook nudge -----------------------------------------------------

def _pin(session_dir: Path, claude_id: str) -> None:
    fields.set_field(session_dir / "arc.md", "claude-session", claude_id)


def _age(path: Path, seconds: int) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_nudge_fires_when_workspace_moved_and_passport_stale(tmp_project, session):
    _pin(session, "sess-1")
    _age(session / "arc.md", offload.NUDGE_WINDOW_SECONDS + 60)
    (session / "workspace" / "work.md").write_text("progress\n", encoding="utf-8")
    reason = offload.nudge_reason(tmp_project, "sess-1")
    # thread-qualified so the suggested command doesn't hit the ambiguity guard (cand 85)
    assert reason and "tide offload hygiene/otliv" in reason


def test_nudge_silent_when_passport_fresh(tmp_project, session):
    _pin(session, "sess-1")
    (session / "workspace" / "work.md").write_text("progress\n", encoding="utf-8")
    # passport touched just now (by _pin) → no nag mid-flow
    assert offload.nudge_reason(tmp_project, "sess-1") is None


def test_nudge_silent_when_workspace_untouched(tmp_project, session):
    _pin(session, "sess-1")
    _age(session / "arc.md", offload.NUDGE_WINDOW_SECONDS + 60)
    assert offload.nudge_reason(tmp_project, "sess-1") is None


def test_nudge_silent_for_unknown_session(tmp_project, session):
    assert offload.nudge_reason(tmp_project, "stranger") is None


def test_offload_clears_the_nudge(tmp_project, session):
    _pin(session, "sess-1")
    _age(session / "arc.md", offload.NUDGE_WINDOW_SECONDS + 60)
    (session / "workspace" / "work.md").write_text("progress\n", encoding="utf-8")
    assert offload.nudge_reason(tmp_project, "sess-1")
    offload.offload(tmp_project, "otliv", note="выгрузился")
    assert offload.nudge_reason(tmp_project, "sess-1") is None  # долг погашен


# --- start-gate add-on: nudge also flags a blind thread goal (cand 81/87) ----

def test_nudge_appends_set_goal_when_thread_goal_is_blind(tmp_project):
    # thread born with a slug-goal ('blind' == its own slug) → board shows no purpose
    stream.new_thread(tmp_project, "blind", goal="blind")
    sess = stream.new_session(tmp_project, "blind", "work")
    _pin(sess, "sid-b")
    _age(sess / "arc.md", offload.NUDGE_WINDOW_SECONDS + 60)
    (sess / "workspace" / "w.md").write_text("progress\n", encoding="utf-8")
    reason = offload.nudge_reason(tmp_project, "sid-b")
    assert reason and "set-goal blind" in reason and "слепая цель" in reason


def test_nudge_no_goal_suffix_when_thread_goal_is_real(tmp_project, session):
    # fixture thread 'hygiene' has a real goal → only the offload line, no goal add-on
    _pin(session, "sess-1")
    _age(session / "arc.md", offload.NUDGE_WINDOW_SECONDS + 60)
    (session / "workspace" / "work.md").write_text("progress\n", encoding="utf-8")
    reason = offload.nudge_reason(tmp_project, "sess-1")
    assert reason and "set-goal" not in reason


# --- dissolved head stands down instead of pulsing (cand 91) -----------------

def _control_home_with_taken_offer(base, *, from_session, taker="successor"):
    """A fresh control-home where *from_session* offered a thread that was then taken."""
    from tests.conftest import build_tide_skeleton
    from tide import handoff_queue as hq

    home = base.parent / "ch-91"
    home.mkdir(exist_ok=True)
    build_tide_skeleton(home, name="ch", control_home=True)
    hq.offer(home, "handoff", arc="-", project="demo", seed="-", from_session=from_session)
    hq.take(home, "handoff", session=taker)
    return home


def test_dissolved_stand_down_note_for_a_handed_off_session(tmp_project, monkeypatch):
    from tide import paths

    home = _control_home_with_taken_offer(tmp_project, from_session="sid-x")
    monkeypatch.setenv(paths.TIDE_HOME_ENV, str(home))
    note = offload._dissolved_stand_down("sid-x")
    assert note and "стой down" in note and "successor" in note


def test_dissolved_stand_down_none_when_still_holding(tmp_project, monkeypatch):
    from tide import paths

    home = _control_home_with_taken_offer(tmp_project, from_session="sid-x")
    monkeypatch.setenv(paths.TIDE_HOME_ENV, str(home))
    assert offload._dissolved_stand_down("someone-else") is None


def test_dissolved_stand_down_none_when_no_control_home(tmp_project, monkeypatch):
    # cand 90's case: control-home can't be resolved → fall through, normal nudge stands
    from tide import paths

    monkeypatch.delenv(paths.TIDE_HOME_ENV, raising=False)
    monkeypatch.chdir(tmp_project)  # a plain project, not a control-home
    assert offload._dissolved_stand_down("sid-x") is None


def test_nudge_hook_stands_down_a_dissolved_head_instead_of_blocking(
    tmp_project, session, monkeypatch, capsys
):
    import io as _io
    from tide import paths

    _pin(session, "sid-x")
    _age(session / "arc.md", offload.NUDGE_WINDOW_SECONDS + 60)
    (session / "workspace" / "w.md").write_text("progress\n", encoding="utf-8")
    home = _control_home_with_taken_offer(tmp_project, from_session="sid-x")
    monkeypatch.setenv(paths.TIDE_HOME_ENV, str(home))
    monkeypatch.chdir(tmp_project)
    monkeypatch.setattr("sys.stdin", _io.StringIO(json.dumps({"session_id": "sid-x"})))

    rc = offload.cmd_offload_nudge(object())
    out, err = capsys.readouterr()
    assert rc == 0
    assert "decision" not in out          # the Stop hook did NOT block a dissolved head
    assert "стой down" in err             # it was told to stand down instead


def test_hook_blocks_with_json_and_respects_antiloop(tmp_project, session, monkeypatch, capsys):
    _pin(session, "sess-1")
    _age(session / "arc.md", offload.NUDGE_WINDOW_SECONDS + 60)
    (session / "workspace" / "work.md").write_text("progress\n", encoding="utf-8")
    monkeypatch.chdir(tmp_project)

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "sess-1"})))
    assert cli.main(["hook", "offload-nudge"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and "tide offload" in out["reason"]

    # anti-loop: the same stop already blocked once → silent pass
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"session_id": "sess-1", "stop_hook_active": True})),
    )
    assert cli.main(["hook", "offload-nudge"]) == 0
    assert capsys.readouterr().out == ""


def test_hook_silent_outside_tide(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert cli.main(["hook", "offload-nudge"]) == 0
    assert capsys.readouterr().out == ""


# --- install merge -----------------------------------------------------------

def test_install_wires_stop_nudge_idempotently():
    from tide.hooks import install

    data: dict = {}
    notes = install.merge_hooks(data)
    assert any("offload-nudge" in n for n in notes)
    groups = data["hooks"]["Stop"]
    assert any(
        h.get("command") == install.OFFLOAD_NUDGE_CMD
        for g in groups for h in g.get("hooks", [])
    )
    assert install.merge_hooks(data) == []  # re-run: nothing to add


def test_find_session_matches_digit_leading_slug(tmp_project):
    # cand 43: session '01-01-mvp' (slug '01-mvp') must resolve by ref '01-mvp'.
    stream.new_thread(tmp_project, "build", goal="g")
    sess = stream.new_session(tmp_project, "build", "01-mvp")
    assert offload.find_session(tmp_project, "01-mvp") == sess
    assert offload.find_session(tmp_project, sess.name) == sess


def test_offload_next_writes_section(tmp_project, session):
    # форма записи (закон доски 07.07): cursor=текущее действие, next=1-3 шага
    offload.offload(tmp_project, "otliv",
                    cursor="женю доску с формой записи",
                    next_steps="таймлайн передач · светофор · форма в скилл")
    text = (session / "arc.md").read_text(encoding="utf-8")
    nxt = text.partition("## next")[2].partition("## ")[0]
    assert "таймлайн передач" in nxt and "светофор" in nxt


def test_cli_offload_next_flag(tmp_project, session, monkeypatch):
    monkeypatch.chdir(tmp_project)
    assert cli.main(["offload", "otliv", "--next", "шаг раз · шаг два"]) == 0
    assert "шаг раз" in (session / "arc.md").read_text(encoding="utf-8")


def test_cli_offload_confirms_a_stranded_reservation(tmp_project, session, monkeypatch, tmp_path, capsys):
    # live 14.07 (forge): the project had no hooks at spawn — the first-prompt flip
    # never fired and the offer stayed reserved («поднимается» forever). The pulse
    # is the session proving it is alive, so it closes the same seam itself.
    from tests.conftest import build_tide_skeleton
    from tide import handoff_queue, paths

    home = tmp_path / "control-home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    monkeypatch.setenv(paths.TIDE_HOME_ENV, str(home))

    fields.set_field(session / "arc.md", "claude-session", "sid-stranded")
    handoff_queue.offer(home, "otliv", arc="hygiene/otliv", project="proj",
                        seed=str(session / "input" / "handoff-seed.md"),
                        from_session="origin-sid")
    key = handoff_queue.list_offers(home)[0]["name"]
    handoff_queue.reserve(home, key, session="sid-stranded")

    monkeypatch.chdir(tmp_project)
    rc = cli.main(["offload", "hygiene/otliv", "--cursor", "жив и работаю"])
    assert rc == 0
    assert "confirmed by this pulse" in capsys.readouterr().out
    assert handoff_queue.list_offers(home)[0]["status"] == handoff_queue.STATUS_TAKEN


# --- cand 106 (второй ложняк) + 108 + 109 -----------------------------------


def test_closure_warning_not_across_clause(tmp_project):
    # «старт-гейт закрыт, строю план НИТИ» — «нити» из другой клаузы (16.07)
    from tide.arc import stream
    from tide.offload import _closure_word_warning

    stream.new_thread(tmp_project, "demo2", goal="ship")
    s = stream.new_session(tmp_project, "demo2", "s1")
    assert _closure_word_warning(
        s / "arc.md", "старт-гейт закрыт, строю план нити по закону 47") is None
    assert _closure_word_warning(s / "arc.md", "нить закрыта, итог в output")


def test_blind_goal_allows_slug_inside_real_words():
    # cand 108: живая цель со слагом внутри — НЕ болванка (кириллицу slugify рубит)
    from tide.placeholders import is_blind_goal

    assert is_blind_goal("стартовать нить test-thread: проверка", "test-thread") is False
    assert is_blind_goal("test-thread", "test-thread") is True
    assert is_blind_goal("debug_deck", "debug-deck") is True


def test_find_by_sid_falls_back_to_closed_sessions(tmp_project):
    # cand 109: арку закрыли, чат жив — sid-роутинг находит закрытый паспорт
    from tide import fields
    from tide.arc import stream
    from tide.offload import find_session_by_claude_id

    stream.new_thread(tmp_project, "office", goal="мир")
    s = stream.new_session(tmp_project, "office", "world")
    fields.set_field(s / "arc.md", "claude-session", "sid-closed-live")
    closed = s.parent / "__{0}__".format(s.name)
    s.rename(closed)  # арка закрыта, чат (sid) продолжает жить
    hit = find_session_by_claude_id(tmp_project, "sid-closed-live")
    assert hit == closed
