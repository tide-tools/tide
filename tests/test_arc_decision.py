"""unit — arc.decision: a thread's decision log (add/list), cand 128-B."""

from __future__ import annotations

import pytest

from tide.arc import decision, stream


def _nit(proj, slug="mynit"):
    stream.new_thread(proj, slug)
    return slug


def test_add_writes_header_and_record_born_accepted(tmp_project):
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
    assert "status: accepted" in text                  # in force from birth…
    assert "done: —" in text                           # …and nothing claimed yet
    assert "kind: commitment" in text                  # a promise, until told otherwise
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
    assert got[0]["status"] == "accepted"
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
    assert out.startswith("01 ") and "· accepted ·" in out and "первое" in out


def test_explicit_slug_is_honoured(tmp_project):
    nit = _nit(tmp_project)
    _, _, dslug = decision.add_decision(
        tmp_project, "длинный текст решения который иначе стал бы слагом",
        thread_ref=nit, dslug="short-handle")
    assert dslug == "short-handle"


# --- lifecycle -------------------------------------------------------------

def test_settle_marks_done_and_writes_canon_journal(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "canon — это пол", thread_ref=nit, dslug="canon-is-floor")
    num, dslug = decision.settle(tmp_project, "canon-is-floor", thread_ref=nit)
    assert (num, dslug) == ("01", "canon-is-floor")
    # settle is the canon DOOR: it stamps the done axis and leaves in-force alone
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert d["status"] == "accepted" and decision.is_done(d)
    assert "canon journal" in d["proof"]          # …and it says what showed it
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
    assert decision.is_done(decision.list_decisions(tmp_project, thread_ref=nit)[0])


def test_supersede_marks_history_not_deletes(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "старое решение", thread_ref=nit, dslug="old")
    decision.add_decision(tmp_project, "новое решение", thread_ref=nit, dslug="new")
    decision.supersede(tmp_project, "old", by="02-new", thread_ref=nit)
    recs = {d["slug"]: d for d in decision.list_decisions(tmp_project, thread_ref=nit)}
    assert recs["old"]["status"] == "superseded"      # marked, not removed
    assert recs["new"]["status"] == "accepted"        # untouched
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
    decision.settle(tmp_project, "one", thread_ref=nit)   # carried out → not injected
    block = decision.render_open_for_context(tmp_project, nit)
    assert block.startswith(decision.CONTEXT_HEADER)
    assert "two — второе решение" in block
    assert "01 one" not in block                          # carried out is hidden
    # newest-first: 'two' (02) appears before any older open decision would
    assert block.index("two") > block.index(decision.CONTEXT_HEADER)


def test_context_render_empty_when_no_open(tmp_project):
    nit = _nit(tmp_project)
    assert decision.render_open_for_context(tmp_project, nit) == ""   # no decisions → no block
    decision.add_decision(tmp_project, "d", thread_ref=nit, dslug="d")
    decision.settle(tmp_project, "d", thread_ref=nit)
    assert decision.render_open_for_context(tmp_project, nit) == ""   # only done → no block


def test_context_render_caps_and_counts_overflow(tmp_project):
    nit = _nit(tmp_project)
    for i in range(10):
        decision.add_decision(tmp_project, "решение {0}".format(i), thread_ref=nit)
    block = decision.render_open_for_context(tmp_project, nit, cap=3)
    body = [ln for ln in block.splitlines() if ln.startswith("- ")]
    assert len(body) == 4                                  # 3 shown + 1 overflow line
    assert "(+7 ещё" in body[-1]                           # 10 open − 3 shown


# --- two axes: in force (status) × carried out (done) — release decision 28 ---

