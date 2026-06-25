"""F7 — `tide verify`: isolated, collision-free verification of a built artifact.

Covers the core the fix promises: an OS-assigned ephemeral port (no fixed-port
collision), an HTTP 200 servability check, the staging that keeps the source tree
untouched, the inline-script extraction recipe, and the CLI pass/fail contract.
The node smoke is exercised only when a real ``node`` is on PATH.
"""

from __future__ import annotations

import shutil

import pytest

from tide import cli, verify

HTML_OK = """<!doctype html>
<html><head><title>Tide Pool</title></head>
<body>
<script src="vendor.js"></script>
<script type="application/json">{"not": "javascript"}</script>
<script>
  const greet = (who) => `hi ${who}`;
  console.log(greet("tide"));
</script>
</body></html>
"""

HTML_BAD_JS = """<!doctype html>
<html><body>
<script>
  function broken( {   // syntax error: unbalanced
</script>
</body></html>
"""


# --- free port -------------------------------------------------------------

def test_free_port_returns_usable_distinct_ports():
    # Arrange / Act
    a = verify.free_port()
    b = verify.free_port()

    # Assert — valid ephemeral-range ints (kernel-assigned, so just sanity-bound)
    assert isinstance(a, int) and 1024 < a < 65536
    assert isinstance(b, int) and 1024 < b < 65536


def test_serve_binds_ephemeral_port_and_serves_200(tmp_path):
    # Arrange
    (tmp_path / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")

    # Act
    with verify.serve(tmp_path) as port:
        assert port != 0  # OS assigned a concrete free port
        status, body = verify.http_status(
            "http://127.0.0.1:{0}/index.html".format(port)
        )

    # Assert
    assert status == 200
    assert b"ok" in body


def test_serve_two_runs_get_different_ports(tmp_path):
    (tmp_path / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    with verify.serve(tmp_path) as p1:
        with verify.serve(tmp_path) as p2:
            assert p1 != p2  # ephemeral → no fixed-port collision


# --- entry / staging -------------------------------------------------------

def test_find_entry_prefers_index(tmp_path):
    (tmp_path / "other.html").write_text("x", encoding="utf-8")
    (tmp_path / "index.html").write_text("x", encoding="utf-8")
    assert verify.find_entry(tmp_path) == "index.html"


def test_find_entry_falls_back_to_first_html(tmp_path):
    (tmp_path / "b.html").write_text("x", encoding="utf-8")
    (tmp_path / "a.html").write_text("x", encoding="utf-8")
    assert verify.find_entry(tmp_path) == "a.html"


def test_find_entry_no_html_raises(tmp_path):
    (tmp_path / "readme.txt").write_text("x", encoding="utf-8")
    with pytest.raises(verify.VerifyError):
        verify.find_entry(tmp_path)


def test_stage_file_copies_in_and_keeps_source(tmp_path):
    # Arrange
    src = tmp_path / "app.html"
    src.write_text(HTML_OK, encoding="utf-8")
    dest = tmp_path / "staged"
    dest.mkdir()

    # Act
    entry = verify.stage_artifact(src, dest)

    # Assert — copied, named, source untouched
    assert entry == "app.html"
    assert (dest / "app.html").read_text(encoding="utf-8") == HTML_OK
    assert src.is_file()


def test_stage_dir_copies_contents(tmp_path):
    src = tmp_path / "build"
    src.mkdir()
    (src / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (src / "app.js").write_text("var x = 1;", encoding="utf-8")
    dest = tmp_path / "staged"
    dest.mkdir()

    entry = verify.stage_artifact(src, dest)

    assert entry == "index.html"
    assert (dest / "app.js").is_file()


def test_stage_non_html_file_raises(tmp_path):
    src = tmp_path / "data.json"
    src.write_text("{}", encoding="utf-8")
    dest = tmp_path / "staged"
    dest.mkdir()
    with pytest.raises(verify.VerifyError):
        verify.stage_artifact(src, dest)


# --- inline-script extraction (node recipe) --------------------------------

def test_extract_inline_scripts_keeps_only_inline_js():
    scripts = verify.extract_inline_scripts(HTML_OK)
    assert len(scripts) == 1  # external + application/json skipped
    assert "greet" in scripts[0]


def test_extract_inline_scripts_keeps_module_type():
    html = '<script type="module">export const x = 1;</script>'
    assert verify.extract_inline_scripts(html) == ["export const x = 1;"]


def test_extract_inline_scripts_empty_when_none():
    assert verify.extract_inline_scripts("<p>no scripts</p>") == []


# --- end-to-end verify -----------------------------------------------------

def test_verify_passes_on_good_html_file(tmp_path):
    art = tmp_path / "index.html"
    art.write_text(HTML_OK, encoding="utf-8")

    # node=False isolates the HTTP-200 core from node availability
    result = verify.verify(art, node=False)

    assert result.ok is True
    assert result.http_ok is True
    assert result.status == 200
    assert result.port != 0
    assert result.node_ran is False


def test_verify_passes_on_directory(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text(HTML_OK, encoding="utf-8")

    result = verify.verify(build, node=False)

    assert result.ok is True
    assert result.entry == "index.html"


def test_verify_missing_artifact_raises(tmp_path):
    with pytest.raises(verify.VerifyError):
        verify.verify(tmp_path / "nope.html")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_verify_node_smoke_passes_clean_js(tmp_path):
    art = tmp_path / "index.html"
    art.write_text(HTML_OK, encoding="utf-8")

    result = verify.verify(art, node=True)

    assert result.ok is True
    assert result.node_ran is True
    assert result.node_ok is True
    assert result.scripts_checked == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_verify_node_smoke_fails_broken_js(tmp_path):
    art = tmp_path / "index.html"
    art.write_text(HTML_BAD_JS, encoding="utf-8")

    result = verify.verify(art, node=True)

    assert result.node_ran is True
    assert result.node_ok is False
    assert result.ok is False  # syntax error fails the overall verdict


# --- CLI -------------------------------------------------------------------

def test_cli_verify_pass_returns_zero(tmp_path, capsys):
    art = tmp_path / "index.html"
    art.write_text(HTML_OK, encoding="utf-8")

    rc = cli.main(["verify", str(art), "--no-node"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "PASS" in out
    assert "http 200" in out


def test_cli_verify_fail_returns_one(tmp_path, capsys):
    # A directory with no .html entry → VerifyError surfaces as a nonzero exit
    empty = tmp_path / "empty"
    empty.mkdir()

    rc = cli.main(["verify", str(empty)])

    assert rc == 1
    assert "tide:" in capsys.readouterr().err


def test_cli_verify_registered_in_help():
    help_text = cli.build_parser().format_help()
    assert "verify" in help_text
