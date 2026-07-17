"""U1 unit — slug: slugify + __…__-tolerant ref matching."""

from __future__ import annotations

from tide import slug


def test_slugify_lowercases_and_dashes_separators():
    assert slug.slugify("Hello World") == "hello-world"
    assert slug.slugify("a/b_c d") == "a-b-c-d"


def test_slugify_drops_unsafe_chars():
    assert slug.slugify("Café #1 (draft)!") == "caf-1-draft"


def test_slugify_collapses_and_trims_dashes():
    assert slug.slugify("--Foo   Bar--") == "foo-bar"
    assert slug.slugify("a   /   b") == "a-b"


def test_slugify_empty_and_none_safe():
    assert slug.slugify("") == ""
    assert slug.slugify(None) == ""


def test_slugify_transliterates_cyrillic():
    # cand 127: a Russian-named thread must yield a real handle, not '' (which
    # threw "empty slug after slugify" at birth so the thread never started).
    assert slug.slugify("чай") == "chay"
    assert slug.slugify("Персонал") == "personal"
    assert slug.slugify("Здоровье и Финансы") == "zdorove-i-finansy"
    # convention match: ц→c, х→h, я→ya (миграция/переходы already on disk)
    assert slug.slugify("миграция") == "migraciya"
    assert slug.slugify("переходы") == "perehody"


def test_slugify_mixed_cyrillic_latin_survives():
    # a mixed name no longer loses its Cyrillic half to the [^a-z0-9-] strip.
    assert slug.slugify("чай-tea") == "chay-tea"
    # create/lookup stay in lock-step: a ref round-trips to its entry
    assert slug.ref_matches("чай", "07-chay") is True


def test_short_slug_passes_through_when_within_cap():
    assert slug.short_slug("fix the leak") == "fix-the-leak"
    assert slug.short_slug("") == ""


def test_short_slug_caps_long_text_on_word_boundary():
    long = "polish the settings screen with spring animations and haptics on slow devices"
    s = slug.short_slug(long, max_len=20)
    assert len(s) <= 20
    # trimmed back to a whole-word ('-') boundary, no trailing dash, no mid-word cut
    assert s == "polish-the-settings"


def test_short_slug_hard_cuts_a_single_overlong_word():
    s = slug.short_slug("a" * 100, max_len=10)
    assert s == "a" * 10


def test_strip_marker_one_layer():
    assert slug.strip_marker("__03-fix__") == "03-fix"
    assert slug.strip_marker("plain") == "plain"


def test_normalize_ref_strips_marker_then_slugifies():
    assert slug.normalize_ref("__Fix It__") == "fix-it"


def test_entry_slug_peels_num_goal_and_marker():
    assert slug.entry_slug("03-fix-bug") == "fix-bug"
    assert slug.entry_slug("07-@ship-it") == "ship-it"
    assert slug.entry_slug("__12-fix-bug__") == "fix-bug"
    assert slug.entry_slug("__09-@goal-x__/") == "goal-x"


def test_is_goal_and_is_closed_detect_markers():
    assert slug.is_goal_entry("07-@ship-it") is True
    assert slug.is_goal_entry("07-ship-it") is False
    assert slug.is_closed_entry("__12-fix__") is True
    assert slug.is_closed_entry("12-fix") is False


def test_is_entry_matches_open_and_closed_arcs_and_goals():
    assert slug.is_entry("03-fix-bug") is True       # open arc
    assert slug.is_entry("07-@ship-it") is True      # open goal
    assert slug.is_entry("__12-fix-bug__") is True   # closed arc
    assert slug.is_entry("__09-@goal-x__") is True   # closed goal
    assert slug.is_entry("candidates") is False      # not a NN- entry
    assert slug.is_entry("") is False


def test_ref_matches_marker_tolerant_both_sides():
    # bare ref against an open arc
    assert slug.ref_matches("fix-bug", "03-fix-bug") is True
    # __-wrapped ref against a closed goal entry
    assert slug.ref_matches("__fix-bug__", "__12-fix-bug__") is True
    # ref against a goal entry (@ stripped)
    assert slug.ref_matches("ship-it", "07-@ship-it") is True
    # non-match
    assert slug.ref_matches("fix-bug", "03-other") is False
