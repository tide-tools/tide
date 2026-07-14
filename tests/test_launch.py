"""launch_session — the ONE spawn path: sid before birth, reserve not take, record."""

from __future__ import annotations

from pathlib import Path

from tide import fields, handoff_queue, registry
from tide.adapters.base import SpawnResult, TerminalAdapter
from tide.arc import stream
from tide.launcher.launch import launch_session


class _FakeAdapter(TerminalAdapter):
    name = "fake"

    def __init__(self, *, ok=True):
        self._ok = ok
        self.spawned = None

    def spawn(self, *, command, cwd, title="tide", dry_run=False):
        self.spawned = {"command": command, "cwd": cwd, "title": title}
        if not self._ok:
            return SpawnResult(ok=False, detail="fake failure")
        return SpawnResult(ok=True, ref="term_x", detail="fake spawn")


def _pickup_fixture(tmp_path):
    """A control-home-ish root with a project, a thread, a seeded session + offer."""
    proj = tmp_path / "proj"
    (proj / ".tide" / "arcs").mkdir(parents=True)
    stream.new_thread(proj, "demo", goal="ship it")
    sess = stream.new_session(proj, "demo", "pickup")
    seed = sess / "input" / "handoff-seed.md"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text("# distil\n", encoding="utf-8")
    home = tmp_path / "home"
    (home / ".tide" / "handoffs").mkdir(parents=True)
    handoff_queue.offer(home, "launcher", arc="demo/pickup", project="proj",
                        seed=str(seed), from_session="origin-sid")
    key = handoff_queue.list_offers(home)[0]["name"]
    return home, proj, sess, seed, key


def test_pickup_pins_sid_reserves_and_records(tmp_path):
    home, proj, sess, seed, key = _pickup_fixture(tmp_path)
    adapter = _FakeAdapter()
    res = launch_session(
        home, project=proj, session_dir=sess, adapter=adapter,
        seed_file=str(seed), trigger="go", title="t", handoff_key=key,
    )
    assert res.ok
    sid = fields.read_field(sess / "arc.md", "claude-session")
    assert sid and not sid.startswith("<")
    # sid минтит tide и он же уходит в argv — никогда не вычитывается назад из claude
    joined = " ".join(adapter.spawned["command"])
    assert "--session-id {0}".format(sid) in joined
    # подпись A: оффер ЗАРЕЗЕРВИРОВАН за sid, но статус остаётся offered до первого хода
    rec = handoff_queue.list_offers(home)[0]
    assert rec["status"] == handoff_queue.STATUS_OFFERED
    assert rec["pickup_session"] == sid
    # sid ориджина не наследуется никогда (cand 103)
    assert sid != "origin-sid"
    # лончер — единственный писатель реестра
    assert registry.recorded_handle(home, sid) == "term_x"
    # первый ход сессии флипает оффер (хук confirm_for_session) — механика на месте
    claimed = handoff_queue.confirm_for_session(home, sid)
    assert claimed and claimed["name"] == key
    assert handoff_queue.list_offers(home)[0]["status"] == handoff_queue.STATUS_TAKEN


def test_pickup_failed_spawn_leaves_offer_recoverable(tmp_path):
    home, proj, sess, seed, key = _pickup_fixture(tmp_path)
    adapter = _FakeAdapter(ok=False)
    res = launch_session(
        home, project=proj, session_dir=sess, adapter=adapter,
        seed_file=str(seed), trigger="go", title="t", handoff_key=key,
    )
    assert not res.ok
    rec = handoff_queue.list_offers(home)[0]
    assert rec["status"] == handoff_queue.STATUS_OFFERED  # оффер не съеден
    sid = fields.read_field(sess / "arc.md", "claude-session")
    assert registry.recorded_handle(home, sid) is None  # реестр не врёт


def test_fresh_launch_pins_sid_and_records(tmp_path):
    home, proj, sess, seed, key = _pickup_fixture(tmp_path)
    adapter = _FakeAdapter()
    res = launch_session(
        home, project=proj, session_dir=sess, adapter=adapter,
        arc_ref="pickup", arc_text="# passport", thread_name="demo",
        trigger="", title="demo · pickup",
    )
    assert res.ok
    sid = fields.read_field(sess / "arc.md", "claude-session")
    assert registry.recorded_handle(home, sid) == "term_x"
    assert adapter.spawned["cwd"] == str(proj)


def test_dry_run_touches_nothing_shared(tmp_path):
    home, proj, sess, seed, key = _pickup_fixture(tmp_path)
    adapter = _FakeAdapter()
    launch_session(
        home, project=proj, session_dir=sess, adapter=adapter,
        seed_file=str(seed), trigger="go", title="t", handoff_key=key, dry_run=True,
    )
    rec = handoff_queue.list_offers(home)[0]
    assert rec["pickup_session"] in ("", "-", None)  # без резерва на dry-run
    reg = registry.read(home)
    assert reg == {}  # и без записи в реестр


def test_pickup_never_inherits_a_stale_pin(tmp_path):
    # e2e 14.07: the pickup session's passport arrived pre-pinned with a FOREIGN sid
    # (the creator's own, stamped at birth) — trusting it spawned claude onto an id
    # already in use and it died on boot. A pickup always mints fresh and re-pins.
    home, proj, sess, seed, key = _pickup_fixture(tmp_path)
    fields.set_field(sess / "arc.md", "claude-session", "creator-own-sid")
    adapter = _FakeAdapter()
    res = launch_session(
        home, project=proj, session_dir=sess, adapter=adapter,
        seed_file=str(seed), trigger="go", title="t", handoff_key=key,
    )
    assert res.ok
    sid = fields.read_field(sess / "arc.md", "claude-session")
    assert sid != "creator-own-sid"
    assert "--session-id {0}".format(sid) in " ".join(adapter.spawned["command"])
    assert handoff_queue.list_offers(home)[0]["pickup_session"] == sid
