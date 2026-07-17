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
