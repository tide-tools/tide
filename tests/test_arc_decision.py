"""unit — arc.decision: a thread's decision log (add/list), cand 128-B."""

from __future__ import annotations

import pytest

from tide.arc import decision, stream


def _nit(proj, slug="mynit"):
    stream.new_thread(proj, slug)
    return slug


def test_add_writes_header_and_record_born_open(tmp_project):
    nit = _nit(tmp_project)
    f, num, dslug = decision.add_decision(
        tmp_project, "закон 47 инлайнится в seed", thread_ref=nit,
        why="холодный агент не грепает исходник", closes="не грепать закон в доске",
        description="где резолвится закон 47",
    )
    assert num == "01"
    text = f.read_text(encoding="utf-8")
    assert text.startswith("# decisions — mynit")     # one-time header
    assert "## 01 — " in text
    assert "status: open" in text                      # born open
    assert "thread: mynit" in text
    assert "закон 47 инлайнится в seed" in text
    assert "closes: не грепать закон в доске" in text


def test_list_parses_records_back(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "решение раз", thread_ref=nit, why="потому")
    decision.add_decision(tmp_project, "решение два", thread_ref=nit)
    got = decision.list_decisions(tmp_project, thread_ref=nit)
    assert [d["num"] for d in got] == ["01", "02"]      # NN increments in-file
    assert got[0]["what"] == "решение раз"
    assert got[0]["why"] == "потому"
    assert got[0]["status"] == "open"
    assert got[1]["what"] == "решение два"
    assert got[1]["why"] == "—"                         # unset renders as em-dash


def test_missing_fields_render_as_dash(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "голое решение", thread_ref=nit)
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert d["work"] == "—" and d["closes"] == "—" and d["description"] == "—"


def test_empty_thread_has_no_decisions(tmp_project):
    nit = _nit(tmp_project)
    assert decision.list_decisions(tmp_project, thread_ref=nit) == []
    assert decision.render_list(tmp_project, thread_ref=nit) == "(no decisions)"


def test_unknown_thread_raises(tmp_project):
    with pytest.raises(decision.DecisionError):
        decision.add_decision(tmp_project, "x", thread_ref="ghost-nit")


def test_empty_text_raises(tmp_project):
    nit = _nit(tmp_project)
    with pytest.raises(decision.DecisionError):
        decision.add_decision(tmp_project, "   ", thread_ref=nit)


def test_render_list_one_line_per_decision(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "первое", thread_ref=nit)
    out = decision.render_list(tmp_project, thread_ref=nit)
    assert out.startswith("01 ") and "· open ·" in out and "первое" in out


def test_explicit_slug_is_honoured(tmp_project):
    nit = _nit(tmp_project)
    _, _, dslug = decision.add_decision(
        tmp_project, "длинный текст решения который иначе стал бы слагом",
        thread_ref=nit, dslug="short-handle")
    assert dslug == "short-handle"


# --- lifecycle -------------------------------------------------------------

def test_settle_flips_status_and_writes_canon_journal(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "canon — это пол", thread_ref=nit, dslug="canon-is-floor")
    num, dslug = decision.settle(tmp_project, "canon-is-floor", thread_ref=nit)
    assert (num, dslug) == ("01", "canon-is-floor")
    # the record flips to settled…
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert d["status"] == "settled"
    # …and a line lands in the canon journal (the terminal section)
    from tide import paths
    canon = paths.canon_file(tmp_project).read_text(encoding="utf-8")
    assert "## Canon journal" in canon
    journal = canon.split("## Canon journal", 1)[1]
    assert "decision" in journal and "canon-is-floor" in journal and "canon — это пол" in journal


def test_settle_by_number_key(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "решение", thread_ref=nit)
    num, _ = decision.settle(tmp_project, "1", thread_ref=nit)  # bare NN resolves
    assert num == "01"
    assert decision.list_decisions(tmp_project, thread_ref=nit)[0]["status"] == "settled"


def test_supersede_marks_history_not_deletes(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "старое решение", thread_ref=nit, dslug="old")
    decision.add_decision(tmp_project, "новое решение", thread_ref=nit, dslug="new")
    decision.supersede(tmp_project, "old", by="02-new", thread_ref=nit)
    recs = {d["slug"]: d for d in decision.list_decisions(tmp_project, thread_ref=nit)}
    assert recs["old"]["status"] == "superseded"      # marked, not removed
    assert recs["new"]["status"] == "open"            # untouched
    text = decision.decisions_file(
        decision._thread_dir(tmp_project, nit)).read_text(encoding="utf-8")
    assert "superseded-by: 02-new" in text            # points forward


def test_settle_unknown_key_raises(tmp_project):
    nit = _nit(tmp_project)
    with pytest.raises(decision.DecisionError):
        decision.settle(tmp_project, "ghost", thread_ref=nit)


def test_supersede_requires_by(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "x", thread_ref=nit, dslug="x")
    with pytest.raises(decision.DecisionError):
        decision.supersede(tmp_project, "x", by="  ", thread_ref=nit)


# --- context injection (128-A, bounded per 131) ----------------------------

def test_context_render_shows_open_newest_first_hides_settled(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "первое решение", thread_ref=nit, dslug="one")
    decision.add_decision(tmp_project, "второе решение", thread_ref=nit, dslug="two")
    decision.settle(tmp_project, "one", thread_ref=nit)   # settled → in canon, not injected
    block = decision.render_open_for_context(tmp_project, nit)
    assert block.startswith(decision.CONTEXT_HEADER)
    assert "two — второе решение" in block
    assert "one" not in block                             # settled is hidden
    # newest-first: 'two' (02) appears before any older open decision would
    assert block.index("two") > block.index(decision.CONTEXT_HEADER)


def test_context_render_empty_when_no_open(tmp_project):
    nit = _nit(tmp_project)
    assert decision.render_open_for_context(tmp_project, nit) == ""   # no decisions → no block
    decision.add_decision(tmp_project, "d", thread_ref=nit, dslug="d")
    decision.settle(tmp_project, "d", thread_ref=nit)
    assert decision.render_open_for_context(tmp_project, nit) == ""   # only settled → no block


def test_context_render_caps_and_counts_overflow(tmp_project):
    nit = _nit(tmp_project)
    for i in range(10):
        decision.add_decision(tmp_project, "решение {0}".format(i), thread_ref=nit)
    block = decision.render_open_for_context(tmp_project, nit, cap=3)
    body = [ln for ln in block.splitlines() if ln.startswith("- ")]
    assert len(body) == 4                                  # 3 shown + 1 overflow line
    assert "(+7 ещё" in body[-1]                           # 10 open − 3 shown
