"""SOC-72 на пути `tide handoff continue` (acceptance-01 п.4): новая сессия получает
title и имя в AO «проект — нить · NN · <AO-id>», как на ручном пути. AO фальшивый."""

from __future__ import annotations

from tide import cli, fields, handoff_queue as hq
from tide.launcher import handoff

from tests.test_handoff_autospawn import _Fake, _FakeAo, _use, world  # noqa: F401  fixture


class _NamedAo(_FakeAo):
    def __init__(self):
        super().__init__(ref="proj-7")
        self.renamed = []

    def rename(self, handle, name):
        self.renamed.append((handle, name))
        return True


def _session(world):
    (rec,) = hq.list_offers(world["home"])
    from pathlib import Path
    return Path(rec["seed"]).parent.parent


def test_handoff_continue_names_the_session_with_the_ao_id(world, monkeypatch):
    monkeypatch.setattr(handoff, "_sleep", lambda s: None)
    adapter = _use(monkeypatch, _NamedAo())
    assert cli.main(["handoff", "seam"]) == 0
    title = fields.read_field(_session(world) / "arc.md", "title")
    assert title.startswith("proj — seam · ") and title.endswith(" · proj-7")
    assert adapter.renamed == [("proj-7", title)]
    # AO took the bare name at spawn; the rename adds the AO id
    assert adapter.spawned["title"] == title.rsplit(" · ", 1)[0]


def test_non_ao_adapter_keeps_the_bare_title(world, monkeypatch):
    _use(monkeypatch, _Fake())
    assert cli.main(["handoff", "seam"]) == 0
    title = fields.read_field(_session(world) / "arc.md", "title")
    assert title.startswith("proj — seam · ") and "term_x" not in title
