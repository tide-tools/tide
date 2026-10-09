"""SOC-101 F59 + F68: пересылка почты передавшей сессии.

F59 — в одном промпте пачка доставок (несколько ``<ao-session-delivery>`` и
``<cross-session-message>``): пересылается КАЖДАЯ, каждая — строкой в журнал
пересылок (``.tide/state/forwarded.log``, FL). Раньше уходила только первая.

F68 — окно «новая поднята, но ещё не приняла» (``raised-at`` есть, ``taken-at``
нет): хук молчал, пока ``is_dissolved`` ложь. Теперь почта пересылается новой
с момента подъёма (адрес — AO-id по ``pickup-session``), а если адреса ещё нет —
ждёт в очереди и уходит при приёме, тоже с записью в FL.

``ao send`` подменён — наружу ничего не уходит.
"""

from __future__ import annotations

import io
import json

import pytest

from tide import cli, fields, handoff_queue as hq, paths, registry
from tide.arc import stream
from tide.hooks import handed_off

from tests.test_handoff_autospawn import world  # noqa: F401  fixture


def _ao(i, sender, body):
    return ('<ao-session-delivery id="session-send:batch-{0}">\n[from {1}] {2}\n'
            '</ao-session-delivery>'.format(i, sender, body))


def _uds(sender, body):
    return ('<cross-session-message from="uds:/tmp/cc-socks/{0}.sock" from-name="{0}" '
            'from-mode="bypass">\n{1}\n</cross-session-message>'.format(sender, body))


BATCH = "\n".join([
    _ao(1, "tide-stack-25", "context-keeper: SOC-98 в In Review"),
    _uds("tide-stack-26-ab", "board: таймлайн готов"),
    _ao(2, "tide-stack-30", "continuity-audit: промер 07 лежит"),
    _uds("tide-stack-27-cd", "design: макет согласован"),
])


class _Ao:
    def __init__(self):
        self.calls = []

    def say(self, handle, text):
        self.calls.append((handle, text))
        return True


def _fl(home):
    try:
        return (paths.state_dir(home) / handed_off.FORWARD_LOG).read_text(
            encoding="utf-8").splitlines()
    except OSError:
        return []


def _base(world, monkeypatch, tmp_path):
    proj, home = world["proj"], world["home"]
    old = stream.new_session(proj, "seam", "old-work", goal="old work")
    fields.set_field(old / "arc.md", "claude-session", "old-sid")
    fields.set_field(old / "arc.md", "title", "proj — seam · 01")
    new = stream.new_session(proj, "seam", "seam-2", goal="шов")
    fields.set_field(new / "arc.md", "title", "proj — seam · 02 · proj-24")
    seed = new / "input" / "handoff-seed.md"
    seed.write_text("# seed\n", encoding="utf-8")
    hq.offer(home, "seam", arc="01-@seam/" + new.name, project="proj", seed=str(seed),
             from_session="old-sid")
    key = hq.list_offers(home)[0]["name"]
    hq.reserve(home, key, session="new-sid")
    mgr = tmp_path / "manager.json"
    mgr.write_text(json.dumps({"ao_session": "management-14"}), encoding="utf-8")
    monkeypatch.setenv("TIDE_MANAGER_FILE", str(mgr))
    monkeypatch.setenv("AO_SESSION_ID", "proj-19")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv(handed_off.FORWARD_ENV, raising=False)
    ao = _Ao()
    monkeypatch.setattr(handed_off, "_ao_adapter", lambda: ao)
    return {"home": home, "ao": ao, "key": key, "rec": hq.list_offers(home)[0]}


def _raise(rec):
    fields.set_field(rec["path"], "raised-by", "auto")
    fields.set_field(rec["path"], "raised-at", "2026-10-09T17:33:06")


def _hook(monkeypatch, prompt, sid="old-sid"):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": sid, "prompt": prompt})))
    return cli.main(["hook", "handoff-confirm"])


# --- F59 ------------------------------------------------------------------------

def test_incoming_all_finds_every_delivery_in_order():
    got = handed_off.incoming_all(BATCH)
    assert [m["sender"] for m in got] == [
        "tide-stack-25", "tide-stack-26-ab", "tide-stack-30", "tide-stack-27-cd"]
    assert len({m["id"] for m in got}) == 4
    assert handed_off.incoming(BATCH)["sender"] == "tide-stack-25"
    assert handed_off.incoming_all("Гриша: сделай доску") == []


def test_batch_of_four_after_take_forwards_four_into_fl(world, monkeypatch, tmp_path, capsys):
    w = _base(world, monkeypatch, tmp_path)
    hq.take(w["home"], w["key"], session="new-sid")
    assert _hook(monkeypatch, BATCH) == 0
    out = capsys.readouterr().out
    assert [h for h, _ in w["ao"].calls] == ["proj-24"] * 4
    bodies = [t for _, t in w["ao"].calls]
    assert "отправитель tide-stack-25] context-keeper" in bodies[0]
    assert "отправитель tide-stack-27-cd] design" in bodies[3]
    fl = _fl(w["home"])
    assert len(fl) == 4
    for sender in ("tide-stack-25", "tide-stack-26-ab", "tide-stack-30", "tide-stack-27-cd"):
        assert any("от {0}".format(sender) in ln for ln in fl)
        assert "Скажи отправителю {0} новый адрес".format(sender) in out
    # повтор пачки — ни одной второй пересылки
    assert _hook(monkeypatch, BATCH) == 0
    assert len(w["ao"].calls) == 4 and len(_fl(w["home"])) == 4


def test_batch_skips_only_new_and_manager_letters(world, monkeypatch, tmp_path, capsys):
    w = _base(world, monkeypatch, tmp_path)
    hq.take(w["home"], w["key"], session="new-sid")
    prompt = "\n".join([_ao(7, "proj-24", "нить приняла, жду вердикт"),
                        _ao(8, "management-14", "ок"),
                        _ao(9, "tide-stack-25", "итог готов")])
    assert _hook(monkeypatch, prompt) == 0
    assert len(w["ao"].calls) == 1 and "tide-stack-25" in w["ao"].calls[0][1]
    assert len(_fl(w["home"])) == 1
    # письмо новой без поручения — не тревога «две головы» (F49 не сломан)
    assert not (paths.state_dir(w["home"]) / handed_off.TWO_HEADS_LOG).exists()


