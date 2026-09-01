"""tide.skills_install — ``tide install-skills``: deliver the tool's skills.

Mirror of ``install-hooks`` (cand 03): the tide skills (tide-flow, tide-work,
offload, …) live in the tool's source checkout under ``skills/`` and are delivered
into ``~/.claude/skills/`` as SYMLINKS by default — so the skill version always
equals the installed tool's source (self-update moves both at once, no manual
``ln``). ``--copy`` materializes real copies instead (for a machine where the
checkout may vanish).

Idempotent and loud: an existing symlink to the right place is "ok"; a FOREIGN
dir/file at a target name is never clobbered — reported and skipped (pass
``--force`` to replace it). A missing source checkout (published-channel install)
is a clear message, not a crash.

A skill may belong to a PLUGIN (work 49). ``tide-work`` is the skill of the works
plugin: the person who does not run works has no use for its instructions, and
the person who switches works on must get them without a manual ``ln``. The skill
declares its owner in its own SKILL.md front-matter::

    ---
    plugin: work
    ---

and this installer reads that: a plugin skill installs only while its plugin is
on (:mod:`tide.plugins`), and OUR symlink for it is removed again when the plugin
goes off. Skills with no ``plugin:`` line are the method's own (tide-flow,
handoff, offload) — core, always installed. Anything unreadable folds into
"install it": a broken registry may never cost someone a working skill.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional, Set, Tuple


def source_skills_dir() -> Optional[Path]:
    """The ``skills/`` dir of the tool's source checkout, or None.

    Resolution mirrors self-update: the install's recorded local checkout first
    (``resolve_source``), then a dev fallback — the enclosing checkout of this
    very file (running from source).
    """
    try:
        from .update.source import resolve_source

        src = resolve_source()
        base = Path(getattr(src, "source_dir", "") or "")
        if str(base) and (base / "skills").is_dir():
            return base / "skills"
    except Exception:  # noqa: BLE001 — published install: no checkout, use fallback
        pass
    dev = Path(__file__).resolve().parents[2] / "skills"
    return dev if dev.is_dir() else None


def default_target_dir() -> Path:
    """Where skills land: ``~/.claude/skills``, or ``$TIDE_SKILLS_DIR`` when set.

    The env override exists because ``tide init`` now delivers the skills itself
    (work 49) — and a test suite that ran ``tide init`` would otherwise reach into
    the developer's REAL ``~/.claude/skills``. Same guard as ``$TIDE_HOME`` /
    ``$CLAUDE_CONFIG_DIR`` in the suite's isolation fixture.
    """
    override = os.environ.get("TIDE_SKILLS_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude" / "skills"


# --- plugin ownership ------------------------------------------------------

def skill_plugin(skill_dir: Path) -> Optional[str]:
    """The plugin a skill belongs to, from its SKILL.md front-matter, or None.

    Reads only the leading ``---`` block and only a bare ``plugin: <name>`` line.
    Deliberately dumb (no YAML dependency — the runtime is stdlib-only) and
    deliberately forgiving: an unreadable file, an absent block and a malformed
    line all read back as "no plugin", i.e. a core skill that always installs.
    """
    try:
        text = (Path(skill_dir) / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() == "plugin":
            value = value.strip().strip('"').strip("'").lower()
            if value:
                return value
    return None


def _points_at(dest: Path, skill: Path) -> bool:
    """True when *dest* is our own symlink for *skill* (a dangling link is not)."""
    try:
        return dest.resolve() == Path(skill).resolve()
    except OSError:
        return False


def _plugins_on() -> Optional[Set[str]]:
    """Plugin names switched on for this person, or None when we cannot tell.

    None means "no opinion" — every skill installs. That is the safe answer for
    an install run outside a control-home, and for any future in which the
    plugins module moves or changes shape.
    """
    try:
        from . import plugins as _plugins

        return set(_plugins.enabled())
    except Exception:  # noqa: BLE001 — no home, no module: never block an install
        return None


def _skill_wanted(skill_dir: Path, plugins_on: Optional[Set[str]]) -> Tuple[bool, str]:
    """(install it?, plugin name) for one skill directory."""
    owner = skill_plugin(skill_dir)
    if owner is None or plugins_on is None:
        return True, owner or ""
    try:
        from . import plugins as _plugins

        if _plugins.part(owner) is None:  # unknown owner — not ours to withhold
            return True, owner
    except Exception:  # noqa: BLE001
        return True, owner
    return owner in plugins_on, owner


def install_skills(
    *,
    source: Optional[Path] = None,
    target: Optional[Path] = None,
    copy: bool = False,
    force: bool = False,
    plugins_on: Optional[Set[str]] = None,
    all_plugins: bool = False,
) -> List[Tuple[str, str]]:
    """Deliver every ``skills/<name>/SKILL.md`` skill into *target*.

    Returns ``[(name, verdict)]`` where verdict is one of ``linked`` / ``copied`` /
    ``ok`` (already correct) / ``replaced`` / ``removed: …`` / ``skipped: …``.
    Raises ``ValueError`` when no source checkout is available (published-channel
    install).

    *plugins_on* names the plugins switched on (default: ask :mod:`tide.plugins`);
    *all_plugins* ignores the registry and installs every skill in the source.
    """
    src_dir = Path(source) if source else source_skills_dir()
    if src_dir is None or not Path(src_dir).is_dir():
        raise ValueError(
            "install-skills: нет локального чекаута с skills/ — установка из "
            "опубликованного канала; склонируй репо tide и задай $TIDE_SOURCE"
        )
    tgt_root = Path(target) if target else default_target_dir()
    tgt_root.mkdir(parents=True, exist_ok=True)
    if all_plugins:
        on: Optional[Set[str]] = None
    elif plugins_on is not None:
        on = set(plugins_on)
    else:
        on = _plugins_on()
    out: List[Tuple[str, str]] = []
    for skill in sorted(p for p in Path(src_dir).iterdir()
                        if p.is_dir() and (p / "SKILL.md").is_file()):
        dest = tgt_root / skill.name
        wanted, owner = _skill_wanted(skill, on)
        if not wanted:
            # The plugin is off. Our own symlink comes back out (the skill leaves
            # with its plugin); anything else at that name is the person's — the
            # installer that never clobbers also never deletes.
            if dest.is_symlink() and _points_at(dest, skill):
                dest.unlink()
                out.append((skill.name, "removed: плагин {0} выключен".format(owner)))
            else:
                out.append((skill.name, "skipped: плагин {0} выключен".format(owner)))
            continue
        if dest.is_symlink():
            if _points_at(dest, skill) and not copy:
                out.append((skill.name, "ok"))
                continue
            dest.unlink()  # наша же ссылка (или устаревшая) — перевешиваем
            verdict = "replaced"
        elif dest.exists():
            if not force:
                out.append((skill.name, "skipped: занято не-симлинком (--force заменит)"))
                continue
            shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
            verdict = "replaced"
        else:
            verdict = "copied" if copy else "linked"
        if copy:
            shutil.copytree(skill, dest)
            out.append((skill.name, verdict if verdict == "replaced" else "copied"))
        else:
            dest.symlink_to(skill.resolve())
            out.append((skill.name, verdict if verdict == "replaced" else "linked"))
    return out


def _cmd_install_skills(args) -> int:
    try:
        results = install_skills(
            source=Path(args.source) if getattr(args, "source", None) else None,
            target=Path(args.target) if getattr(args, "target", None) else None,
            copy=bool(getattr(args, "copy", False)),
            force=bool(getattr(args, "force", False)),
            all_plugins=bool(getattr(args, "all", False)),
        )
    except ValueError as exc:
        print("tide: {0}".format(exc))
        return 1
    for name, verdict in results:
        print("tide: skill {0}: {1}".format(name, verdict))
    if not results:
        print("tide: install-skills: в skills/ источника пусто")
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "install-skills",
        help="deliver the tool's skills (skills/*) into ~/.claude/skills — "
             "symlinks by default, so skill version = tool version (cand 03); "
             "a skill declaring `plugin:` rides its plugin (work 49)",
    )
    p.add_argument("--copy", action="store_true",
                   help="copy instead of symlink (checkout may vanish)")
    p.add_argument("--force", action="store_true",
                   help="replace a foreign dir/file occupying a target name")
    p.add_argument("--all", action="store_true",
                   help="install plugin skills too, whatever the plugin registry says")
    p.add_argument("--source", help="override the skills/ source dir")
    p.add_argument("--target", help="override the target dir (default ~/.claude/skills)")
    p.set_defaults(func=_cmd_install_skills, _cmd="install-skills")
