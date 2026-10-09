"""SOC-101 п.2 / F35: reception records what the receiving session understood —
its OWN words from its first real pulse land on the offer record; the seeded
passport goal (next[0] of the seed) is an echo and never counts."""

from __future__ import annotations

from tide import cli, fields, handoff_queue as hq

from tests.test_handoff_queue import _project_with_seeded_offer


def _rec(home):
    return hq.list_offers(home)[0]


def test_reception_stamps_no_goal_first_pulse_gives_understood(tmp_control_home,
                                                                monkeypatch):
    sess = _project_with_seeded_offer(tmp_control_home)
    # автохендофф кладёт в паспорт next[0] сида — это эхо, не понимание (F35)
    fields.set_field(sess / "arc.md", "goal", "довести аудит хендоффов до доски")
    hq.reserve(tmp_control_home, "kickoff", session="sid-1")
    hq.confirm_for_session(tmp_control_home, "sid-1")
    r = _rec(tmp_control_home)
    assert r["understood_goal"] == "-"
    # the mechanical «нить принята» pulse is not the session's own understanding
    assert r["first_pulse"] == "-"

    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    monkeypatch.chdir(sess)
    assert cli.main(["offload", sess.name, "--cursor", "понял: сверяю сид с паспортом",
                     "принял нить: Гриша ждёт аудит, нельзя трогать доску"]) == 0
    r = _rec(tmp_control_home)
    assert r["first_pulse"] == "понял: сверяю сид с паспортом"
    assert r["first_pulse_at"] != "-"
    assert r["understood_goal"] == "принял нить: Гриша ждёт аудит, нельзя трогать доску"
    assert cli.main(["offload", sess.name, "--cursor", "второй пульс", "другое"]) == 0
    r = _rec(tmp_control_home)
    assert r["first_pulse"] == "понял: сверяю сид с паспортом"
    assert r["understood_goal"] == "принял нить: Гриша ждёт аудит, нельзя трогать доску"


def test_cursor_only_first_pulse_is_the_understood_goal(tmp_control_home, monkeypatch):
    sess = _project_with_seeded_offer(tmp_control_home)
    fields.set_field(sess / "arc.md", "goal", "<цель>")
    hq.take(tmp_control_home, "kickoff", session="sid-2")
    assert _rec(tmp_control_home)["understood_goal"] == "-"

    fields.set_field(sess / "arc.md", "goal", "собрать аудит сида")
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    monkeypatch.chdir(sess)
    assert cli.main(["offload", sess.name, "--cursor", "сверяю сид своими словами"]) == 0
    r = _rec(tmp_control_home)
    assert r["understood_goal"] == "сверяю сид своими словами"   # не goal паспорта
    assert r["first_pulse"] == "сверяю сид своими словами"


def test_note_only_first_pulse(tmp_control_home, monkeypatch):
    sess = _project_with_seeded_offer(tmp_control_home)
    hq.take(tmp_control_home, "kickoff", session="sid-3")
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    monkeypatch.chdir(sess)
    assert cli.main(["offload", sess.name, "цель поставлена"]) == 0
    r = _rec(tmp_control_home)
    assert r["understood_goal"] == "цель поставлена"
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


# --- старт-гейт приёма: пересказ своими словами (§4.2, SOC-101 F35) -------------

def test_retell_gaps_rule():
    # живой первый пульс приёма 260: ни ожидания, ни «ждёт Гришу», ни запретов
    live = ("принял нить по офферу 260 от main · 03 (tide-stack-19); в полёте у · 03 "
            "ничего, входящие она перешлёт")
    assert hq.retell_gaps(live) == ["ожидание Гриши", "что ждёт Гришу", "запреты"]
    full = ("Ожидание: Гриша ждёт промер к вечеру · Ждёт Гришу: PR #5, SOC-89 · "
            "Нельзя: пушить в main, дублировать board")
    assert hq.retell_gaps(full) == []
    assert hq.retell_gaps("в сиде нет ожидания Гриши; ждёт Гришу — ничего; "
                          "не трогаю доску") == []
    # «ждёт Гришу» не засчитывается за ожидание Гриши и наоборот
    assert hq.retell_gaps("ждёт Гришу PR #5, нельзя пушить") == ["ожидание Гриши"]
    assert hq.retell_gaps("Гриша ждёт промер, нельзя пушить") == ["что ждёт Гришу"]


def _first_pulse_err(tmp_control_home, monkeypatch, capsys, *argv):
    sess = _project_with_seeded_offer(tmp_control_home)
    hq.take(tmp_control_home, "kickoff", session="sid-r")
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    monkeypatch.chdir(sess)
    capsys.readouterr()
    assert cli.main(["offload", sess.name, *argv]) == 0   # не блок
    return sess, capsys.readouterr().err


def test_first_pulse_without_retell_warns_not_blocks(tmp_control_home, monkeypatch, capsys):
    sess, err = _first_pulse_err(tmp_control_home, monkeypatch, capsys,
                                 "--cursor", "рассылаю адрес нитям",
                                 "принял нить, в полёте ничего")
    assert "старт-гейт приёма" in err
    assert "нет: ожидание Гриши, что ждёт Гришу, запреты" in err
    assert "Ожидание: … · Ждёт Гришу: … · Нельзя: …" in err
    assert "принял нить, в полёте ничего" in (sess / "arc.md").read_text(encoding="utf-8")
    # второй пульс — подсказки больше нет
    assert cli.main(["offload", sess.name, "--cursor", "дальше"]) == 0
    assert "старт-гейт приёма" not in capsys.readouterr().err


def test_first_pulse_lists_only_missing(tmp_control_home, monkeypatch, capsys):
    _sess, err = _first_pulse_err(tmp_control_home, monkeypatch, capsys,
                                  "--cursor", "сверяю сид",
                                  "Ожидание: Гриша ждёт промер; нельзя пушить")
    assert "нет: что ждёт Гришу." in err
    assert "Ждёт Гришу: …" in err and "Нельзя: …" not in err


def test_full_retell_is_silent(tmp_control_home, monkeypatch, capsys):
    _sess, err = _first_pulse_err(
        tmp_control_home, monkeypatch, capsys, "--cursor", "сверяю сид",
        "Ожидание: промер к вечеру · Ждёт Гришу: PR #5 · Нельзя: пушить в main")
    assert "старт-гейт приёма" not in err
