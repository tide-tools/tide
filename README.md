# tide

**A simplified orchestration machine. Pure CLI + markdown — nothing else.**

One `tide` binary leads all your projects from a single control-home. No web
surface, no Telegram, no background daemon, no autonomy: it is **synchronous and
human-driven**. You steer; an agent runs the module CLI. State lives in plain
markdown files you can read, diff, and grep — not in a chat or a database.

```
tide init  →  control-home (roster + dogfood .tide/)
tide        →  pick projects → launch a seeded orchestrator session
arc · contract · cannon  →  do the work, bind it, fold the truth back in
```

> **Requires Python ≥ 3.12.** Runtime is **stdlib-only** (argparse, no `click`,
> no web deps). Your system `python3` may be older, so install under a 3.12
> interpreter — `install.sh` handles that for you.

---

## Install

### Option A — `install.sh` (recommended)

Puts `tide` on your PATH under a Python ≥ 3.12 interpreter. Uses `pipx` if you
have it, otherwise a dedicated venv + a symlink into `~/.local/bin`. Idempotent;
prints the resulting version when done.

```bash
git clone <this repo> tide && cd tide
./install.sh
# › using Python 3.12.x
# ✓ tide 0.1.0
```

Knobs (all optional):

| env var | default | meaning |
|---|---|---|
| `TIDE_PYTHON` | auto-detect | force a specific interpreter |
| `TIDE_HOME` | `~/.local/share/tide` | where the fallback venv lives |
| `TIDE_BIN_DIR` | `~/.local/bin` | where the `tide` symlink is placed |

### Option B — pipx / pip, by hand

```bash
pipx install --python python3.12 .        # isolated app install
# or, into the current environment:
python3.12 -m pip install .               # runtime (stdlib only)
python3.12 -m pip install '.[test]'       # + pytest for the suite
tide --version
# or without the console script:
python3.12 -m tide --version
```

---

## The 60-second loop

```bash
# 1. unfold a control-home in an empty dir — this dir is where you lead from
mkdir ~/control && cd ~/control
tide init --name control

# 2. register the projects you actually lead (they live anywhere on disk)
tide roster add myapp ~/code/myapp
tide roster ls

# 3. launch a seeded ORCHESTRATOR session over the projects you pick
tide                      # interactive menu
tide menu --pick all      # non-interactive
#  → opens a terminal with TIDE_ROLE=orchestrator and the right context.

# --- from here on, the agent runs the module CLI; you steer in prose ---

# 4. open a unit of work (an arc) inside a project — arcs are addressed by SLUG
tide arc new ship-onboarding
tide arc open ship-onboarding

# 5. bind the work to a contract (goal + criteria), then run it
tide contract new ship-onboarding
tide contract sign ship-onboarding     # strict = human signs; loose = synchronous

# 6. fold the result back into durable truth (orchestrator-only)
tide contract report ship-onboarding   # what was done
tide contract proof ship-onboarding    # evidence the criteria are met
tide contract accept ship-onboarding
# the worker proposes the cannon-delta in the arc's delta.md, then:
tide contract close ship-onboarding    # guards + merges the delta → CANON.md

# board, any time
tide status            # current project
tide status --all      # roster-wide; flags unmerged deltas + drift
```

`arc` carves the work, `contract` binds it to a goal you can sign off on, and
`cannon` is the single place durable truth accumulates. The merge from an arc's
`output/` into `CANON.md` is the **one serialization point** — and it only
happens inside a live orchestrator session.

---

## Why this shape — the UNIX-like pitch

tide is built like a small UNIX tool, on purpose:

- **One binary, namespaced subcommands.** `tide arc …`, `tide cannon …`,
  `tide contract …` compose the way `git <verb>` does. Each module owns its
  group via a thin `register(subparsers)`; `cli.py` only wires.
- **Plain text is the database.** Everything is markdown under
  `<project>/.tide/`. No daemon, no server, no lock file. `cat`, `grep`, `diff`,
  and `git` all just work on your state.
- **Do one thing, pipe-friendly.** Handlers stay thin (I/O only); the real logic
  is argparse-free functions you can unit-test in isolation. Synchronous, exit
  codes, no hidden background magic.
- **Least privilege by default.** Role is carried in one env var, `TIDE_ROLE`
  (`worker` by default). Orchestrator-only operations — `cannon merge`,
  `candidate promote`, `contract close` — refuse to run unless
  `TIDE_ROLE=orchestrator`.
- **Composable, not a platform.** tide doesn't host your projects; it's the
  point you *lead* them from. They live wherever they live; tide just holds the
  thread.

tide dogfoods itself — it is led as a tide project, in its own `.tide/`.

---

## Command surface

