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
    assert reason and "tide offload otliv" in reason


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
