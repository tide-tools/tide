"""U4 unit — candidates backlog: capture / list / promote (move-into-input/)."""

from __future__ import annotations

import pytest

from tide import fields, paths
from tide.arc import candidate, stream


# --- capture ---------------------------------------------------------------

def test_new_candidate_writes_own_numbered_file(tmp_project):
    path = candidate.new_candidate(tmp_project, "fix the leak")
    assert path.name == "01-fix-the-leak.md"
    assert path.parent == paths.candidates_dir(tmp_project)
    assert path.is_file()


def test_new_candidate_records_from_origin(tmp_project):
    path = candidate.new_candidate(tmp_project, "idea", from_arc="alpha")
    assert fields.read_field(path, "from") == "alpha"


def test_new_candidate_default_from_is_dash(tmp_project):
    path = candidate.new_candidate(tmp_project, "idea")
    assert fields.read_field(path, "from") == "-"


def test_new_candidate_writes_body(tmp_project):
    path = candidate.new_candidate(tmp_project, "idea", body="batch the writes")
    assert "batch the writes" in path.read_text(encoding="utf-8")


def test_new_candidate_stamps_dropped(tmp_project):
    # candidate 89: the board's age must be honest from birth, so the drop time is
    # stamped in the file (not left to FS times, which a rename-editor resets).
    from datetime import datetime

    when = datetime(2026, 7, 13, 11, 38)
    path = candidate.new_candidate(tmp_project, "idea", now=when)
    assert fields.read_field(path, "dropped") == "2026-07-13 11:38"


def test_new_candidate_dropped_matches_deck_parse_format(tmp_project):
    # the stamp must parse with the deck's primary format (``%Y-%m-%d %H:%M``),
    # else the board silently falls back to lying FS times.
    from datetime import datetime

    path = candidate.new_candidate(tmp_project, "idea")
    stamp = fields.read_field(path, "dropped")
    datetime.strptime(stamp, "%Y-%m-%d %H:%M")  # raises if the format drifted


def test_new_candidate_body_falls_back_to_title(tmp_project):
    # fix F6: with no explicit body, the full title text is persisted in the body
    # (not just encoded into the slug) so the idea survives.
    path = candidate.new_candidate(tmp_project, "batch the writes on flush")
    text = path.read_text(encoding="utf-8")
    assert "batch the writes on flush" in text
    # and the placeholder is NOT used when we have real text
    assert "<one line" not in text


def test_new_candidate_caps_long_slug_keeps_idea_in_body(tmp_project):
    # fix F6: a pasted idea must not become a 200-char filename; the slug is a
    # short capped handle while the full idea lives in the body.
    long_idea = (
        "polish the settings screen with spring animations and a haptic tap "
        "when the user toggles dark mode on slow devices"
    )
    path = candidate.new_candidate(tmp_project, long_idea)
    # slug stem is short (NN- prefix + capped slug)
    stem_slug = path.stem.split("-", 1)[1]
    assert len(stem_slug) <= 48
    # full idea preserved in the body
    assert long_idea in path.read_text(encoding="utf-8")
    # the file is still a valid candidate (re-discoverable on the board)
    items = candidate.list_candidates(tmp_project)
    assert items and items[0]["path"] == path


def test_candidate_counter_is_separate_from_arc_stream(tmp_project):
    # An arc consumes 01 in the work stream; the candidate still starts at 01.
    stream.new_arc(tmp_project, "real-arc")
    c = candidate.new_candidate(tmp_project, "an-idea")
    assert c.name == "01-an-idea.md"


def test_candidate_numbering_is_continuous(tmp_project):
    a = candidate.new_candidate(tmp_project, "one")
    b = candidate.new_candidate(tmp_project, "two")
    assert a.name == "01-one.md"
    assert b.name == "02-two.md"


def test_new_candidate_empty_slug_raises(tmp_project):
    with pytest.raises(candidate.CandidateError):
        candidate.new_candidate(tmp_project, "!!!")


# --- list ------------------------------------------------------------------

def test_list_candidates_returns_entries(tmp_project):
    candidate.new_candidate(tmp_project, "one", from_arc="alpha")
    candidate.new_candidate(tmp_project, "two")
    items = candidate.list_candidates(tmp_project)
    assert [it["stem"] for it in items] == ["01-one", "02-two"]
    assert items[0]["from"] == "alpha"


def test_render_list_empty(tmp_project):
    assert candidate.render_list(tmp_project) == "(no candidates)"


def test_render_list_shows_slug_and_origin(tmp_project):
    candidate.new_candidate(tmp_project, "batch-writes", from_arc="alpha")
    rendered = candidate.render_list(tmp_project)
    assert "01-batch-writes" in rendered
    assert "alpha" in rendered


# --- promote ---------------------------------------------------------------

def test_promote_creates_arc_and_moves_file_into_input(tmp_project):
    candidate.new_candidate(tmp_project, "batch-writes", from_arc="alpha", body="do it")
    entry = candidate.promote(tmp_project, "batch-writes")
    # arc created in the work stream
    assert entry.name == "01-batch-writes"
    assert (entry / "arc.md").is_file()
    # candidate file MOVED into input/ (seed), origin + body preserved
    seed = entry / "input" / "01-batch-writes.md"
    assert seed.is_file()
    assert fields.read_field(seed, "from") == "alpha"
    assert "do it" in seed.read_text(encoding="utf-8")
    # cleared from candidates/
    assert not (paths.candidates_dir(tmp_project) / "01-batch-writes.md").exists()


def test_promote_resolves_by_number(tmp_project):
    candidate.new_candidate(tmp_project, "alpha")
    entry = candidate.promote(tmp_project, "01")
    assert entry.name == "01-alpha"
    assert (entry / "input" / "01-alpha.md").is_file()


def test_promote_resolves_by_full_stem(tmp_project):
    candidate.new_candidate(tmp_project, "alpha")
    entry = candidate.promote(tmp_project, "01-alpha")
    assert entry.name == "01-alpha"


def test_promote_with_new_slug_renames_arc(tmp_project):
    candidate.new_candidate(tmp_project, "rough-idea")
    entry = candidate.promote(tmp_project, "rough-idea", new_slug="polished plan")
    assert entry.name == "01-polished-plan"
    # the moved seed keeps its candidate filename
    assert (entry / "input" / "01-rough-idea.md").is_file()


def test_promote_into_goal_substream(tmp_project):
    stream.new_goal(tmp_project, "ship")
    candidate.new_candidate(tmp_project, "wire-api")
    entry = candidate.promote(tmp_project, "wire-api", goal_slug="ship")
    assert entry.parent.parent.name == "01-@ship"
    assert entry.name == "01-wire-api"
    assert (entry / "input" / "01-wire-api.md").is_file()
    assert not (paths.candidates_dir(tmp_project) / "01-wire-api.md").exists()


def test_promote_unknown_key_raises(tmp_project):
    with pytest.raises(candidate.CandidateError):
        candidate.promote(tmp_project, "ghost")


def test_promoted_candidate_leaves_the_open_backlog(tmp_project):
    # fix F6: once promoted, a candidate must not be re-advertised on the board.
    candidate.new_candidate(tmp_project, "batch-writes")
    candidate.new_candidate(tmp_project, "ship-it")
    candidate.promote(tmp_project, "batch-writes")
    items = candidate.list_candidates(tmp_project)
    slugs = [it["slug"] for it in items]
    assert "batch-writes" not in slugs  # gone from the backlog
    assert "ship-it" in slugs  # untouched candidate still listed
    assert "batch-writes" not in candidate.render_list(tmp_project)
