"""SOC-101 F21: сторож контекста не требует хендофф у сессии, что уже отдала нить.

Её оффер принят (is_dissolved) — вместо «передай нить» один раз: «нить передана
<новой>, доведи в работе и перешли итоги ей». Дальше — тишина.
"""

from __future__ import annotations

from tide import fields, handoff_queue as hq
from tide.arc import stream
from tide.hooks import context_watch as cw

from tests.test_autohandoff import SID, _payload, world  # noqa: F401  fixture


def _hand_over(world):
    new = stream.new_session(world["proj"], "seam", "seam-2")
    fields.set_field(new / "arc.md", "title", "proj — seam · 02 · proj-7")
    (new / "input").mkdir(exist_ok=True)
    seed = new / "input" / "handoff-seed.md"
    seed.write_text("# seed\n", encoding="utf-8")
    path = hq.offer(world["home"], "seam", arc="seam/seam-2", project="proj",
                    seed=str(seed), from_session=SID)
    hq.take(world["home"], path.stem, session="new-sid")
    return new


def test_handed_over_session_is_told_once_not_asked_for_handoff(world):
    _hand_over(world)
    first = cw.watch_reason(world["proj"], _payload(world, 900_000))
    assert first.startswith("tide: нить передана proj — seam · 02 · proj-7 — хендофф от "
                            "тебя больше не нужен. Новое не начинай: доведи то, что уже "
                            "в работе, и перешли итоги ей.")
    assert "ты передала нить, контекст 900 тыс." in first  # SOC-98: ступень 500 тем же блоком
    assert cw.AUTO_CMD not in first
    # once: later stops are silent, whatever the context
    assert cw.watch_reason(world["proj"], _payload(world, 950_000)) is None
    assert cw.watch_reason(world["proj"], _payload(world, 100_000)) is None
    assert (fields.read_field(world["sess"] / "arc.md", cw.HANDED_TOLD_FIELD) or "").strip()


def test_told_even_below_the_threshold(world):
    # it is not about the context any more — the thread moved on
    _hand_over(world)
    assert cw.watch_reason(world["proj"], _payload(world, 10_000)) == cw.handed_over_text(
        "proj — seam · 02 · proj-7")


def test_not_handed_over_keeps_the_ladder(world):
    assert cw.AUTO_CMD in cw.watch_reason(world["proj"], _payload(world, 260_000))


def test_a_later_take_outweighs_an_old_giveaway(world):
    # live 09.10: the same claude id gave thread A away long ago (154-hod) and later
    # TOOK this thread (228-30-handoffs) — it holds a thread, nothing was handed over
    other = stream.new_thread(world["proj"], "other")
    old = stream.new_session(world["proj"], "other", "old")
    (old / "input").mkdir(exist_ok=True)
    (old / "input" / "handoff-seed.md").write_text("# seed\n", encoding="utf-8")
    gave = hq.offer(world["home"], "other", arc="{0}/{1}".format(other.name, old.name),
                    project="proj", seed=str(old / "input" / "handoff-seed.md"),
                    from_session=SID)
    hq.take(world["home"], gave.stem, session="someone-else")
    fields.set_field(gave, "taken-at", "2026-08-03T12:26:15")
    mine = stream.new_session(world["proj"], "seam", "seam-2")
    (mine / "input").mkdir(exist_ok=True)
    (mine / "input" / "handoff-seed.md").write_text("# seed\n", encoding="utf-8")
    got = hq.offer(world["home"], "seam", arc="seam/seam-2", project="proj",
                   seed=str(mine / "input" / "handoff-seed.md"), from_session="prev")
    hq.take(world["home"], got.stem, session=SID)
    assert hq.is_dissolved(world["home"], SID) is None
    reason = cw.watch_reason(world["proj"], _payload(world, 260_000))
    assert reason is None or "нить передана" not in reason
    assert not (fields.read_field(world["sess"] / "arc.md", cw.HANDED_TOLD_FIELD) or "")
