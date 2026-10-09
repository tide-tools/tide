"""SOC-101 п.2: reception records what the receiving session understood — the
session passport's goal and its first real pulse land on the offer record."""

from __future__ import annotations

from tide import cli, fields, handoff_queue as hq

from tests.test_handoff_queue import _project_with_seeded_offer


def _rec(home):
    return hq.list_offers(home)[0]


def test_reception_stamps_real_goal_and_first_pulse_once(tmp_control_home, monkeypatch):
    sess = _project_with_seeded_offer(tmp_control_home)
    fields.set_field(sess / "arc.md", "goal", "довести аудит хендоффов до доски")
    hq.reserve(tmp_control_home, "kickoff", session="sid-1")
    hq.confirm_for_session(tmp_control_home, "sid-1")
    r = _rec(tmp_control_home)
    assert r["understood_goal"] == "довести аудит хендоффов до доски"
    # the mechanical «нить принята» pulse is not the session's own understanding
    assert r["first_pulse"] == "-"

    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    monkeypatch.chdir(sess)
    assert cli.main(["offload", sess.name, "--cursor", "понял: сверяю сид с паспортом",
                     "принял нить"]) == 0
    r = _rec(tmp_control_home)
    assert r["first_pulse"] == "понял: сверяю сид с паспортом"
    assert r["first_pulse_at"] != "-"
    assert cli.main(["offload", sess.name, "--cursor", "второй пульс"]) == 0
    assert _rec(tmp_control_home)["first_pulse"] == "понял: сверяю сид с паспортом"


def test_blind_goal_at_reception_is_filled_at_first_pulse(tmp_control_home, monkeypatch):
    sess = _project_with_seeded_offer(tmp_control_home)
    fields.set_field(sess / "arc.md", "goal", "<цель>")
    hq.take(tmp_control_home, "kickoff", session="sid-2")
    assert _rec(tmp_control_home)["understood_goal"] == "-"

    fields.set_field(sess / "arc.md", "goal", "собрать аудит сида")
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    monkeypatch.chdir(sess)
    assert cli.main(["offload", sess.name, "цель поставлена"]) == 0
    r = _rec(tmp_control_home)
    assert r["understood_goal"] == "собрать аудит сида"
    assert r["first_pulse"] == "цель поставлена"   # no cursor → the note


def test_note_with_key_like_line_does_not_catch_new_fields(tmp_control_home):
    path = hq.offer(tmp_control_home, "n", arc="-", project="p", seed="-",
                    note="look: here\nmore")
    hq._set_field(path, hq.FIRST_PULSE, "x")
    text = path.read_text(encoding="utf-8")
    assert text.index("first-pulse: x") < text.index("## note")


def test_pulse_of_an_ordinary_session_touches_no_offer(tmp_control_home):
    sess = _project_with_seeded_offer(tmp_control_home)
    assert hq.note_first_pulse(tmp_control_home, sess, cursor="x") is None  # still offered
    assert _rec(tmp_control_home)["first_pulse"] == "-"
