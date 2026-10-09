"""tide.context_fill — fill per turn, compactions, window guess, AO journal tail.

All transcripts and journals here are scratch files under tmp_path."""

import json

from tide import context_fill as cf


def _asst(mid, inp, cw, cr, model="claude-opus-5-5", ts="2026-10-09T00:00:00Z", side=False):
    return {"type": "assistant", "isSidechain": side, "timestamp": ts,
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": inp, "cache_creation_input_tokens": cw,
                                  "cache_read_input_tokens": cr, "output_tokens": 50}}}


def _compact(pre, post):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-09T01:00:00Z",
            "compactMetadata": {"trigger": "auto", "preTokens": pre, "postTokens": post}}


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n",
                    encoding="utf-8")
    return path


def test_fill_of_sums_three_inputs_and_ignores_output():
    assert cf.fill_of({"input_tokens": 2, "cache_creation_input_tokens": 30,
                       "cache_read_input_tokens": 100, "output_tokens": 999}) == 132
    assert cf.fill_of(None) == 0
    assert cf.fill_of({"input_tokens": "x"}) == 0


def test_split_reply_counts_once_and_sidechain_is_skipped(tmp_path):
    t = _write(tmp_path / "p" / "s1.jsonl", [
        {"type": "user", "message": {"content": "hi"}},
        _asst("m1", 2, 1000, 0),
        _asst("m1", 2, 1000, 0),            # same reply, second content block
        _asst("sub", 5, 90000, 0, side=True),  # a subagent's own context
        _asst("m2", 1, 500, 1000),
        _asst("syn", 0, 0, 0, model="<synthetic>"),
    ])
    res = cf.context_fill(t)
    assert res["turns"] == 2
    assert res["tokens"] == 1501
    assert res["window"] == cf.WINDOW_1M          # opus-5-5 runs on 1M by default
    assert res["percent"] == 0.2
    assert res["sid"] == "s1"


def test_compaction_marks_the_drop_and_proves_1m_window(tmp_path):
    t = _write(tmp_path / "p" / "s2.jsonl", [
        _asst("a", 1, 100000, 0),
        _asst("b", 1, 0, 400000),
        _compact(420000, 20000),
        _asst("c", 1, 25000, 0),
    ])
    s = cf.context_series(t)
    assert s["window"] == cf.WINDOW_1M
    assert s["peak"] == 400001
    assert s["tokens"] == 25001
    assert s["compacts"] == 1
    assert s["compact_marks"][0]["turn"] == 3   # first turn after the compaction
    assert s["compact_marks"][0]["pre"] == 420000
    assert [p[0] for p in s["points"]] == [1, 2, 3]


def test_compaction_after_last_reply_reports_post_size(tmp_path):
    t = _write(tmp_path / "p" / "s3.jsonl", [_asst("a", 1, 150000, 0), _compact(150001, 9000)])
    assert cf.context_fill(t)["tokens"] == 9000


def test_window_from_model_suffix_and_cost_state(tmp_path):
    assert cf.window_for("claude-sonnet-4-5[1m]", 10) == cf.WINDOW_1M
    assert cf.window_for("claude-sonnet-4-5", 10) == cf.WINDOW_DEFAULT
    t = _write(tmp_path / "p" / "s4.jsonl", [
        _asst("a", 1, 1000, 0, model="claude-sonnet-4-5"),
        {"type": "cost-state", "modelUsage": {"claude-sonnet-4-5[1m]": {}}},
    ])
    assert cf.context_fill(t)["window"] == cf.WINDOW_1M
    # an explicit window (AO journal) beats the guess
    assert cf.context_fill(t, window=500)["percent"] == 200.2


def test_window_from_model_id_without_suffix(tmp_path):
    # the transcript says only "claude-opus-5-5" — the default window of the model decides
    for model in ("claude-opus-5-5", "claude-opus-5", "claude-opus-4-6",
                  "claude-sonnet-5-5", "claude-sonnet-4-6", "claude-haiku-5-5",
                  "claude-fable-5-1", "claude-fable-5"):
        assert cf.window_for(model, 10) == cf.WINDOW_1M, model
    for model in ("claude-haiku-4-5-20251001", "claude-haiku-4-5", "claude-sonnet-4-5",
                  "claude-opus-4-5", "claude-3-7-sonnet-20250219", "", "<synthetic>"):
        assert cf.window_for(model, 10) == cf.WINDOW_DEFAULT, model
    # a 200k model that already went past 200k still proves 1M
    assert cf.window_for("claude-haiku-4-5", 250000) == cf.WINDOW_1M
    t = _write(tmp_path / "p" / "s9.jsonl", [_asst("a", 1, 150000, 0)])
    res = cf.context_fill(t)
    assert res["window"] == cf.WINDOW_1M and res["percent"] == 15.0
    t2 = _write(tmp_path / "p" / "s10.jsonl",
                [_asst("a", 1, 150000, 0, model="claude-haiku-4-5-20251001")])
    assert cf.context_fill(t2)["window"] == cf.WINDOW_DEFAULT


def test_sid_lookup_and_thinning_keep_compaction_edges(tmp_path):
    rows = [_asst("m%d" % i, 1, 1000 + i, 0) for i in range(500)]
    rows.insert(301, _compact(2000, 10))
    _write(tmp_path / "proj-a" / "abc-1.jsonl", rows)
    assert cf.transcript_for("abc-1", tmp_path).name == "abc-1.jsonl"
    assert cf.transcript_for("nope", tmp_path) is None
    assert cf.context_fill("nope", tmp_path) is None
    s = cf.context_series("abc-1", tmp_path, max_points=40)
    turns = [p[0] for p in s["points"]]
    assert len(turns) <= 44
    assert turns[0] == 1 and turns[-1] == 500
    assert 301 in turns and 302 in turns   # both sides of the drop survive


def test_ao_journal_usage_reads_last_update(tmp_path):
    host = tmp_path / "chat-hosts" / "proj-1"
    host.mkdir(parents=True)

    def upd(used, size):
        return {"jsonrpc": "2.0", "method": "session/update",
                "params": {"sessionId": "sid-9",
                           "update": {"sessionUpdate": "usage_update", "used": used, "size": size}}}
    other = {"params": {"sessionId": "sid-9", "update": {"sessionUpdate": "tool_call"}}}
    (host / "acp-prompt.journal").write_text(
        "\n".join(json.dumps(r) for r in (upd(10, 1000000), upd(132089, 1000000), other)) + "\n",
        encoding="utf-8")
    assert cf.ao_journal_usage("proj-1", tmp_path) == {"sid": "sid-9", "used": 132089,
                                                        "size": 1000000}
    assert cf.ao_journal_usage("missing", tmp_path) is None
    assert cf.ao_journal_usage("../x", tmp_path) is None
