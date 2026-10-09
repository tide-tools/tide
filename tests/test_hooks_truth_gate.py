"""tide.hooks.truth_gate — записи решений и фактов правит только держатель context-keeper.

Всё на tmp_path: своя тема, свой держатель; живые журналы не трогаем.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from tide import cli, truth_edits as edits
from tide.hooks import truth_gate as tg

KEEPER = {"thread": "34-@context-keeper", "project": "tide-stack",
          "claude_session": "sid-keeper", "ao_session": "tide-stack-28",
          "title": "tide-stack — context-keeper · 04 · tide-stack-28"}


@pytest.fixture
def topic(tmp_path):
    d = tmp_path / "proj" / ".tide" / "topics" / "01-tema"
    d.mkdir(parents=True)
    (d / "facts.md").write_text("# Факты\n\n## Факты (новые сверху)\n\n- было\n", encoding="utf-8")
    (d / "decisions-tg.jsonl").write_text("", encoding="utf-8")
    edits.baseline(d)
    return d


def pre(tool, tool_input, sid="sid-other", agent=None, cwd=None):
    p = {"hook_event_name": "PreToolUse", "session_id": sid, "tool_name": tool,
         "tool_input": tool_input}
    if agent:
        p["agent_id"] = agent
    if cwd:
        p["cwd"] = str(cwd)
    return p


def test_foreign_session_edit_of_facts_denied(topic):
    ok, why = tg.decide(pre("Edit", {"file_path": str(topic / "facts.md"),
                                     "old_string": "было", "new_string": "стало"}), KEEPER)
    assert not ok
    assert "записи решений и фактов ведёт context-keeper" in why
    assert "ao send tide-stack-28" in why and str(topic / "facts.md") in why


def test_holder_and_its_subagent_pass(topic):
    inp = {"file_path": str(topic / "facts.md"), "content": "x"}
    assert tg.decide(pre("Write", inp, sid="sid-keeper"), KEEPER) == (True, "")
    assert tg.decide(pre("Write", inp, sid="sid-keeper", agent="a1"), KEEPER) == (True, "")
    assert not tg.decide(pre("Write", inp, sid="sid-other", agent="a1"), KEEPER)[0]


def test_other_files_untouched_by_gate(topic, tmp_path):
    other = tmp_path / "proj" / "facts.md"  # не в .tide/topics
    for path in (other, topic / "topic.md"):
        assert tg.decide(pre("Write", {"file_path": str(path), "content": "x"}), KEEPER)[0]
    assert tg.decide(pre("Bash", {"command": "ls -la"}), KEEPER)[0]


def test_journal_of_edits_closed_for_everyone(topic):
    inp = {"file_path": str(topic / edits.EDITS_FILE), "content": "{}"}
    ok, why = tg.decide(pre("Write", inp, sid="sid-keeper"), KEEPER)
    assert not ok and "журнал правок, его пишет только tide" in why


@pytest.mark.parametrize("cmd, allowed", [
    ("cat facts.md", True),
    ("grep -n решение facts.md | head -3", True),
    ("sed -n 1,5p facts.md", True),
    ("echo x >> facts.md", False),
    ("sed -i '' 's/было/стало/' facts.md", False),
    ("printf x | tee -a decisions-tg.jsonl", False),
    ("python3 -c \"open('facts.md','a').write('x')\"", False),
    ("tide truth decide --topic a/01 --q 1 --a 2 --decision x", False),
    ("tide truth decide --topic a/01 --q 1 --a 2 --decision x --dry-run", True),
    ("tide truth check", True),
])
def test_bash_foreign(topic, cmd, allowed):
    ok, _ = tg.decide(pre("Bash", {"command": cmd}, cwd=topic), KEEPER)
    assert ok is allowed


def test_bash_absolute_path_foreign_denied(topic, tmp_path):
    cmd = "echo x >> {0}".format(topic / "facts.md")
    assert not tg.decide(pre("Bash", {"command": cmd}, cwd=tmp_path), KEEPER)[0]
    assert tg.decide(pre("Bash", {"command": cmd}, sid="sid-keeper", cwd=tmp_path), KEEPER)[0]


def test_holder_blocked_when_files_off_journal(topic):
    (topic / "facts.md").write_text("подмена\n", encoding="utf-8")
    ok, why = tg.decide(pre("Edit", {"file_path": str(topic / "facts.md")}, sid="sid-keeper"),
                        KEEPER)
    assert not ok and "без журнала правок правка не проходит" in why


def test_no_keeper_thread_gate_is_silent(topic):
    ok, _ = tg.decide(pre("Write", {"file_path": str(topic / "facts.md")}), None,
                      lookup=lambda: None)
    assert ok


def test_post_records_edit(topic):
    (topic / "facts.md").write_text("стало\n", encoding="utf-8")
    payload = dict(pre("Edit", {"file_path": str(topic / "facts.md")}, sid="sid-keeper",
                       agent="a1"), hook_event_name="PostToolUse")
    rows = tg.record(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "edit" and row["tool"] == "Edit" and row["files"] == ["facts.md"]
    assert row["session"] == "sid-keeper" and row["agent"] == "a1"
    assert edits.check_dir(topic) == []
    assert tg.record(payload) == []  # второй раз ничего не изменилось


def test_cli_exit_codes(topic, monkeypatch, capsys):
    monkeypatch.setattr(tg, "keeper", lambda home=None: KEEPER)
    payload = pre("Write", {"file_path": str(topic / "facts.md"), "content": "x"})
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["hook", "truth-gate"]) == 2
    assert "ведёт context-keeper" in capsys.readouterr().err
    payload["session_id"] = "sid-keeper"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["hook", "truth-gate"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("не json"))
    assert cli.main(["hook", "truth-gate"]) == 0


def test_keeper_resolved_from_roster(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".tide").mkdir(parents=True)
    a, b = tmp_path / "a", tmp_path / "b"
    (a / ".tide" / "arcs").mkdir(parents=True)
    (b / ".tide" / "arcs" / "34-@context-keeper").mkdir(parents=True)
    (home / "roster.md").write_text("# tide roster\na | {0}\nb | {1}\n".format(a, b),
                                    encoding="utf-8")
    from tide.arc import holder as _holder

    seen = {}

    def fake(root, ref, home=None, alive=None):
        seen["root"] = Path(root)
        return {"thread": "34-@context-keeper", "arc": "", "claude_session": "sid-k",
                "ao_session": "b-1", "alive": None}

    monkeypatch.setattr(_holder, "holder", fake)
    k = tg.keeper(home)
    assert seen["root"] == b and k["claude_session"] == "sid-k" and k["project"] == "b"
    monkeypatch.setenv(tg.KEEPER_ENV, "a/context-keeper")
    assert tg.keeper(home) is None