def test_done_needs_a_work_or_a_proof_and_leaves_in_force_alone(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "доска едет в коробке", thread_ref=nit, dslug="box")
    with pytest.raises(decision.DecisionError):
        decision.mark_done(tmp_project, "box", "   ", thread_ref=nit)   # nothing to follow
    decision.mark_done(tmp_project, "box", "работа 48 · tide plugins", thread_ref=nit)
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert decision.is_done(d)
    assert d["status"] == "accepted"          # carried out and STILL binding
    assert d["proof"] == "работа 48 · tide plugins"
    assert d["done"].startswith("20")         # stamped with the day it landed


def test_done_by_work_records_the_owner_without_a_proof(tmp_project):
    """`work:` was in this format from day one and had never once been filled."""
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "решение", thread_ref=nit, dslug="d")
    decision.mark_done(tmp_project, "d", work="64", thread_ref=nit)
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert decision.owner(d) == "64" and decision.is_done(d)
    assert decision.unproved(d) is False


def test_accept_names_the_work_that_will_carry_a_promise(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "обещание", thread_ref=nit, dslug="p")
    decision.accept(tmp_project, "p", work="65", thread_ref=nit)
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert decision.owner(d) == "65"
    assert decision.is_done(d) is False       # owned is not the same as done
    assert decision.unowned(d) is False


def test_accept_unclaims_a_decision_marked_done_too_early(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "решение", thread_ref=nit, dslug="d")
    decision.mark_done(tmp_project, "d", "коммит", thread_ref=nit)
    decision.accept(tmp_project, "d", thread_ref=nit)
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert decision.is_done(d) is False and d["status"] == "accepted"


def test_a_standing_rule_is_never_unowned(tmp_project):
    """A rule takes effect on signature; demanding a work for it forever is noise."""
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "приёмка — живой проход", thread_ref=nit,
                          dslug="crit", rule=True)
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert decision.is_rule(d) and decision.unowned(d) is False
    assert d["kind"] == "rule"


def test_a_commitment_with_nobody_carrying_it_is_unowned(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "обещание", thread_ref=nit, dslug="p")
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert decision.unowned(d) is True        # born a commitment, born unowned


def test_accept_can_reclassify_a_record_as_a_standing_rule(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "критерий", thread_ref=nit, dslug="c")
    decision.accept(tmp_project, "c", rule=True, thread_ref=nit)
    assert decision.is_rule(decision.list_decisions(tmp_project, thread_ref=nit)[0])


def test_drop_requires_a_reason_and_stays_as_history(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "снимаемое", thread_ref=nit, dslug="gone")
    with pytest.raises(decision.DecisionError):
        decision.drop(tmp_project, "gone", "", thread_ref=nit)
    decision.drop(tmp_project, "gone", "вопрос перестал задаваться", thread_ref=nit)
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert d["status"] == "dropped" and decision.in_force(d) is False
    assert d["proof"] == "вопрос перестал задаваться"
    assert d["what"] == "снимаемое"           # the record itself survives
    assert decision.unowned(d) is False       # retired promises owe nobody


def test_pre_two_axis_logs_still_answer_both_questions(tmp_project):
    """`open` and `settled` predate the split; neither log gets rewritten."""
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "старое", thread_ref=nit, dslug="old")
    decision.add_decision(tmp_project, "осевшее", thread_ref=nit, dslug="set")
    f = decision.decisions_file(decision._thread_dir(tmp_project, nit))
    text = f.read_text(encoding="utf-8")
    text = text.replace("status: accepted", "status: open", 1)
    text = text.replace("status: accepted", "status: settled", 1)
    f.write_text(text, encoding="utf-8")
    old, settled = decision.list_decisions(tmp_project, thread_ref=nit)
    assert old["status"] == "accepted" and decision.is_done(old) is False
    assert settled["status"] == "accepted" and decision.is_done(settled) is True


def test_done_with_neither_work_nor_proof_on_disk_is_unproved(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "заявлено", thread_ref=nit, dslug="claim")
    f = decision.decisions_file(decision._thread_dir(tmp_project, nit))
    f.write_text(f.read_text(encoding="utf-8").replace("done: —", "done: 2026-09-01"),
                 encoding="utf-8")
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert decision.is_done(d) and decision.unproved(d) is True