| group | what it does |
|---|---|
| `init` | unfold a control-home (roster + dogfood `.tide/`) or `--project` for a bare per-project `.tide/` |
| `roster add\|rm\|ls` | register / list the projects you lead |
| `menu` (`tide` with no args) | pick N projects → launch seeded sessions (`--pick`, `--adapter`, `--dry-run`) |
| `status [--all]` | the STREAM board; flags unmerged cannon-deltas and drift on open arcs |
| `arc new\|open\|resume\|close\|reopen\|supersede\|status` | the numbered work stream (`new-goal` nests a substream) |
| `candidate` | capture / list / **promote** future-work ideas (separate backlog) |
| `cannon init\|status\|merge\|rev` | durable truth; `merge` and a fresh `rev` are the truth-update path |
| `contract new\|sign\|report\|proof\|accept\|close\|reopen\|state\|list\|ask\|answer` | worker→arc binding + open-questions |
| `strictness [strict\|loose]` | per-project dispatch dial (default `strict`) |
| `install-hooks` | merge-safe wiring of the Claude Code hooks into `.claude/settings.json` |
| `handoff` | warm-handoff: distil chat → arc workspace, then fork |
| `context show` | the deterministic **on-entry view**: tool-context + read-order + open arcs/candidates |
| `terminal` | exec a clean, logged-in, seeded session **in this terminal** (`--dry-run`, `--no-skip-permissions`) |

The human steers; **the agent runs the module CLI**. You never type the inner
verbs by hand.

### Entering a project — the context-loading strategy

A fresh session shouldn't have to be told where it is. `tide context show`
**deterministically explains a project on entry**, reading a small declarative
strategy from `<project>/.tide/state/context.json` (every key optional):

| key | half | meaning |
|---|---|---|
| `strict_mcp` / `mcp_config` / `allowed_tools` / `extra_args` | **tool** | what the session loads (written by `chandler add`) |
| `read_first` | **strategy** | orientation read-order; unset ⇒ compute the default (`CLAUDE.md` + `cannon/CANON.md`, only those present) |
| `surface_on_entry` | **strategy** | show the open-arcs/candidates summary on entry (default `true`) |

The two halves coexist in one file and never clobber each other (unknown keys
round-trip). `context show` prints the resolved tool-context, the `read_first`
order (missing files flagged), and a summary of **open arcs + candidates**
computed from `.tide/arcs/` — so a session lands and the project states what to
load, what to read, and what work is live. A legacy pre-tide `.arcs/` dir is
noted, not summarized.

```jsonc
// .tide/state/context.json — strategy half (tool half written by chandler)
{ "read_first": ["CLAUDE.md", "docs/ARCHITECTURE.md"], "surface_on_entry": true }
```

**`tide terminal`** drops you into a clean, scoped, still-logged-in session in
the current terminal. It adds `--dangerously-skip-permissions` by default — a
**deliberate operator choice for the interactive head** (the human-driven
coordinator, where constant prompts kill the flow), **not** for autonomous or
spawned workers (those go through the menu/Orca path, which never adds it). Opt
out with `--no-skip-permissions`; inspect the exact argv with `--dry-run`.

### Two roles

| | **orchestrator** | **worker** |
|---|---|---|
| scope | cross-project session | one arc |
| owns | roster, arc create/select, contracts, **cannon merge**, candidate **promote**, handoff | produce arc output, surface candidates, **propose cannon-delta** |
| never | does project work directly | merges cannon, touches another arc |

The worker is a subagent inside the orchestrator session. The launcher sets
`TIDE_ROLE`.

---

## Where state lives

Per project, under **`<project>/.tide/`**:

| dir | holds |
|---|---|
| `cannon/` | `CANON.md` (living IS) + `config` (`lang=en`); durable truth, notes/changelog/goals folded in |
| `arcs/` | the numbered stream `NN-<slug>/` (arc) and `NN-@<slug>/` (goal); `arcs/candidates/` is a separate backlog |
| `state/` | `strictness` + cannon-rev stamps + contract index |

The control-home (where `tide init` ran) adds a top-level **`roster.md`**
(`name | path` lines) and its own dogfood `.tide/`.

**On-disk invariants** (don't get these subtly wrong):

- **Frontmatter** = first line matching `^key:`; `prev:` is a read-only alias of
  `supersedes:`.
- **Closed entry** = wrapped dir `__NN-<slug>__` **AND** `status: done` — both
  must agree (dual marking).
- **Numbering** counts both `NN-*/` and `__NN-*__/` and **never reuses** a number
  (closing renames, never frees). Candidates have a **separate** counter; goal
  sub-arcs use their own local `01,02…` stream.
- **cannon-rev** = short sha256 of **`CANON.md` only**, so note/changelog tweaks
  don't spam drift.
- **Encapsulation**: an arc is `input/` → `workspace/` (disposable) → `output/`;
  outside reads `output/` only. The merge into `CANON.md` is the single
  serialization point.

---

## Tests

```bash
cd tide
python3.12 -m pytest tests/ -q
```

The suite is cumulative and must stay green. `pyproject.toml` puts `src/` on the
test path, so `import tide` works without an editable install.

---

See **[QUICKSTART.md](QUICKSTART.md)** for a 5-minute hands-on, and `tide help`
for the full command tree.
