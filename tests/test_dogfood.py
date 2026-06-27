"""U13 dogfood — tide leads tide: the repo carries its own real ``.tide/``.

Unlike every other test (which works in a tmp ``.tide/``), this one reads the
**real** tide repo. The U13 build step ran the full loop on the repo itself —
``tide init`` → one arc → contract sign/report/proof/accept/close (canon merge)
→ ``arc close`` — so the repo is now a tide project led by tide. These assertions
pin that durable artifact: the dogfood arc must stay closed, merged, and clean.

If the repo's ``.tide/`` is ever removed these tests skip (the dogfood is a
committed artifact, not something the suite regenerates), so a fresh checkout
without it doesn't fail — but a present-yet-broken dogfood does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import paths, sync
from tide.contract import model

REPO_ROOT = Path(__file__).resolve().parents[1]
DOGFOOD_SLUG = "tide-leads-tide"
DOGFOOD_MARKER = "tide now leads tide"

_has_dogfood = paths.tide_dir(REPO_ROOT).is_dir()
needs_dogfood = pytest.mark.skipif(
    not _has_dogfood, reason="repo has no .tide/ dogfood (run U13 dogfood to create it)"
)


@needs_dogfood
def test_repo_is_its_own_tide_control_home():
    # tide dogfoods itself: the repo is a control-home (roster.md) AND a project.
    assert paths.is_control_home(REPO_ROOT)
    assert paths.canon_file(REPO_ROOT).is_file()


@needs_dogfood
def test_dogfood_arc_is_closed_on_disk():
    closed = paths.arcs_dir(REPO_ROOT) / "__01-{0}__".format(DOGFOOD_SLUG)
    assert closed.is_dir(), "the dogfood arc must be stream-closed (__…__)"
    # the on-disk dual-mark agrees: a closed dir reads as a done contract.
    assert model.read_state(closed) == model.CLOSE


@needs_dogfood
def test_dogfood_delta_is_merged_into_canon_journal():
    canon = paths.canon_file(REPO_ROOT).read_text(encoding="utf-8")
    assert "## Cannon journal" in canon
    assert DOGFOOD_MARKER in canon
    assert "· {0}".format(DOGFOOD_SLUG) in canon


@needs_dogfood
def test_dogfood_stream_has_no_unmerged_delta():
    # the merge gate consumed the delta → the between-arcs barrier is clear.
    assert sync.unmerged_deltas(REPO_ROOT) == []
