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
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Tuple


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
    return Path.home() / ".claude" / "skills"


def install_skills(
    *,
    source: Optional[Path] = None,
    target: Optional[Path] = None,
    copy: bool = False,
    force: bool = False,
) -> List[Tuple[str, str]]:
    """Deliver every ``skills/<name>/SKILL.md`` skill into *target*.

    Returns ``[(name, verdict)]`` where verdict is one of ``linked`` / ``copied`` /
    ``ok`` (already correct) / ``replaced`` / ``skipped: …``. Raises ``ValueError``
    when no source checkout is available (published-channel install).
    """
    src_dir = Path(source) if source else source_skills_dir()
    if src_dir is None or not Path(src_dir).is_dir():
        raise ValueError(
            "install-skills: нет локального чекаута с skills/ — установка из "
            "опубликованного канала; склонируй репо tide и задай $TIDE_SOURCE"
        )
    tgt_root = Path(target) if target else default_target_dir()
    tgt_root.mkdir(parents=True, exist_ok=True)
    out: List[Tuple[str, str]] = []
    for skill in sorted(p for p in Path(src_dir).iterdir()
                        if p.is_dir() and (p / "SKILL.md").is_file()):
        dest = tgt_root / skill.name
        if dest.is_symlink():
            if dest.resolve() == skill.resolve() and not copy:
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
             "symlinks by default, so skill version = tool version (cand 03)",
    )
    p.add_argument("--copy", action="store_true",
                   help="copy instead of symlink (checkout may vanish)")
    p.add_argument("--force", action="store_true",
                   help="replace a foreign dir/file occupying a target name")
    p.add_argument("--source", help="override the skills/ source dir")
    p.add_argument("--target", help="override the target dir (default ~/.claude/skills)")
    p.set_defaults(func=_cmd_install_skills, _cmd="install-skills")
