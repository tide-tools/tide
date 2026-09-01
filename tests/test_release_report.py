"""50-release — `tide report`: a stuck user's word reaches the maintainer, and nothing else does.

The load-bearing property here is NOT that the report arrives — it is what the
report does not contain. A diagnostic bundle is the classic way private work
leaks: someone reports a tide bug and ships the names of their employer's repos
along with it. So most of this file is about redaction and about the collected
set being CLOSED.

Nothing here touches the network: `gh` is never invoked, the issue path is driven
through a stub.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import cli, release_report as rr


# --- redaction ----------------------------------------------------------------


def test_redact_collapses_the_whole_home_path_not_just_the_prefix(monkeypatch, tmp_path):
    # Half-redacting still ships the client's name — the exact leak this prevents.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/zaphod")))
    out = rr.redact("failed at /home/zaphod/work/acme-client/run.py line 3")
    assert "acme-client" not in out
    assert "zaphod" not in out
    assert "~/…" in out


def test_redact_strips_a_foreign_home_path_too(monkeypatch):
    # Not just THIS machine's home: a log can quote a colleague's path.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/zaphod")))
    out = rr.redact("cp /Users/trillian/Projects/secret/a.txt .")
    assert "trillian" not in out
    assert "secret" not in out


def test_redact_replaces_the_bare_username(monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/zaphod")))
    assert "zaphod" not in rr.redact("user zaphod cannot write there")
    assert "<user>" in rr.redact("user zaphod cannot write there")


def test_redact_leaves_ordinary_text_alone(monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/zaphod")))
    text = "tide menu hangs after I pick a project"
    assert rr.redact(text) == text


def test_log_tail_is_redacted_and_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/zaphod")))
    log = tmp_path / "out.log"
    log.write_text(
        "\n".join(["line {0} /home/zaphod/private/{0}".format(i) for i in range(200)]),
        encoding="utf-8",
    )
    tail = rr.read_log_tail(log, lines=10)
    assert len(tail.splitlines()) == 10
    assert "private" not in tail
    assert "zaphod" not in tail


# --- collection ---------------------------------------------------------------


def _collect(monkeypatch, what="the board will not open", **kw):
    monkeypatch.setattr(rr, "_doctor_statuses", lambda network=False: [("python", "ok")])
    monkeypatch.setattr(rr, "_project_count", lambda control_home=None: "3")
    monkeypatch.setattr(rr, "resolve_source", lambda: None)
    monkeypatch.setattr(rr, "read_marker", lambda p: {"version": "1.0.2"})
    return rr.collect(what, **kw)


def test_report_carries_what_the_maintainer_needs(monkeypatch):
    body = _collect(monkeypatch).render()
    for needed in ("tide version", "install shape", "python", "platform", "update channel"):
        assert needed in body
    assert "the board will not open" in body


def test_report_counts_projects_but_never_names_them(monkeypatch):
    body = _collect(monkeypatch).render()
    assert "projects in roster" in body
    assert "3" in body


def test_report_carries_doctor_statuses_but_not_doctor_details(monkeypatch):
    # doctor's detail strings quote roster paths and project names by design.
    monkeypatch.setattr(
        rr, "_doctor_statuses",
        lambda network=False: [("roster", "warn")],
    )
    monkeypatch.setattr(rr, "_project_count", lambda control_home=None: "1")
    monkeypatch.setattr(rr, "resolve_source", lambda: None)
    monkeypatch.setattr(rr, "read_marker", lambda p: {})
    body = rr.collect("x").render()
    assert "roster: warn" in body


def test_report_redacts_the_users_own_words(monkeypatch):
    # People paste paths into the description constantly.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/zaphod")))
    body = _collect(monkeypatch, what="broke in /home/zaphod/clients/bigcorp").render()
    assert "bigcorp" not in body
    assert "zaphod" not in body


def test_report_states_what_it_withheld(monkeypatch):
    # The promise is checkable, not just claimed.
    body = _collect(monkeypatch).render()
    assert "deliberately not included" in body
    for line in rr.WITHHELD:
        assert line in body


def test_install_shape_names_the_axis_that_matters():
    class Editable:
        editable = True
        uv_tool = False

        def name(self):
            return "local-source"

    class Published:
        editable = False
        uv_tool = False

        def name(self):
            return "published-channel"

    assert "editable" in rr.install_shape(Editable())
    assert "published" in rr.install_shape(Published())


def test_title_is_one_line_however_long_the_description(monkeypatch):
    rep = _collect(monkeypatch, what="a" * 500 + "\nsecond line")
    assert "\n" not in rep.title()
    assert len(rep.title()) <= 100


# --- the CLI surface ------------------------------------------------------------


def test_dry_run_sends_nothing_and_writes_nothing(monkeypatch, capsys, tmp_path):
    sent = []
    monkeypatch.setattr(rr, "send_via_gh", lambda r, repo=None: sent.append(r) or (True, ""))
    monkeypatch.setattr(rr, "save_to_file", lambda r: sent.append("file") or tmp_path / "x")
    monkeypatch.setattr(rr, "_doctor_statuses", lambda network=False: [])
    rc = cli.main(["report", "--dry-run", "--no-network", "the board will not open"])
    out = capsys.readouterr().out
    assert rc == 0
    assert sent == []
    assert "EXACTLY what would be sent" in out
    assert "nothing was sent or written" in out


def test_report_shows_the_body_before_sending(monkeypatch, capsys, tmp_path):
    # The human must be able to READ what leaves their machine, not trust a claim.
    monkeypatch.setattr(rr, "_doctor_statuses", lambda network=False: [])
    monkeypatch.setattr(rr, "gh_ready", lambda: True)
    monkeypatch.setattr(rr, "send_via_gh", lambda r, repo=None: (True, "https://issue/1"))
    rc = cli.main(["report", "--yes", "--no-network", "it broke"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "EXACTLY what would be sent" in out
    assert out.index("EXACTLY") < out.index("https://issue/1")


def test_falls_back_to_a_file_when_gh_is_not_ready(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(rr, "_doctor_statuses", lambda network=False: [])
    monkeypatch.setattr(rr, "gh_ready", lambda: False)
    monkeypatch.setattr(rr, "tide_home_dir", lambda env=None: tmp_path)
    monkeypatch.setattr(rr, "copy_to_clipboard", lambda text: False)
    rc = cli.main(["report", "--no-network", "it broke"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "saved:" in out
    written = list((tmp_path / "reports").glob("report-*.md"))
    assert len(written) == 1
    assert "it broke" in written[0].read_text(encoding="utf-8")


def test_a_failed_send_still_leaves_the_report_on_disk(monkeypatch, capsys, tmp_path):
    # Losing the report because the network blinked would be the worst outcome.
    monkeypatch.setattr(rr, "_doctor_statuses", lambda network=False: [])
    monkeypatch.setattr(rr, "gh_ready", lambda: True)
    monkeypatch.setattr(rr, "send_via_gh", lambda r, repo=None: (False, "rate limited"))
    monkeypatch.setattr(rr, "tide_home_dir", lambda env=None: tmp_path)
    rc = cli.main(["report", "--yes", "--no-network", "it broke"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "rate limited" in out
    assert list((tmp_path / "reports").glob("report-*.md"))


def test_empty_report_asks_for_words(monkeypatch, capsys):
    rc = cli.main(["report"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "say what happened" in out
