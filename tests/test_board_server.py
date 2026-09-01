"""Работа 51 — `tide board`: the boxed board server (render + CLI wiring).

The page is a pure projection (render_page), so most coverage needs no socket;
one test binds a real localhost port and GETs, because "the port answers" is the
part that actually breaks on a fresh machine.
"""

from __future__ import annotations

import urllib.request

import pytest

from tide import board_server, cli, roster
from tide.arc import stream, work as work_mod


@pytest.fixture
def home(tmp_control_home, monkeypatch):
    monkeypatch.chdir(tmp_control_home)
    monkeypatch.setenv("TIDE_HOME", str(tmp_control_home))
    return tmp_control_home


def test_render_page_shows_home_stream(home):
    stream.new_arc(home, "alpha")
    page = board_server.render_page(home)
    assert "01-alpha" in page
    assert home.name in page


def test_render_page_shows_rostered_project_and_unadopted(home, tmp_path):
    proj = tmp_path / "proj-b"
    proj.mkdir()
    stream.new_arc(proj, "beta")
    roster.add(home, "proj-b", str(proj))
    bare = tmp_path / "bare"
    bare.mkdir()
    roster.add(home, "bare", str(bare))

    page = board_server.render_page(home)
    assert "proj-b" in page and "01-beta" in page
    assert "no .tide/ here yet" in page  # a rostered dir without .tide says so


def test_render_page_escapes_html(home):
    stream.new_arc(home, "alpha")
    pp = stream.passport_path(home / ".tide" / "arcs" / "01-alpha")
    pp.write_text(
        pp.read_text(encoding="utf-8").replace(
            "<one line — what this arc closes>", "goal with <script>alert(1)</script>"
        ),
        encoding="utf-8",
    )
    page = board_server.render_page(home)
    assert "<script>alert(1)" not in page


def test_render_page_lists_work_cards(home):
    work_mod.new_work(home, "первая работа: проверить доску")
    page = board_server.render_page(home)
    assert "первая работа" in page
    assert "st-open" in page


def test_cli_once_prints_page(home, capsys):
    rc = cli.main(["board", "--once"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "<!doctype html>" in out


def test_server_answers_on_localhost(home):
    import threading

    httpd = board_server.serve(home, port=0)  # port 0 = any free port
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:{0}/".format(port), timeout=5
        ) as resp:
            body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert home.name in body
        with urllib.request.urlopen(
            "http://127.0.0.1:{0}/nope".format(port), timeout=5
        ) as resp2:
            pytest.fail("unexpected 200 for /nope")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_busy_port_is_a_clear_error(home):
    httpd = board_server.serve(home, port=0)
    port = httpd.server_address[1]
    try:
        with pytest.raises(board_server.BoardServeError) as err:
            board_server.serve(home, port=port)
        assert "--port" in str(err.value)
    finally:
        httpd.server_close()
