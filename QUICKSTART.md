# tide — quickstart

Zero → one closed unit of work. You need **Python ≥ 3.12**.

The whole idea: you **steer in plain words**, an orchestrator session **runs the
commands**, and the result lands as durable truth in plain markdown you can read.
You almost never type the inner verbs yourself — they're shown here only so you can
see what the session is doing on your behalf.

---

## 1. Install

### From source (current, before PyPI/Homebrew release)

```bash
cd tide
./install.sh
tide --version        # tide 0.1.0
```

`install.sh` puts `tide` on your PATH under a 3.12 interpreter (pipx if you have it,
otherwise a private venv + symlink). If `~/.local/bin` isn't on your PATH yet, the
script prints the one line to add.

### Via pip (once published to PyPI — human-gated, requires token rotation)

```bash
pip install tide          # or: pipx install tide
tide --version
```

### Via Homebrew tap (once the release tarball is published)

```bash
brew tap tide-project/tide https://github.com/tide-project/tide
brew install tide-project/tide/tide
tide --version
```

The Homebrew formula lives at `packaging/tide.rb`. The `url` and `sha256` in that
file are placeholders marked `# TODO(publish)` — fill them from the released PyPI
sdist before cutting a tap release.

---

## 2. Unfold a control-home

The control-home is the single place you lead all your projects from. Make an empty
dir and unfold tide into it:

```bash
mkdir ~/control && cd ~/control
tide init --name control
```

You now have a `roster.md` (the projects you lead) and a dogfood `.tide/` — the
control-home is itself a tide project.

---

## 3. Register the projects you lead

Projects live anywhere on disk; the roster just points at them.

```bash
tide roster add myapp ~/code/myapp
tide roster ls
# myapp | /Users/you/code/myapp
```

---

## 4. Open an orchestrator session — then steer in prose

```bash
tide                  # interactive picker → pick the project(s)
tide menu --pick all  # non-interactive
```

This opens a terminal seeded as an **orchestrator** (`TIDE_ROLE=orchestrator`, the
project's cannon + active arc + roster already loaded). From here you **talk**, and
the session does the work:

> **you:** let's ship onboarding — clicking "start" walks the user through 3 steps,
> no console errors.

The orchestrator translates that into the loop:

1. **carves an arc** — one bounded unit of work
   (`tide arc new ship-onboarding` → `tide arc open ship-onboarding`).
2. **binds a contract** — your goal + hard criteria
   (`tide contract new …` → `tide contract sign …`). In a **strict** project it asks
   you to sign first; in **loose** it runs synchronously.
3. **dispatches a worker** — a subagent that builds the work into the arc's
   `output/` and proposes the durable change in `delta.md`. A worker can never merge
   cannon or close a contract.
4. **lands it in front of you** — `tide contract report/proof/accept`, then
   `tide contract close`, which **merges the delta into `CANON.md`**. That merge is
   the single serialization point, and it only happens in a live orchestrator
   session.

You never had to type any of that. You said what you wanted, signed once, and the
truth got reconciled.

---

## 5. See where you are, any time

```bash
tide status            # the current project's STREAM board
tide status --all      # roster-wide; flags unmerged deltas + drift
```

The board flags anything dangling — an unmerged delta, or an open arc still stamped
at an older cannon-rev (drift). The one load-bearing rule: **you cannot open the
next arc while the last closed arc's delta is still unmerged.** Truth is reconciled
before new work begins, every time.

---

## 6. Stop cleanly — handoff

When a session gets heavy, you don't have to remember "what was going on." Say
*"handoff"* / *"let's wrap up"* and the orchestrator distils the thread into the arc
and forks a fresh session already on the focus:

```bash
tide handoff ship-onboarding --mode continue --summary-file <distil>
```

The next session starts on the focus, not on a pile of chat.

---

## See it run for real

`examples/tide-pool/` is a complete, real tide run: a single-file browser game built
**feature-by-feature** through three arcs (`f1-core` → `f2-upgrades` → `f3-persist`),
each one a signed-and-closed contract whose delta was merged before the next arc
could exist. Read **[`examples/tide-pool/SHOWCASE.md`](examples/tide-pool/SHOWCASE.md)**
— it walks the whole loop, including the two moments tide *refused* to open the next
arc because the previous delta was still unmerged. `examples/dogfood-runA/` and
`runB/` are two more finished `.tide/` trees you can `cat`, `grep`, and `diff`.

---

## The loop in one line

```
roster add → tide (orchestrator) → say the goal → arc · contract · worker · cannon-merge → status / handoff
```

Everything is plain markdown under `<project>/.tide/`. Next: read
**[README.md](README.md)** for the full command surface and the on-disk invariants,
or run `tide help`.
