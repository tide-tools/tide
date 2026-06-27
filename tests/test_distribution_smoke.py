"""Distribution smoke test — Criterion E: fresh instantiation by a second person.

Proves that a SECOND PERSON can:
1. Run ``tide init`` in a fresh temp directory and get the expected control-home
   scaffold (their own ``.tide/``, canon, roster, README).
2. Receive a PORTABLE / CLEAN instance — no absolute host paths or instance tokens
   baked into generated files. This is the C∩E intersection: criterion C (tool ⊥
   instance) proved on the output of criterion E (fresh instantiation).
3. Perform real operations in THEIR instance (roster add/ls, status) — they can
   lead their own work, not just hold an empty shell.

Additional checks:
4. ``pyproject.toml`` has the metadata required for a real PyPI release (classifiers,
   project.urls) with no runtime dependencies added.
5. The Homebrew formula exists at the conventional location for a tap.

Everything here is offline and hermetic: no network calls, no writes outside the
temp dir managed by pytest, no subprocess shell-out to a globally installed tide —
all init paths are invoked the same way ``tests/test_init_cli.py`` does it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tide import cli, init_home, paths, roster, verify


# ---------------------------------------------------------------------------
# Internal helper — scan a freshly-initialised home for portable leaks
# ---------------------------------------------------------------------------

def _scan_home_for_leaks(home: Path) -> list:
    """Scan every text file under *home* for absolute host paths or instance tokens.

    Mirrors the logic inside ``verify.scan_init_skeleton`` but targets the
    specific home we already created in the test instead of spinning a new
    tmpdir.  Adds the home's own absolute path (both raw and /private-resolved
    form on macOS) as an extra token so any re-baked-root class of bug is
    caught even when the OS tmpdir is outside the ``/(Users|home)/`` regex.
    """
    base_tokens = verify.default_instance_tokens()
    home_tokens = sorted(
        set(base_tokens)
        | {str(home), str(home.resolve())}
        | {str(home.parent), str(home.parent.resolve())}
    )
    leaks: list = []
    for f in sorted(home.rglob("*")):
        if not f.is_file() or "__pycache__" in f.parts:
            continue
        try:
            raw = f.read_bytes()
            if b"\x00" in raw[:8192]:
                continue  # binary — skip
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            continue
        rel = "fresh-home:{0}".format(f.relative_to(home))
        leaks.extend(verify.scan_text(text, rel, home_tokens))
    return leaks


# ---------------------------------------------------------------------------
# 1. Scaffold — tide init produces the expected control-home layout
# ---------------------------------------------------------------------------

def test_second_person_init_creates_control_home_scaffold(tmp_path, monkeypatch):
    """``tide init`` in a fresh dir produces the full control-home layout.

    Calls the CLI the same way ``test_init_cli.py`` does (monkeypatch.chdir +
    cli.main) — no shell-out, no globally-installed binary.
    """
    home = tmp_path / "alice-home"
    home.mkdir()
    monkeypatch.chdir(home)

    rc = cli.main(["init", "--name", "alice"])

    assert rc == 0
    # Core scaffold items the second person receives.
    assert paths.tide_dir(home).is_dir(), ".tide/ must exist"
    assert paths.canon_file(home).is_file(), "cannon/CANON.md must exist"
    assert paths.candidates_dir(home).is_dir(), "arcs/candidates/ must exist"
    assert paths.strictness_file(home).is_file(), "state/strictness must exist"
    assert paths.is_control_home(home), "roster.md must mark this as a control-home"
    assert (home / "README.md").is_file(), "README.md orientation must exist"


def test_second_person_init_readme_mentions_their_name(tmp_path, monkeypatch):
    """The README generated for the second person references the name they gave."""
    home = tmp_path / "bob-home"
    home.mkdir()
    monkeypatch.chdir(home)

    cli.main(["init", "--name", "bob"])

    readme = (home / "README.md").read_text(encoding="utf-8")
    assert "bob" in readme


def test_second_person_init_is_idempotent(tmp_path, monkeypatch):
    """Re-running ``tide init`` in an existing home is safe (reports nothing to create)."""
    home = tmp_path / "carol-home"
    home.mkdir()
    monkeypatch.chdir(home)

    cli.main(["init", "--name", "carol"])
    rc = cli.main(["init", "--name", "carol"])

    assert rc == 0


# ---------------------------------------------------------------------------
# 2. Portability — C∩E proof: fresh instance is clean
# ---------------------------------------------------------------------------

def test_fresh_home_has_no_host_leaks(tmp_path):
    """The scaffold produced by ``tide init`` carries no absolute host paths.

    This is the C∩E intersection: criterion C (tool ⊥ instance) applied to the
    OUTPUT of criterion E (distribution/fresh instantiation).  A second person's
    control-home must be clean — no ``/Users/<me>/`` or other host-specific
    literals baked into generated files.
    """
    home = tmp_path / "clean-home"
    home.mkdir()
    init_home.unfold_control_home(home, name="clean")

    leaks = _scan_home_for_leaks(home)

    assert leaks == [], (
        "fresh init produced host leaks:\n"
        + "\n".join(
            "  {src}:{ln} [{kind}] {detail}".format(
                src=lk.source, ln=lk.line, kind=lk.kind, detail=lk.detail
            )
            for lk in leaks
        )
    )


def test_distribution_check_portable_gate():
    """``tide verify --portable`` passes — the full C∩E combined gate.

    This is the single authoritative assertion: the SHIPPED TOOL is portable
    (no host leakage in ``src/tide/`` or ``pyproject.toml``) AND a freshly-
    initialised instance is clean.  Both halves must be green for Criterion E
    to be satisfied.
    """
    report = verify.check_portable()
    assert report.ok, (
        "verify --portable FAILED (distribution gate broken):\n"
        + "\n".join(report.messages)
    )


# ---------------------------------------------------------------------------
# 3. Second person can run their own work
# ---------------------------------------------------------------------------

def test_second_person_can_roster_add(tmp_path):
    """After ``tide init``, a second person can register a project in their roster."""
    home = tmp_path / "dave-home"
    home.mkdir()
    init_home.unfold_control_home(home, name="dave")

    entries = roster.add(home, "myproject", "/home/dave/code/myproject")

    assert any(e["name"] == "myproject" for e in entries)
    # Verify the entry persists through a disk round-trip.
    on_disk = roster.read_roster(home)
    assert {"name": "myproject", "path": "/home/dave/code/myproject"} in on_disk


def test_second_person_roster_add_multiple_projects(tmp_path):
    """A second person can register several projects in their own roster."""
    home = tmp_path / "eve-home"
    home.mkdir()
    init_home.unfold_control_home(home, name="eve")

    roster.add(home, "alpha", "/p/alpha")
    roster.add(home, "beta", "/p/beta")
    entries = roster.read_roster(home)

    names = [e["name"] for e in entries]
    assert "alpha" in names
    assert "beta" in names


def test_second_person_roster_ls_via_cli(tmp_path, monkeypatch, capsys):
    """``tide roster ls`` works in the second person's fresh home."""
    home = tmp_path / "frank-home"
    home.mkdir()
    monkeypatch.chdir(home)
    cli.main(["init", "--name", "frank"])
    cli.main(["roster", "add", "alpha", "/p/alpha"])
    capsys.readouterr()

    rc = cli.main(["roster", "ls"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "alpha" in out


def test_second_person_status_renders_stream_board(tmp_path, monkeypatch, capsys):
    """``tide status`` renders a STREAM board in the second person's fresh home."""
    home = tmp_path / "grace-home"
    home.mkdir()
    monkeypatch.chdir(home)
    cli.main(["init", "--name", "grace"])
    capsys.readouterr()

    rc = cli.main(["status"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "STREAM" in out


def test_second_person_roster_remove(tmp_path):
    """A second person can remove a project from their roster."""
    home = tmp_path / "henry-home"
    home.mkdir()
    init_home.unfold_control_home(home, name="henry")
    roster.add(home, "removeme", "/p/removeme")

    roster.remove(home, "removeme")

    on_disk = roster.read_roster(home)
    assert not any(e["name"] == "removeme" for e in on_disk)


# ---------------------------------------------------------------------------
# 4. PyPI release readiness — pyproject.toml metadata check
# ---------------------------------------------------------------------------

def test_pyproject_has_required_pypi_metadata():
    """``pyproject.toml`` contains the metadata required for a valid PyPI release.

    Reads the file directly via stdlib ``tomllib`` — no build tool required.
    Validates the fields that PyPI checks but setuptools does NOT enforce at
    install time (classifiers, project.urls).  Also asserts the runtime
    dependencies remain empty (STDLIB ONLY invariant).
    """
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert pyproject.is_file(), "pyproject.toml must exist at repo root"

    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)

    project = data.get("project", {})

    # Fields PyPI requires for a complete release entry.
    assert project.get("name"), "project.name is required"
    assert project.get("version"), "project.version is required"
    assert project.get("description"), "project.description is required"
    assert project.get("readme"), "project.readme is required (long_description)"
    assert project.get("requires-python"), "project.requires-python is required"
    assert project.get("license"), "project.license is required"
    assert project.get("authors"), "project.authors is required"

    # PyPI discoverability — currently missing from the base pyproject.toml.
    assert project.get("classifiers"), (
        "project.classifiers is missing; "
        "PyPI uses these to categorise the package (programming language, topic, etc.)"
    )
    assert project.get("urls"), (
        "project.urls is missing; "
        "PyPI expects at least a Homepage or Source link under [project.urls]"
    )

    # STDLIB ONLY invariant — must stay empty forever.
    deps = project.get("dependencies", [])
    assert deps == [], (
        "runtime dependencies must remain empty (STDLIB ONLY): {0}".format(deps)
    )


# ---------------------------------------------------------------------------
# 5. Homebrew formula exists at the conventional location
# ---------------------------------------------------------------------------

def test_homebrew_formula_exists():
    """The Homebrew formula file exists at ``packaging/tide.rb``."""
    repo_root = Path(__file__).resolve().parents[1]
    formula = repo_root / "packaging" / "tide.rb"
    assert formula.is_file(), (
        "packaging/tide.rb must exist for the Homebrew tap distribution channel"
    )


def test_homebrew_formula_declares_python_dependency():
    """The Homebrew formula declares a Python dependency."""
    repo_root = Path(__file__).resolve().parents[1]
    formula = repo_root / "packaging" / "tide.rb"
    if not formula.is_file():
        pytest.skip("packaging/tide.rb not yet created")
    text = formula.read_text(encoding="utf-8")
    assert "python" in text.lower(), (
        "formula must declare a python dependency (e.g. depends_on 'python@3.12')"
    )


def test_homebrew_formula_exposes_tide_binary():
    """The Homebrew formula installs the ``tide`` binary."""
    repo_root = Path(__file__).resolve().parents[1]
    formula = repo_root / "packaging" / "tide.rb"
    if not formula.is_file():
        pytest.skip("packaging/tide.rb not yet created")
    text = formula.read_text(encoding="utf-8")
    # A formula that installs a pip-backed script typically uses pip_install or
    # a similar helper that names the package, OR references the entry-point directly.
    assert "tide" in text.lower(), (
        "formula must reference the 'tide' binary / package"
    )


def test_homebrew_formula_has_explicit_placeholder_not_fake_sha():
    """The Homebrew formula uses a clear TODO placeholder for sha256, not a fake hash.

    Publishing to PyPI and generating the real sha256 is a human-gated step
    requiring token rotation.  The formula MUST signal this with an explicit
    TODO comment rather than embedding a made-up 64-character hex string that
    looks real.
    """
    repo_root = Path(__file__).resolve().parents[1]
    formula = repo_root / "packaging" / "tide.rb"
    if not formula.is_file():
        pytest.skip("packaging/tide.rb not yet created")
    text = formula.read_text(encoding="utf-8")
    has_placeholder = (
        "TODO" in text
        or "placeholder" in text.lower()
        or "FILL" in text
        or "REPLACE" in text
    )
    assert has_placeholder, (
        "formula must use an explicit placeholder for sha256 — "
        "actual hash requires a published PyPI release (human-gated)"
    )
