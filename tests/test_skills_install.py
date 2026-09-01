"""cand 03 — tide install-skills: skills/* доезжают симлинками, версия = версии тула."""

from __future__ import annotations

from pathlib import Path

from tide import skills_install as si


def _fixture_source(tmp_path):
    src = tmp_path / "checkout" / "skills"
    for name in ("tide-flow", "offload"):
        d = src / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# {0}\n".format(name), encoding="utf-8")
    (src / "not-a-skill").mkdir()  # без SKILL.md — не скилл, не ставится
    return src


def test_install_symlinks_and_is_idempotent(tmp_path):
    src = _fixture_source(tmp_path)
    tgt = tmp_path / "claude-skills"
    out = dict(si.install_skills(source=src, target=tgt))
    assert out == {"tide-flow": "linked", "offload": "linked"}
    assert (tgt / "tide-flow").is_symlink()
    assert (tgt / "tide-flow" / "SKILL.md").read_text(encoding="utf-8").startswith("# tide-flow")
    assert not (tgt / "not-a-skill").exists()
    # второй прогон — ок, ничего не ломает
    assert dict(si.install_skills(source=src, target=tgt)) == {"tide-flow": "ok", "offload": "ok"}


def test_foreign_dir_is_skipped_without_force(tmp_path):
    src = _fixture_source(tmp_path)
    tgt = tmp_path / "claude-skills"
    foreign = tgt / "tide-flow"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("чужой скилл", encoding="utf-8")
    out = dict(si.install_skills(source=src, target=tgt))
    assert out["tide-flow"].startswith("skipped")
    assert (foreign / "SKILL.md").read_text(encoding="utf-8") == "чужой скилл"
    out = dict(si.install_skills(source=src, target=tgt, force=True))
    assert out["tide-flow"] == "replaced"
    assert (tgt / "tide-flow").is_symlink()


def test_copy_mode_materializes(tmp_path):
    src = _fixture_source(tmp_path)
    tgt = tmp_path / "claude-skills"
    out = dict(si.install_skills(source=src, target=tgt, copy=True))
    assert out == {"tide-flow": "copied", "offload": "copied"}
    assert (tgt / "offload").is_dir() and not (tgt / "offload").is_symlink()


def test_no_source_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "source_skills_dir", lambda: None)
    try:
        si.install_skills(target=tmp_path / "t")
        assert False, "must raise"
    except ValueError as exc:
        assert "TIDE_SOURCE" in str(exc)


# --- work 49: plugin skills ride their plugin -------------------------------

def _plugin_source(tmp_path):
    """A source with one core skill and one that declares `plugin: work`."""
    src = tmp_path / "checkout" / "skills"
    core = src / "handoff"
    core.mkdir(parents=True)
    (core / "SKILL.md").write_text(
        "---\nname: handoff\n---\n\n# handoff\n", encoding="utf-8")
    plug = src / "tide-work"
    plug.mkdir(parents=True)
    (plug / "SKILL.md").write_text(
        "---\ntagline: \"w\"\nplugin: work\nname: tide-work\n---\n\n# tide-work\n",
        encoding="utf-8")
    return src


def test_skill_plugin_reads_front_matter(tmp_path):
    src = _plugin_source(tmp_path)
    assert si.skill_plugin(src / "tide-work") == "work"
    assert si.skill_plugin(src / "handoff") is None
    # no front matter at all, and a missing file: both are "core"
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "SKILL.md").write_text("# bare\n", encoding="utf-8")
    assert si.skill_plugin(bare) is None
    assert si.skill_plugin(tmp_path / "nowhere") is None


def test_plugin_skill_installs_while_its_plugin_is_on(tmp_path):
    src = _plugin_source(tmp_path)
    tgt = tmp_path / "claude-skills"
    out = dict(si.install_skills(source=src, target=tgt, plugins_on={"work"}))
    assert out == {"handoff": "linked", "tide-work": "linked"}
    assert (tgt / "tide-work").is_symlink()


def test_plugin_skill_stays_home_while_its_plugin_is_off(tmp_path):
    src = _plugin_source(tmp_path)
    tgt = tmp_path / "claude-skills"
    out = dict(si.install_skills(source=src, target=tgt, plugins_on=set()))
    assert out["handoff"] == "linked"          # core skill unaffected
    assert out["tide-work"].startswith("skipped: плагин work")
    assert not (tgt / "tide-work").exists()


def test_switching_the_plugin_off_takes_our_symlink_back_out(tmp_path):
    src = _plugin_source(tmp_path)
    tgt = tmp_path / "claude-skills"
    si.install_skills(source=src, target=tgt, plugins_on={"work"})
    assert (tgt / "tide-work").is_symlink()
    out = dict(si.install_skills(source=src, target=tgt, plugins_on=set()))
    assert out["tide-work"].startswith("removed: плагин work")
    assert not (tgt / "tide-work").exists()
    # and back on again
    assert dict(si.install_skills(source=src, target=tgt,
                                  plugins_on={"work"}))["tide-work"] == "linked"


def test_plugin_off_never_deletes_someone_elses_dir(tmp_path):
    """The installer that never clobbers also never deletes what isn't ours."""
    src = _plugin_source(tmp_path)
    tgt = tmp_path / "claude-skills"
    foreign = tgt / "tide-work"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("чужой скилл", encoding="utf-8")
    out = dict(si.install_skills(source=src, target=tgt, plugins_on=set()))
    assert out["tide-work"].startswith("skipped: плагин work")
    assert (foreign / "SKILL.md").read_text(encoding="utf-8") == "чужой скилл"


def test_all_plugins_ignores_the_registry(tmp_path):
    src = _plugin_source(tmp_path)
    tgt = tmp_path / "claude-skills"
    out = dict(si.install_skills(source=src, target=tgt,
                                 plugins_on=set(), all_plugins=True))
    assert out["tide-work"] == "linked"


def test_unknown_plugin_name_is_never_withheld(tmp_path):
    """A skill naming a plugin the catalogue doesn't know still installs."""
    src = _plugin_source(tmp_path)
    odd = src / "odd-skill"
    odd.mkdir()
    (odd / "SKILL.md").write_text(
        "---\nplugin: no-such-plugin\n---\n", encoding="utf-8")
    tgt = tmp_path / "claude-skills"
    out = dict(si.install_skills(source=src, target=tgt, plugins_on=set()))
    assert out["odd-skill"] == "linked"


def test_target_dir_honours_the_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("TIDE_SKILLS_DIR", str(tmp_path / "elsewhere"))
    assert si.default_target_dir() == tmp_path / "elsewhere"
    monkeypatch.setenv("TIDE_SKILLS_DIR", "")
    assert si.default_target_dir() == Path.home() / ".claude" / "skills"


# --- the package actually carries the cycle's skills -------------------------

def test_package_ships_the_whole_cycle():
    """handoff/offload/tide-flow ship as core; tide-work rides the work plugin;
    the dissolved tide-routines is gone (work 49)."""
    src = si.source_skills_dir()
    assert src is not None, "dev checkout must expose skills/"
    names = {p.name for p in src.iterdir() if (p / "SKILL.md").is_file()}
    assert {"handoff", "offload", "tide-flow", "tide-work"} <= names
    assert "tide-routines" not in names
    assert si.skill_plugin(src / "tide-work") == "work"
    for core in ("handoff", "offload", "tide-flow"):
        assert si.skill_plugin(src / core) is None
