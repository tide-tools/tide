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
    leaks = verify.scan_text("owner = myapp_thing", "f.py", ["myapp"])
    assert len(leaks) == 1
    assert leaks[0].kind == "instance-token"
    assert leaks[0].detail == "myapp"


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


# --- personal tokens (the owner's human name) ------------------------------

def test_home_instance_tokens_reads_control_home_file(tmp_path):
    home = tmp_path / "control-home"
    (home / ".tide").mkdir(parents=True)
    (home / ".tide" / verify.INSTANCE_TOKENS_FILE).write_text(
        "# a comment\n\nStem\nOther  # trailing note\nStem\n", encoding="utf-8")
    assert verify.home_instance_tokens(home) == ["Other", "Stem"]


def test_home_instance_tokens_absent_file_is_not_an_error(tmp_path):
    home = tmp_path / "control-home"
    (home / ".tide").mkdir(parents=True)
    assert verify.home_instance_tokens(home) == []


def test_check_portable_flags_a_personal_token(tmp_path, monkeypatch):
    # The whole point: a human name in shipped source must FAIL the gate, armed from
    # the owner's control-home rather than a flag someone has to remember.
    monkeypatch.setattr(verify, "home_instance_tokens", lambda *a, **k: ["Stem"])
    pkg = tmp_path / "tide"
    pkg.mkdir()
    (pkg / "greet.py").write_text('MSG = "покажи Stemy"\n', encoding="utf-8")
    report = verify.check_portable(pkg_dir=pkg)
    assert not report.ok
    assert any(lk.detail == "Stem" for lk in report.leaks)


def test_check_portable_says_when_no_personal_token_is_armed(tmp_path, monkeypatch):
    # A PASS with nothing personal armed is a WEAK pass — the report must say so out
    # loud, or a green gate reads as "the name was checked" when it never was.
    monkeypatch.setattr(verify, "home_instance_tokens", lambda *a, **k: [])
    pkg = tmp_path / "tide"
    pkg.mkdir()
    (pkg / "ok.py").write_text("X = 1\n", encoding="utf-8")
    report = verify.check_portable(pkg_dir=pkg)
    assert report.ok
    assert any("NONE" in m for m in report.messages)


# --- human-facing surfaces (docs/ is the Pages source) ---------------------

