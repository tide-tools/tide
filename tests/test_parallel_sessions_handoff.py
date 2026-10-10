"""SOC-143: автохендофф в нити с двумя живыми параллельными сессиями.

Живой случай 09.10 (app-development, нить techlead): техлид·11 передал нить
техлиду·13, а параллельно живой техлид·12 автохендоффом передал свою линию
техлиду·14. Паспорт 14 подписался «from: techlead-13» (новейшая арка нити), а
хук старой 11 пересылал письма — в том числе от самой 13 — в 14 (whois нити =
новейшая принятая), хотя преемник 11 — это 13.

Здесь то же на временном дереве: from: преемника — сессия, которая сама
передаёт; пересылка старой — только преемнику ЕЁ передачи.
"""

from __future__ import annotations

import json

import pytest

from tide import fields, handoff_queue as hq, roster
from tide.arc import stream
from tide.hooks import handed_off
from tide.launcher import autohandoff as ah, handoff, reception

SID11 = "11111111-0000-4000-8000-000000000011"
SID12 = "11111111-0000-4000-8000-000000000012"
SID13 = "11111111-0000-4000-8000-000000000013"
SID14 = "11111111-0000-4000-8000-000000000014"


class _Ao:
    def __init__(self):
        self.calls = []

    def say(self, handle, text):
        self.calls.append((handle, text))
        return True


def _msg(sender: str, body: str, mid: str) -> str:
    return ('<ao-session-delivery id="{0}">\n[from {1}] {2}\n'
            '</ao-session-delivery>'.format(mid, sender, body))


