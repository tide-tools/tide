"""Derive the thread's real state from disk and from the CLI.

Two kinds of fact live here.

DERIVED — read straight off the machine every run: step numbers, decisions by
state, works in review, the cursor line, the three versions. These never go
stale, so the benchmark can be re-run in a month against the same questions and
still be fair.

CURATED — the handful of answers that only a reader can produce (today: just
what is left between now and the thread's finish). It lives in ref/<thread>.json
with the date it was written and a pointer to the file it was read from.
"""

import json
import re
import subprocess
from pathlib import Path

# Бенчмарк лежит В движке: bench/cold_session/facts.py → корень репозитория.
ENGINE = Path(__file__).resolve().parents[2]
EMPTY = ("—", "", "-")


def _read(p):
    return Path(p).read_text(encoding="utf-8")


def _cli(args, cwd):
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=120)
        return r.stdout
    except Exception:
        return ""


def derive(project: Path, thread_dir: Path, session_dir: Path):
    plan = _read(thread_dir / "plan.md")
    decisions = _read(thread_dir / "decisions.md")
    arc = _read(session_dir / "arc.md")
    goal_file = next(thread_dir.glob("*-goal.md"), None)

    # Решение несёт две оси: status: (accepted|superseded|dropped) и отдельно
    # done: с датой плюс proof:. `kind: rule` — стоящее правило, выполненности
    # у него не бывает. На baseline осей не было — у всех стоял один `open`,
    # и «что выполнено» из машины не читалось вовсе: на этом легли все три
    # сессии условия Б.
    dec = []
    for blk in re.split(r"^## (?=\d\d )", decisions, flags=re.M)[1:]:
        rec = {}
        for fld in ("status", "kind", "done", "proof", "work", "closes"):
            m = re.search(r"^{0}:\s*(.*)$".format(fld), blk, re.M)
            if m:
                rec[fld] = m.group(1).strip()
        dec.append((blk[:2], rec))

    # Шаг несёт три состояния: [x] закрыт, [ ] открыт, [~] снят — растворился и
    # больше не считается остатком.
    steps = [(m.group(1), m.group(2), m.group(3).strip())
             for m in re.finditer(r"^- \[([ x~])\] (\d+)\.\s*([^|]+)", plan, re.M)]

    # Текущим может быть не один шаг: «текущий шаг — 9 (…) и 10 (…)».
    line = re.search(r"^##\s*текущий шаг\s*—\s*(.+)$", plan, re.M)
    pairs = re.findall(r"(\d+)\s*\(([^)]+)\)", line.group(1)) if line else []
    cur_nos = [n for n, _ in pairs] or ["?"]
    cur_name = " и ".join(t for _, t in pairs) or "?"

    goal = "?"
    if goal_file:
        g = re.search(r"^goal:\s*(.+)$", _read(goal_file), re.M)
        if g:
            goal = g.group(1).strip()

    cur = re.search(r"^## cursor.*?\n(.+?)(?:\n\n|\n##)", arc, re.S | re.M)

    # Работы в review: `tide work list` считает по всем нитям, экран нити —
    # по своей. Оба ответа верные; судья принимает любой.
    works = _cli(["tide", "work", "list"], cwd=project)
    review = [ln.split()[0] for ln in works.splitlines() if re.search(r"\breview\b", ln)]
    mine = []
    for r in review:
        card = project / ".tide/arcs/works" / r / "work.md"
        if card.exists() and thread_dir.name in _read(card):
            mine.append(r.split("-")[0])

    # Три версии, не две. На baseline исходник и последний релиз совпадали, и
    # путать их было безнаказанно; когда выкат идёт, исходник обгоняет релиз.
    # Вопрос 8 спрашивает про ВЫКАЧЕННУЮ — это тег, а не pyproject.
    cli_v = (_cli(["tide", "--version"], cwd=project).strip().split() or ["?"])[-1]
    src_v = "?"
    if (ENGINE / "pyproject.toml").exists():
        sm = re.search(r'^version\s*=\s*"([^"]+)"', _read(ENGINE / "pyproject.toml"), re.M)
        if sm:
            src_v = sm.group(1)
    tags = _cli(["/usr/bin/git", "tag", "--sort=-v:refname"], cwd=ENGINE).split()
    rel_v = next((t.lstrip("v") for t in tags if re.fullmatch(r"v?\d+\.\d+\.\d+", t)), "?")

    ws = session_dir / "workspace"

    return {
        "project": str(project),
        "thread_name": thread_dir.name,
        "thread_goal": goal,
        "steps_total": len(steps),
        "current_step_no": cur_nos[0],
        "current_step_nos": cur_nos,
        "current_step_name": cur_name,
        "current_step_words": [w for w in cur_name.split() if len(w) > 3] or [cur_name],
        "open_steps": [(n, t) for mark, n, t in steps if mark == " "],
        "withdrawn_steps": [(n, t) for mark, n, t in steps if mark == "~"],
        "decisions_total": len(dec),
        "decisions_not_done": [n for n, r in dec if r.get("status") == "accepted"
                               and r.get("kind") != "rule"
                               and r.get("done", "—") in EMPTY],
        "decisions_done": [n for n, r in dec if r.get("done", "—") not in EMPTY],
        "decisions_rules": [n for n, r in dec if r.get("kind") == "rule"],
        "rejected": ["{0} — {1}".format(n, r["closes"])
                     for n, r in dec if r.get("closes", "—") not in EMPTY],
        "cursor": cur.group(1).strip() if cur else "?",
        "review_count": len(review),
        "review_ids": [r.split("-")[0] for r in review],
        "review_count_thread": len(mine),
        "review_ids_thread": mine,
        "workspace_rel": str(ws.relative_to(project)),
        "workspace_files": sorted(p.name for p in ws.glob("*.md")) if ws.exists() else [],
        "arc_rel": str((session_dir / "arc.md").relative_to(project)),
        "cli_version": cli_v,
        "source_version": src_v,
        "released_version": rel_v,
    }


def facts(project, thread_dir, session_dir, ref_path):
    f = derive(Path(project), Path(thread_dir), Path(session_dir))
    f.update(json.loads(_read(Path(ref_path))))
    return f
