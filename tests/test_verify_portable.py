"""`tide verify --portable` — the tool ⊥ instance enforcement gate.

The keystone for sharing tide: the shipped tool must carry no absolute home paths
or instance tokens, and a fresh `tide init` must produce a clean generic skeleton.
These tests prove the gate passes on a clean tree, fails loud on a planted leak,
and that the real package + a real init skeleton are clean.
"""

from __future__ import annotations

from pathlib import Path

from tide import cli, verify


# --- scan_text -------------------------------------------------------------

def test_scan_text_flags_abs_home_path():
    leaks = verify.scan_text('p = "/Users/alice/secret"', "f.py", [])
    assert len(leaks) == 1
    assert leaks[0].kind == "abs-home-path"
    assert leaks[0].detail == "/Users/alice"


def test_scan_text_flags_home_root_path():
    leaks = verify.scan_text('p = "/home/bob/work"', "f.py", [])
    assert [lk.kind for lk in leaks] == ["abs-home-path"]


def test_scan_text_ignores_tilde_home():
    # The portable `~/…` form is fine — only absolute roots are leaks.
    assert verify.scan_text('cfg = "~/.claude/CLAUDE.md"', "f.py", []) == []


def test_scan_text_flags_instance_token():
    leaks = verify.scan_text("owner = unimatch_thing", "f.py", ["unimatch"])
    assert len(leaks) == 1
    assert leaks[0].kind == "instance-token"
    assert leaks[0].detail == "unimatch"


def test_scan_text_clean_line_no_leaks():
    assert verify.scan_text("def f():\n    return 1", "f.py", ["zzz"]) == []


# --- scan_package_source (clean + planted leak) ----------------------------

def test_scan_package_source_clean(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "ok.py").write_text("X = 1\nhome = '~/safe'\n", encoding="utf-8")
    assert verify.scan_package_source(pkg, verify.default_instance_tokens()) == []


def test_scan_package_source_detects_planted_abs_path(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "leaky.py").write_text('CFG = "/Users/someone/x"\n', encoding="utf-8")
    leaks = verify.scan_package_source(pkg, [])
    assert len(leaks) == 1
    assert leaks[0].kind == "abs-home-path"
    assert leaks[0].source == "pkg/leaky.py"


def test_scan_package_source_skips_pycache(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "__pycache__" / "x.py").write_text('"/Users/me/c"', encoding="utf-8")
    (pkg / "real.py").write_text("X = 1\n", encoding="utf-8")
    assert verify.scan_package_source(pkg, []) == []


def test_scan_package_source_scans_non_py_text_files(tmp_path):
    # A .json/.md/.toml added under the package ships in the wheel — must be scanned.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "ok.py").write_text("X = 1\n", encoding="utf-8")
    (pkg / "data.json").write_text('{"path": "/Users/leaky/x"}\n', encoding="utf-8")
    leaks = verify.scan_package_source(pkg, [])
    assert len(leaks) == 1
    assert leaks[0].source.endswith("data.json")
    assert leaks[0].kind == "abs-home-path"


def test_scan_package_source_skips_binary(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "blob.bin").write_bytes(b"\x00\x01/Users/me/x\x00")
    assert verify.scan_package_source(pkg, []) == []


# --- the real shipped package + init skeleton are clean --------------------

def test_real_package_source_is_clean():
    leaks = verify.scan_package_source(
        verify.package_source_dir(), verify.default_instance_tokens()
    )
    assert leaks == [], "shipped src/tide leaked: {0}".format(leaks)


def test_init_skeleton_is_clean():
    assert verify.scan_init_skeleton(verify.default_instance_tokens()) == []


def test_scan_init_skeleton_catches_rebaked_abs_root(monkeypatch):
    # Regression guard for the gate itself: re-introduce the ORIGINAL bug — bake the
    # init root's absolute path into the contract passport — and prove the init scan
    # FLAGS it. The macOS tmpdir (/private/var/folders/…) is invisible to the
    # /(Users|home)/ regex, so this only passes because scan_init_skeleton seeds the
    # init root's abs path as an instance token.
    from pathlib import Path

    from tide.contract import lifecycle

    orig_new = lifecycle.new

    def buggy_new(root, arc_ref, **kwargs):
        cpath = orig_new(root, arc_ref, **kwargs)
        # the exact shape of the fixed bug: str(Path(root).resolve())
        cpath.write_text(
            cpath.read_text(encoding="utf-8")
            + "\nleaked-root: {0}\n".format(str(Path(root).resolve())),
            encoding="utf-8",
        )
        return cpath

    monkeypatch.setattr(lifecycle, "new", buggy_new)
    leaks = verify.scan_init_skeleton([])
    assert leaks, "scan_init_skeleton false-passed on a re-baked absolute root path"
    assert any(lk.kind == "instance-token" for lk in leaks)


# --- check_portable orchestration ------------------------------------------

def test_check_portable_passes_on_clean_repo():
    report = verify.check_portable()
    assert report.ok, "\n".join(report.messages)


def test_check_portable_fails_on_planted_package_leak(tmp_path):
    pkg = tmp_path / "tide"
    pkg.mkdir()
    (pkg / "boom.py").write_text('SECRET = "/Users/grisha/.ssh/id"\n', encoding="utf-8")
    report = verify.check_portable(pkg_dir=pkg, include_auto_tokens=False)
    assert not report.ok
    assert any(lk.kind == "abs-home-path" for lk in report.leaks)


def test_check_portable_honors_extra_instance_token(tmp_path):
    pkg = tmp_path / "tide"
    pkg.mkdir()
    (pkg / "names.py").write_text("PROJECT = 'unimatch'\n", encoding="utf-8")
    clean = verify.check_portable(pkg_dir=pkg, include_auto_tokens=False)
    assert clean.ok  # no token configured → not flagged
    flagged = verify.check_portable(
        pkg_dir=pkg, instance_tokens=["unimatch"], include_auto_tokens=False
    )
    assert not flagged.ok
    assert any(lk.detail == "unimatch" for lk in flagged.leaks)


# --- CLI contract ----------------------------------------------------------

def test_cli_verify_portable_exits_zero_on_clean(capsys):
    rc = cli.main(["verify", "--portable"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PASS" in out


def test_cli_verify_requires_path_without_portable(capsys):
    rc = cli.main(["verify"])
    err = capsys.readouterr().err
    assert rc != 0
    assert "PATH is required" in err