def test_lines_are_grown_on_a_record_that_predates_the_fields(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "древнее", thread_ref=nit, dslug="ancient")
    f = decision.decisions_file(decision._thread_dir(tmp_project, nit))
    f.write_text("\n".join(ln for ln in f.read_text(encoding="utf-8").splitlines()
                           if not ln.startswith(("proof:", "done:", "kind:"))) + "\n",
                 encoding="utf-8")
    decision.mark_done(tmp_project, "ancient", "коммит abc123", thread_ref=nit)
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert d["proof"] == "коммит abc123" and decision.is_done(d)
    assert d["what"] == "древнее"


def test_proof_text_with_regex_metachars_lands_verbatim(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "решение", thread_ref=nit, dslug="meta")
    decision.mark_done(tmp_project, "meta", r"grep '^\1.*$' — C:\tmp", thread_ref=nit)
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert d["proof"] == r"grep '^\1.*$' — C:\tmp"


def test_list_filters_on_both_axes(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "первое", thread_ref=nit, dslug="one")
    decision.add_decision(tmp_project, "второе", thread_ref=nit, dslug="two")
    decision.add_decision(tmp_project, "правило", thread_ref=nit, dslug="r", rule=True)
    decision.mark_done(tmp_project, "one", "коммит", thread_ref=nit)
    assert "one" in decision.render_list(tmp_project, thread_ref=nit, state="done")
    not_done = decision.render_list(tmp_project, thread_ref=nit, state="not-done")
    assert "two" in not_done and "01 one" not in not_done
    unowned = decision.render_list(tmp_project, thread_ref=nit, state="unowned")
    assert "two" in unowned and " r " not in unowned      # a rule owes nobody
    with pytest.raises(decision.DecisionError):
        decision.render_list(tmp_project, thread_ref=nit, state="выполнено")


def test_settle_refusal_leaves_the_record_untouched(tmp_project):
    """No canon → no promotion, and no half-promoted record either."""
    from tide import paths

    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "решение", thread_ref=nit, dslug="d")
    paths.canon_file(tmp_project).unlink()
    with pytest.raises(decision.DecisionError):
        decision.settle(tmp_project, "d", thread_ref=nit)
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert d["status"] == "accepted" and decision.is_done(d) is False


def test_closes_listing_prints_what_each_decision_settled(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "решение", thread_ref=nit, dslug="d",
                          closes="спор «а или б» — не перерешивать")
    decision.add_decision(tmp_project, "без closes", thread_ref=nit, dslug="bare")
    out = decision.render_list(tmp_project, thread_ref=nit, closes=True)
    assert "closes: спор «а или б» — не перерешивать" in out
    assert "bare" not in out             # a record that settled nothing says nothing


def test_every_touched_record_leaves_with_the_full_shape(tmp_project):
    """A field present on some records and absent on others reads as meaningful."""
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "древнее", thread_ref=nit, dslug="old")
    f = decision.decisions_file(decision._thread_dir(tmp_project, nit))
    f.write_text("\n".join(ln for ln in f.read_text(encoding="utf-8").splitlines()
                           if not ln.startswith("kind:")) + "\n", encoding="utf-8")
    decision.accept(tmp_project, "old", thread_ref=nit)
    assert "kind: commitment" in f.read_text(encoding="utf-8")


def test_a_plain_accept_never_demotes_a_standing_rule(tmp_project):
    nit = _nit(tmp_project)
    decision.add_decision(tmp_project, "критерий", thread_ref=nit, dslug="c", rule=True)
    decision.accept(tmp_project, "c", work="65", thread_ref=nit)   # no --rule
    d = decision.list_decisions(tmp_project, thread_ref=nit)[0]
    assert decision.is_rule(d) and decision.owner(d) == "65"
