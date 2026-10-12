"""SOC-245: после принятого хендоффа почта нити идёт преемнику, а не старой сессии.

Живой случай 12.10: письма с ``to-session: 29-techlead-29`` / ``31-techlead-31``
(адрес ``<нить>@<сессия>``) висели «НЕ ПРОЧИТАНО адресатом» и били тревогу
менеджеру — адресат давно передал нить, пинги уходили в мёртвую AO-сессию, а
квитанция преемника не считалась. Теперь:

* ``<нить>@<старая>`` после ПРИНЯТОГО оффера — письмо преемнику по цепочке
  хендоффов (пинг и квитанция — его), в шапке и журнале — от кого перенаправлено;
* письмо старой, отправленное ДО приёма, — повтор пингует преемника, квитанция
  преемника закрывает письмо (тревоги менеджеру нет);
* оффер повешен, но не принят — как раньше, старой.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tide import cli, handoff_queue as hq, mail, registry, roster
from tide.arc import stream
from tide.launcher import autohandoff


@pytest.fixture
def chain(tmp_path, monkeypatch):
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    build_tide_skeleton(proj, name="proj")
    roster.add(home, "proj", str(proj))
    thread = stream.new_thread(proj, "techlead", goal="техлид")
    s = {}
    for n in (29, 30, 31):
        s[n] = stream.new_session(proj, "techlead", "techlead-{0}".format(n),
                                  claude_session="sid-{0}".format(n))
        registry.record(home, "sid-{0}".format(n), "proj-{0}".format(n + 57), "techlead")
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv(mail.WATCH_ENV, "0")
    monkeypatch.setenv(mail.BATCH_ENV, "0")
    monkeypatch.delenv("TIDE_ALERT_CMD", raising=False)
    monkeypatch.delenv(mail.AO_SESSION_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.chdir(proj)
    sent, alerts = [], []
    monkeypatch.setattr(autohandoff, "_ao_send", lambda h, t: sent.append((h, t)) or 0)
    monkeypatch.setattr(autohandoff, "send_alert",
                        lambda text, **kw: alerts.append(text) or 0)
    monkeypatch.setattr(mail, "_ao_terminated", lambda: set())

    def hand(old: int, new: int, *, take: bool = True) -> str:
        seed = s[new] / "input" / "handoff-seed.md"
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text("# seed\n", encoding="utf-8")
        hq.offer(home, "techlead", arc="{0}/{1}".format(thread.name, s[new].name),
                 project="proj", seed=str(seed), from_session="sid-{0}".format(old))
        key = hq.list_offers(home)[-1]["name"]
        if take:
            hq.take(home, key, session="sid-{0}".format(new))
        return key

    return {"home": home, "proj": proj, "s": s, "sent": sent, "alerts": alerts,
            "hand": hand, "tdir": mail.thread_dir("techlead")}


def _to(chain):
    return [h for h, _t in chain["sent"]]


def test_at_old_session_after_take_goes_to_successor(chain):
    chain["hand"](29, 30)
    out = mail.send("techlead@proj-86", "к 077: правка C готова", sender="music-33")
    assert _to(chain) == ["proj-87"]
    text = (chain["tdir"] / "inbox" / "001-от-music-33.md").read_text(encoding="utf-8")
    assert "to-session: {0} (proj-87)".format(chain["s"][30].name) in text
    assert "redirected-from: {0} (proj-86)".format(chain["s"][29].name) in text
    assert out["redirected_from"] == chain["s"][29].name
    # квитанция преемника — квитанция адресата
    mail.read("techlead", out["num"], by="proj-87")
    assert mail.unread(chain["tdir"]) == []


def test_chain_of_two_handoffs_reaches_the_current_holder(chain):
    chain["hand"](29, 30)
    chain["hand"](30, 31)
    mail.send("techlead@techlead-29", "старой по имени", sender="m")
    assert _to(chain) == ["proj-88"]


def test_letter_sent_before_take_retries_to_successor_and_his_ack_counts(chain):
    mail.send("techlead@proj-86", "до передачи", sender="music-33")
    assert _to(chain) == ["proj-86"]
    chain["hand"](29, 30)
    later = mail._now() + timedelta(minutes=20)
    assert mail.step(chain["tdir"], "001", now=later, minutes=15) == "retry"
    assert _to(chain) == ["proj-86", "proj-87"]
    mail.read("techlead", "001", by="proj-87")
    assert mail.step(chain["tdir"], "001", now=later + timedelta(minutes=60),
                     minutes=15) == "acked"
    assert chain["alerts"] == []


def test_offer_not_taken_still_goes_to_old(chain):
    chain["hand"](29, 30, take=False)
    mail.send("techlead@proj-86", "оффер висит", sender="m")
    assert _to(chain) == ["proj-86"]


def test_plain_thread_address_goes_to_holder_not_old(chain):
    chain["hand"](29, 30)
    mail.send("techlead", "нити", sender="m")
    # 31 — параллельная живая сессия нити (не из этой цепочки): письмо общее ей и 30
    assert "proj-86" not in _to(chain) and "proj-87" in _to(chain)


def test_cli_send_and_ls_name_the_successor(chain, capsys):
    chain["hand"](29, 30)
    assert cli.main(["mail", "send", "proj/techlead@techlead-29", "старой", "--from",
                     "m"]) == 0
    out = capsys.readouterr().out
    assert "пинг proj-87" in out
    assert "передала нить" in out and chain["s"][30].name in out
    assert cli.main(["mail", "ls", "techlead"]) == 0
    assert "НЕ ПРОЧИТАНО адресатом {0}".format(chain["s"][30].name) in \
        capsys.readouterr().out