@pytest.fixture
def par(tmp_path, monkeypatch):
    """Нить seam: 01 (sid 11) уже передала 03 (sid 13, живая); 02 (sid 12) жива
    параллельно и ещё не передавала."""
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    build_tide_skeleton(proj, name="proj")
    roster.add(home, "proj", str(proj))
    thread = stream.new_thread(proj, "seam", goal="держать шов чистым")
    s11 = stream.new_session(proj, "seam", "tl-11", claude_session=SID11)
    s12 = stream.new_session(proj, "seam", "tl-12", claude_session=SID12)
    s13 = stream.new_session(proj, "seam", "tl-13", from_ref=s11.name,
                             claude_session=SID13)
    fields.set_field(s11 / "arc.md", "title", "proj — seam · 11 · proj-34")
    fields.set_field(s12 / "arc.md", "title", "proj — seam · 12 · proj-36")
    fields.set_field(s13 / "arc.md", "title", "proj — seam · 13 · proj-50")
    seed = s13 / "input" / "handoff-seed.md"
    seed.write_text("# seed\n", encoding="utf-8")
    rec = hq.offer(home, "seam", arc="{0}/{1}".format(thread.name, s13.name),
                   project="proj", seed=str(seed), from_session=SID11)
    hq.reserve(home, rec.stem, session=SID13)
    hq.take(home, rec.stem, session=SID13)
    mgr = tmp_path / "manager.json"
    mgr.write_text(json.dumps({"ao_session": "management-22"}), encoding="utf-8")
    monkeypatch.setenv("TIDE_MANAGER_FILE", str(mgr))
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    for k in (handoff.NO_SPAWN_ENV, "AO_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
              handed_off.FORWARD_ENV, ah.ALERT_CMD_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(proj)
    ao = _Ao()
    monkeypatch.setattr(handed_off, "_ao_adapter", lambda: ao)
    return {"home": home, "proj": proj, "thread": thread, "s11": s11, "s12": s12,
            "s13": s13, "offer11": rec.stem, "ao": ao}


def _born(par, offer_stem):
    rec = next(r for r in hq.list_offers(par["home"]) if r["name"] == offer_stem)
    return par["thread"] / "arcs" / rec["arc"].split("/", 1)[1]


def _auto_from_12(par, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID12)
    res = ah.run_auto(par["proj"], session_ref=par["s12"].name, spawn=False)
    stem = res.handoff.offer_path.stem
    born = _born(par, stem)
    fields.set_field(born / "arc.md", "title", "proj — seam · 14 · proj-51")
    hq.reserve(par["home"], stem, session=SID14)
    hq.take(par["home"], stem, session=SID14)
    return stem, born


def test_auto_handoff_from_parallel_session_signs_its_own_from(par, monkeypatch):
    stem, born = _auto_from_12(par, monkeypatch)
    # новейшая арка нити до рождения — 03 (tl-13), но передаёт 02 (tl-12)
    assert fields.read_field(born / "arc.md", "from") == "tl-12"
    rec = next(r for r in hq.list_offers(par["home"]) if r["name"] == stem)
    assert rec["from_session"] == SID12


def test_plain_handoff_takes_from_of_the_caller_env(par, monkeypatch):
    """Без --from-session: from — сессия вызывателя ($CLAUDE_CODE_SESSION_ID), а не
    новейшая; вызыватель не из нити — как раньше, новейшая."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID12)
    res = handoff.run_handoff(par["proj"], arc_ref=par["thread"].name,
                              summary="# s\n", spawn=False)
    rec = next(r for r in hq.list_offers(par["home"]) if r["name"] == res.offer_path.stem)
    born = _born(par, rec["name"])
    assert fields.read_field(born / "arc.md", "from") == "tl-12"
    assert rec["from_session"] == SID12

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "чужой-сид")
    res2 = handoff.run_handoff(par["proj"], arc_ref=par["thread"].name,
                               summary="# s\n", spawn=False)
    born2 = _born(par, res2.offer_path.stem)
    assert fields.read_field(born2 / "arc.md", "from") == stream.slug.entry_slug(born.name)


def test_successor_is_the_taker_of_that_handoff_not_newest(par, monkeypatch):
    stem, _born14 = _auto_from_12(par, monkeypatch)
    recs = {r["name"]: r for r in hq.list_offers(par["home"])}
    assert reception.successor(par["home"], recs[par["offer11"]])["ao_session"] == "proj-50"
    assert reception.successor(par["home"], recs[stem])["ao_session"] == "proj-51"


def test_old_11_forwards_only_to_13_and_never_13s_own_letters(par, monkeypatch):
    _auto_from_12(par, monkeypatch)
    home, ao = par["home"], par["ao"]
    env11 = {"AO_SESSION_ID": "proj-34"}
    # письмо самой 13 своей старой 11 — не пересылается вовсе (тем более в 14)
    note = handed_off.redirect(
        {"session_id": SID11, "prompt": _msg("proj-50-15", "сверка хаба: ок", "m-1")},
        home=home, env=env11)
    assert ao.calls == []
    assert "proj-50" in note and "proj-51" not in note
    # стороннее письмо старой 11 — её преемнику 13, не 14
    handed_off.redirect(
        {"session_id": SID11, "prompt": _msg("proj-48-8b", "пакет дизайнера", "m-2")},
        home=home, env=env11)
    assert [h for h, _t in ao.calls] == ["proj-50"]
    # стороннее письмо старой 12 — её преемнику 14
    handed_off.redirect(
        {"session_id": SID12, "prompt": _msg("proj-48-8b", "ревью k172", "m-3")},
        home=home, env={"AO_SESSION_ID": "proj-36"})
    assert [h for h, _t in ao.calls] == ["proj-50", "proj-51"]


def test_chain_still_follows_further_handoffs(par, monkeypatch):
    """13 передала дальше (15) — письма старой 11 идут 15, а не 14."""
    _auto_from_12(par, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID13)
    res = handoff.run_handoff(par["proj"], arc_ref=par["thread"].name,
                              summary="# s\n", spawn=False)
    stem = res.offer_path.stem
    born = _born(par, stem)
    assert fields.read_field(born / "arc.md", "from") == "tl-13"
    fields.set_field(born / "arc.md", "title", "proj — seam · 15 · proj-60")
    hq.reserve(par["home"], stem, session="sid-15")
    hq.take(par["home"], stem, session="sid-15")
    rec11 = next(r for r in hq.list_offers(par["home"]) if r["name"] == par["offer11"])
    assert reception.successor(par["home"], rec11)["ao_session"] == "proj-60"