def _showcase_tree(root):
    """A miniature repo: the four surfaces the gate must read, plus a decoy."""
    (root / "src" / "tide").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "tide"\n', encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "RELEASING.md").write_text("release runbook\n", encoding="utf-8")
    (root / "skills" / "handoff").mkdir(parents=True)
    (root / "skills" / "handoff" / "SKILL.md").write_text("a skill\n", encoding="utf-8")
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / "QUICKSTART.ru.md").write_text("быстрый старт\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_thing.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (root / "NOTES.md").write_text("not a showcase surface\n", encoding="utf-8")


def test_showcase_files_covers_every_public_surface(tmp_path):
    _showcase_tree(tmp_path)
    names = sorted(str(p.relative_to(tmp_path)) for p in verify.showcase_files(tmp_path))
    assert names == [
        "QUICKSTART.ru.md", "README.md",
        "docs/RELEASING.md", "skills/handoff/SKILL.md", "tests/test_thing.py",
    ]


def test_scan_showcase_flags_a_token_in_docs(tmp_path):
    # The hole this closes: docs/ is the GitHub Pages source and ships to readers,
    # not to pip — so the package scan never saw it.
    _showcase_tree(tmp_path)
    (tmp_path / "docs" / "RELEASING.md").write_text(
        "ask Stem before pushing\n", encoding="utf-8")
    leaks = verify.scan_showcase(tmp_path, ["Stem"])
    assert [(lk.source, lk.kind) for lk in leaks] == [("docs/RELEASING.md", "instance-token")]


def test_scan_showcase_keeps_placeholder_example_paths(tmp_path):
    # QUICKSTART legitimately shows a home-rooted path with a placeholder user —
    # prose is checked for IDENTITY, not for path shape.
    _showcase_tree(tmp_path)
    (tmp_path / "README.md").write_text(
        "tide control-home ready at /Users/you/tide-home\n", encoding="utf-8")
    assert verify.scan_showcase(tmp_path, ["Stem"]) == []


def test_scan_showcase_still_flags_a_real_home_path(tmp_path):
    # Dropping the path-shape rule costs nothing: the owner's ACTUAL home path is
    # an auto-token, so a genuine leak in prose is still caught — by name.
    _showcase_tree(tmp_path)
    (tmp_path / "README.md").write_text(
        "see /Users/realperson/notes\n", encoding="utf-8")
    leaks = verify.scan_showcase(tmp_path, ["/Users/realperson"])
    assert [lk.source for lk in leaks] == ["README.md"]


def test_repo_root_is_none_outside_a_dev_tree(monkeypatch, tmp_path):
    # A pip-installed tide sits in site-packages with no repo above it.
    pkg = tmp_path / "site-packages" / "tide"
    pkg.mkdir(parents=True)
    monkeypatch.setattr(verify, "package_source_dir", lambda: pkg)
    assert verify.repo_root() is None


def test_check_portable_says_when_surfaces_are_skipped(monkeypatch, tmp_path):
    # Skipped must READ as skipped — never as a clean pass.
    monkeypatch.setattr(verify, "repo_root", lambda: None)
    pkg = tmp_path / "tide"
    pkg.mkdir()
    (pkg / "ok.py").write_text("X = 1\n", encoding="utf-8")
    report = verify.check_portable(pkg_dir=pkg, include_auto_tokens=False)
    assert report.ok
    assert any("skipped" in m for m in report.messages)


def test_scan_showcase_flags_a_name_parked_in_tests(tmp_path):
    # tests/ never rides in the wheel, but it sits in the PUBLIC repo — and it is
    # the least-watched surface, so a name in a fixture survives every sweep of the
    # "real" code. Four of six leaks in one deanon round were exactly here.
    _showcase_tree(tmp_path)
    (tmp_path / "tests" / "test_thing.py").write_text(
        'def test_x():\n    assert sign(signer="stem") == "stem @ 2026-06-25"\n',
        encoding="utf-8")
    leaks = verify.scan_showcase(tmp_path, ["Stem"])
    assert [lk.source for lk in leaks] == ["tests/test_thing.py"]


def test_scan_showcase_keeps_invented_fixture_paths_in_tests(tmp_path):
    # The suite's own fixtures ARE made-up home paths — they are the inputs the
    # abs-path rule is tested with. Shape-scanning tests/ would fail that suite.
    _showcase_tree(tmp_path)
    (tmp_path / "tests" / "test_thing.py").write_text(
        'LEAK = "/Users/someoneelse/x"\n', encoding="utf-8")
    assert verify.scan_showcase(tmp_path, ["Stem"]) == []


def test_prose_is_not_scanned_for_the_bare_login(tmp_path, monkeypatch):
    # A login is a MACHINE token and usually an ordinary word (demo, user, test).
    # Aimed at prose and fixtures it finds the dictionary, not a person: the
    # clean-machine check runs as `tester` and went red on a signer fixture spelled
    # `tester`. A red gate makes `tide self-update` REFUSE, so this cost every user
    # with a common login their updates. The package scan below still catches it.
    monkeypatch.setattr(verify, "machine_login_token", lambda: "tester")
    monkeypatch.setattr(verify, "home_instance_tokens", lambda *a, **k: [])
    monkeypatch.setattr(verify, "default_instance_tokens", lambda: ["tester"])
    root = tmp_path / "repo"
    root.mkdir()
    _showcase_tree(root)
    (root / "tests" / "test_thing.py").write_text(
        'curate.validate_step(t, "1", who="tester")\n', encoding="utf-8")
    monkeypatch.setattr(verify, "repo_root", lambda: root)
    pkg = root / "src" / "tide"
    (pkg / "ok.py").write_text("X = 1\n", encoding="utf-8")
    report = verify.check_portable(pkg_dir=pkg)
    assert report.ok, report.messages


def test_a_login_the_owner_listed_himself_still_guards_prose(tmp_path, monkeypatch):
    # Dropping the login is about a word that merely happens to be a login — not an
    # exemption. An owner who lists it says "this word IS me", and it stays armed.
    monkeypatch.setattr(verify, "machine_login_token", lambda: "tester")
    monkeypatch.setattr(verify, "home_instance_tokens", lambda *a, **k: ["tester"])
    monkeypatch.setattr(verify, "default_instance_tokens", lambda: ["tester"])
    root = tmp_path / "repo"
    root.mkdir()
    _showcase_tree(root)
    (root / "docs" / "RELEASING.md").write_text("ask tester first\n", encoding="utf-8")
    monkeypatch.setattr(verify, "repo_root", lambda: root)
    pkg = root / "src" / "tide"
    (pkg / "ok.py").write_text("X = 1\n", encoding="utf-8")
    report = verify.check_portable(pkg_dir=pkg)
    assert not report.ok
    assert any(lk.source == "docs/RELEASING.md" for lk in report.leaks)


def test_the_login_still_guards_shipped_code(tmp_path, monkeypatch):
    # The narrowing is scoped to prose: in shipped source a username can only be a
    # leak, and that rule is untouched.
    monkeypatch.setattr(verify, "machine_login_token", lambda: "tester")
    monkeypatch.setattr(verify, "home_instance_tokens", lambda *a, **k: [])
    monkeypatch.setattr(verify, "default_instance_tokens", lambda: ["tester"])
    monkeypatch.setattr(verify, "repo_root", lambda: None)
    pkg = tmp_path / "tide"
    pkg.mkdir()
    (pkg / "boom.py").write_text('PATH = "/somewhere/tester/cache"\n', encoding="utf-8")
    report = verify.check_portable(pkg_dir=pkg)
    assert not report.ok
    assert any(lk.detail == "tester" for lk in report.leaks)


# --- the traps: one spelling never covers the others -----------------------

def test_tokens_match_case_insensitively():
    # A name is capitalised in prose, lower-cased in a signer field or a path, and
    # shouted in an env var. One entry must catch all three, or the list rots into
    # variants nobody remembers to add.
    for line in ("prose says Stem", 'signer="stem"', "STEM=1"):
        leaks = verify.scan_text(line, "f.py", ["Stem"])
        assert len(leaks) == 1, line
        assert leaks[0].detail == "Stem"


def test_a_latin_token_does_not_cover_a_cyrillic_one():
    # THE ALPHABET TRAP, as a test: a sweep done in one script leaves the other
    # untouched and the gate reports an honest, confident zero.
    assert verify.scan_text("покажи Стему", "f.py", ["Stem"]) == []
    assert verify.scan_text("show Stem", "f.py", ["Стем"]) == []
    both = ["Stem", "Стем"]
    assert len(verify.scan_text("покажи Стему", "f.py", both)) == 1
    assert len(verify.scan_text("show Stem", "f.py", both)) == 1


# --- check_portable orchestration ------------------------------------------

def test_check_portable_passes_on_clean_repo():
    report = verify.check_portable()
    assert report.ok, "\n".join(report.messages)


def test_check_portable_fails_on_planted_package_leak(tmp_path):
    pkg = tmp_path / "tide"
    pkg.mkdir()
    (pkg / "boom.py").write_text('SECRET = "/Users/zaphod/.ssh/id"\n', encoding="utf-8")
    report = verify.check_portable(pkg_dir=pkg, include_auto_tokens=False)
    assert not report.ok
    assert any(lk.kind == "abs-home-path" for lk in report.leaks)


def test_check_portable_honors_extra_instance_token(tmp_path):
    pkg = tmp_path / "tide"
    pkg.mkdir()
    (pkg / "names.py").write_text("PROJECT = 'myapp'\n", encoding="utf-8")
    clean = verify.check_portable(pkg_dir=pkg, include_auto_tokens=False)
    assert clean.ok  # no token configured → not flagged
    flagged = verify.check_portable(
        pkg_dir=pkg, instance_tokens=["myapp"], include_auto_tokens=False
    )
    assert not flagged.ok
    assert any(lk.detail == "myapp" for lk in flagged.leaks)


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
