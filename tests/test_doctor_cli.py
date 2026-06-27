"""candidate 23 — `tide doctor` routed through the REAL argparse root.

Confirms the command is wired into ``cli.build_parser`` and that the aggregate
exit code reaches ``cli.main`` (0 when healthy, nonzero when a check fails). The
network probe is suppressed with ``--no-network`` so the CLI tests are hermetic
(no implicit network — the whole point of the candidate). ``$TIDE_HOME`` is
redirected to a tmp dir so the real machine's install marker never leaks in.
"""

from __future__ import annotations

import pytest

from tide import cli


@pytest.fixture(autouse=True)
def _hermetic_home(tmp_path, monkeypatch):
    # Redirect the install-marker home so doctor reads a tmp (absent) marker,
    # never the developer's real ~/.local/share/tide marker.
    monkeypatch.setenv("TIDE_HOME", str(tmp_path / "tide-home"))


def test_doctor_is_wired_into_the_parser():
    parser = cli.build_parser()
    args = parser.parse_args(["doctor", "--no-network"])
    assert getattr(args, "_cmd", None) == "doctor"
    assert callable(getattr(args, "func", None))


def test_doctor_runs_green_in_a_healthy_project(tmp_project, monkeypatch, capsys):
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["doctor", "--no-network"])
    out = capsys.readouterr().out
    assert rc == 0
    # the board lists each check by name
    for name in ("python", "structure", "canon", "hooks", "install-marker", "channel"):
        assert name in out


def test_doctor_exits_nonzero_outside_a_tide_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no .tide anywhere above a pytest tmp dir
    rc = cli.main(["doctor", "--no-network"])
    capsys.readouterr()
    assert rc != 0


def test_doctor_help_is_listed(capsys):
    rc = cli.main(["help"])
    assert rc == 0
    assert "doctor" in capsys.readouterr().out.lower()
