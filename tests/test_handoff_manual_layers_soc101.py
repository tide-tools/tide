"""SOC-101 — ручной ``tide handoff <нить>`` (без --auto) предупреждает о пустых
слоях нити так же, как ``--auto`` (0e7f873), и передачу не останавливает."""

from __future__ import annotations

from tide import cli
from tide.arc import stream, thread_layers
from tide.launcher import handoff


def test_manual_handoff_warns_on_empty_layers_and_does_not_block(tmp_project, monkeypatch, capsys):
    thread = stream.new_thread(tmp_project, "hygiene", goal="держать шов чистым")
    expected = thread_layers.exit_warning(thread)
    assert expected  # свежая нить — слои пусты
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["handoff", "hygiene", "--mode", "continue", "--dry-run"])
    cap = capsys.readouterr()
    assert rc == 0
    assert expected in cap.err
    assert "«ожидание Гриши», «где мы», «опыт»" in cap.err and "--seed-skeleton" in cap.err
    assert "handoff [continue]" in cap.out  # передача прошла


def test_manual_handoff_silent_when_layers_filled(tmp_project, monkeypatch, capsys):
    thread = stream.new_thread(tmp_project, "hygiene", goal="держать шов чистым")
    pp = stream.passport_path(thread)
    head = pp.read_text(encoding="utf-8").partition("## where we are\n")[0]
    pp.write_text(head + "## where we are\n### ожидание Гриши\n- а (09.10)\n\n### где мы\n"
                  "- б\n\n### опыт\n- в\n", encoding="utf-8")
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["handoff", "hygiene", "--dry-run"])
    assert rc == 0
    assert "слои нити пусты" not in capsys.readouterr().err


def test_manual_handoff_plain_arc_no_warning(tmp_project, monkeypatch, capsys):
    stream.new_arc(tmp_project, "ship-it")
    assert handoff.layers_warning(tmp_project, "ship-it") is None
    assert handoff.layers_warning(tmp_project, "no-such-arc") is None
    monkeypatch.chdir(tmp_project)
    assert cli.main(["handoff", "ship-it", "--dry-run"]) == 0
    assert "слои нити пусты" not in capsys.readouterr().err
