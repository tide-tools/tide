"""U1 unit — fields: first-line ^key: read/write, prev: alias, order preserved."""

from __future__ import annotations

from tide import fields

ARC_MD = """# 03-fix-bug

goal: stop the leak
status: active
cannon-rev: abc123

## input
some prose with a colon: not a field
"""


def test_read_field_first_line_after_colon():
    assert fields.read_field_text(ARC_MD, "goal") == "stop the leak"
    assert fields.read_field_text(ARC_MD, "status") == "active"
    assert fields.read_field_text(ARC_MD, "cannon-rev") == "abc123"


def test_read_field_missing_returns_none():
    assert fields.read_field_text(ARC_MD, "contract") is None


def test_read_field_ignores_prose_colon_lines():
    # 'some prose with a colon: not a field' must NOT register as key 'some prose'.
    assert fields.read_field_text(ARC_MD, "some prose with a colon") is None


def test_prev_is_read_alias_of_supersedes():
    doc = "# t\n\ngoal: g\nstatus: active\nprev: old-arc\n"
    assert fields.read_field_text(doc, "supersedes") == "old-arc"
    assert fields.read_field_text(doc, "prev") == "old-arc"


def test_read_supersedes_strips_double_underscore():
    doc = "# t\n\nstatus: done\nsupersedes: __old__\n"
    assert fields.read_field_text(doc, "supersedes") == "old"


def test_set_field_replaces_in_place_preserving_order():
    out = fields.set_field_text(ARC_MD, "status", "done")
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    # order: goal, status, cannon-rev — status stays in slot 2
    assert lines[0] == "goal: stop the leak"
    assert lines[1] == "status: done"
    assert lines[2] == "cannon-rev: abc123"


def test_set_field_new_key_appended_to_frontmatter_block():
    # Use a real known key ("slug") — the whitelist introduced in the durability
    # hardening pass restricts read_field_text to KNOWN_KEYS, so "contract" (a
    # CLI sub-command name, not a real field key) would not round-trip.  The
    # insertion-ordering behavior under test is the same regardless of which
    # whitelisted key is used.
    out = fields.set_field_text(ARC_MD, "slug", "c-07")
    assert fields.read_field_text(out, "slug") == "c-07"
    # existing fields keep their values/order
    assert fields.read_field_text(out, "goal") == "stop the leak"
    assert fields.read_field_text(out, "cannon-rev") == "abc123"
    # inserted inside the frontmatter block, before the body heading
    assert out.index("slug: c-07") < out.index("## input")


def test_set_field_supersedes_replaces_prev_line_in_place():
    doc = "# t\n\ngoal: g\nprev: old-arc\nstatus: active\n"
    out = fields.set_field_text(doc, "supersedes", "new-old")
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    # prev: line is rewritten as canonical supersedes:, IN ITS ORIGINAL SLOT
    assert lines[0] == "goal: g"
    assert lines[1] == "supersedes: new-old"
    assert lines[2] == "status: active"
    assert "prev:" not in out


def test_set_field_supersedes_strips_marker_from_value():
    out = fields.set_field_text("# t\n\nstatus: active\n", "supersedes", "__old__")
    assert fields.read_field_text(out, "supersedes") == "old"
    assert "supersedes: old" in out


def test_set_field_preserves_trailing_newline():
    assert fields.set_field_text(ARC_MD, "status", "done").endswith("\n")
    assert not fields.set_field_text("status: a", "status", "b").endswith("\n")
