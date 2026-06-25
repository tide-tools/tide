"""U13 e2e — the full tide loop, driven end-to-end through the real CLI parser.

One smoke test walks the whole machine exactly as a human + worker would:

    tide init (control-home) → roster add demo → in demo:
      arc new a1 (cannon-rev stamped)
      → mock worker writes output/ + a non-empty delta.md
      → contract new/sign/report/proof/accept
      → arc close a1 (stream)  ⇒ a1 is a CLOSED arc carrying an unmerged delta
      → arc new a2 BLOCKS (between-arcs barrier, decision 9)
      → contract close a1 merges the delta into CANON.md (cannon-rev bumps)
      → arc new a2 now OPENS (barrier lifted)
      → drift_check on a1 reports NO self-drift (F3: close re-stamped its arc.md
        to the post-merge rev — the authoring arc never drifts against its canon)
    asserts: CANON.md journal carries the merged delta body; the board shows
    no UNMERGED-DELTAS flag once the gate has run.

Everything goes through ``cli.main`` (true end-to-end), not the module funcs, so
the argparse wiring + role gate + cwd-resolution are all exercised together.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import cli, fields, paths, sync
from tide.cannon import rev, store

from tests.conftest import strip_placeholders

DELTA_MARKER = "valve swapped for a brass fitting — the durable truth"


def _seed_home_and_demo(tmp_path: Path):
    """A control-home and a separate demo project, both empty dirs to init into."""
    home = tmp_path / "home"
    demo = tmp_path / "demo"
    home.mkdir()
    demo.mkdir()
    return home, demo


def test_full_loop_init_roster_arc_contract_merge_block_drift(tmp_path, monkeypatch, capsys):
    home, demo = _seed_home_and_demo(tmp_path)
    monkeypatch.setenv("TIDE_ROLE", "orchestrator")

    # --- tide init (control-home) + roster add demo --------------------------
    monkeypatch.chdir(home)
    assert cli.main(["init", "--name", "home"]) == 0
    assert paths.is_control_home(home)
    assert cli.main(["roster", "add", "demo", str(demo)]) == 0
    capsys.readouterr()

    # --- enter the demo project, scaffold its .tide/, loose dial -------------
    monkeypatch.chdir(demo)
    assert cli.main(["init", "--project", "--name", "demo"]) == 0
    assert cli.main(["strictness", "loose"]) == 0
    capsys.readouterr()

    r0 = rev.compute(demo)

    # --- arc new a1 (cannon-rev stamped) -------------------------------------
    assert cli.main(["arc", "new", "a1"]) == 0
    a1 = paths.arcs_dir(demo) / "01-a1"
    assert a1.is_dir()
    assert fields.read_field(a1 / "arc.md", "cannon-rev") == r0

    # --- mock worker: write output/ + a non-empty unmerged delta -------------
    (a1 / "output" / "result.md").write_text("a1 done\n", encoding="utf-8")
    (a1 / "delta.md").write_text(
        "# delta — a1\nmerged: no\n\n{0}\n".format(DELTA_MARKER), encoding="utf-8"
    )

    # --- contract new → sign → report → proof → accept -----------------------
    assert cli.main(["contract", "new", "a1", "--goal", "close the leak"]) == 0
    assert cli.main(["contract", "sign", "a1"]) == 0
    assert cli.main(["contract", "report", "a1", "replaced", "the", "valve"]) == 0
    assert cli.main(["contract", "proof", "a1", "no", "more", "drip"]) == 0
    assert cli.main(["contract", "accept", "a1"]) == 0
    # F5: the worker fills the passport (arc.md + contract.md) before close.
    strip_placeholders(a1 / "arc.md", a1 / "contract.md")
    capsys.readouterr()

    # --- arc close a1 (stream): now a CLOSED arc with an unmerged delta -------
    assert cli.main(["arc", "close", "a1"]) == 0
    a1_closed = paths.arcs_dir(demo) / "__01-a1__"
    assert a1_closed.is_dir()
    assert sync.unmerged_deltas(demo) == [a1_closed]
    capsys.readouterr()

    # --- arc new a2 BLOCKS while a1's delta is unmerged -----------------------
    rc = cli.main(["arc", "new", "a2"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unmerged" in err
    assert not (paths.arcs_dir(demo) / "02-a2").exists()

    # --- contract close a1: merge the delta into CANON.md (cannon-rev bumps) --
    assert cli.main(["contract", "close", "a1"]) == 0
    capsys.readouterr()
    r1 = rev.compute(demo)
    assert r1 != r0
    # journal carries the merged delta body + a stamped slug heading.
    canon_text = store.read(demo)
    assert DELTA_MARKER in canon_text
    assert "## Cannon journal" in canon_text
    assert "· a1" in canon_text
    # the gate consumed the offender → barrier is clear.
    assert sync.unmerged_deltas(demo) == []

    # --- arc new a2 now OPENS (barrier lifted), stamped at the NEW rev --------
    assert cli.main(["arc", "new", "a2"]) == 0
    a2 = paths.arcs_dir(demo) / "02-a2"
    assert a2.is_dir()
    assert fields.read_field(a2 / "arc.md", "cannon-rev") == r1  # no drift on a2

    # --- F3: the just-merged a1 was re-stamped to the post-merge rev ----------
    # contract close seals + re-stamps, so the arc that AUTHORED this canon does
    # NOT self-drift against it.
    assert fields.read_field(a1_closed / "arc.md", "cannon-rev") == r1
    drift = sync.drift_check(a1_closed, demo)
    assert drift.drifted is False
    assert drift.stamped == r1
    assert drift.current == r1

    # --- board shows no UNMERGED-DELTAS flag ---------------------------------
    assert cli.main(["status"]) == 0
    board = capsys.readouterr().out
    assert "STREAM" in board
    assert "UNMERGED DELTAS" not in board


def test_loop_block_is_a_hard_refusal_not_a_silent_skip(tmp_path, monkeypatch, capsys):
    """A worker (non-orchestrator) hitting the barrier still gets a nonzero refusal."""
    demo = tmp_path / "demo"
    demo.mkdir()
    monkeypatch.chdir(demo)
    monkeypatch.setenv("TIDE_ROLE", "orchestrator")
    cli.main(["init", "--project", "--name", "demo"])
    capsys.readouterr()

    cli.main(["arc", "new", "a1"])
    a1 = paths.arcs_dir(demo) / "01-a1"
    (a1 / "output" / "r.md").write_text("x", encoding="utf-8")
    (a1 / "delta.md").write_text("# delta — a1\nmerged: no\n\nreal body\n", encoding="utf-8")
    strip_placeholders(a1 / "arc.md")  # F5: fill the passport before close
    cli.main(["arc", "close", "a1"])
    capsys.readouterr()

    # a worker cannot sneak a new arc past the unmerged-delta barrier.
    monkeypatch.setenv("TIDE_ROLE", "worker")
    assert cli.main(["arc", "new", "a2"]) == 1
    assert "unmerged" in capsys.readouterr().err
