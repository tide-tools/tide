# tide — the first hour

*Русская версия: [QUICKSTART.ru.md](QUICKSTART.ru.md)*

One pass: install → home → project → board → your first closed work.
Every step says what you'll see. Needs Python ≥ 3.12; step 6 needs
[Claude Code](https://claude.com/claude-code) (tide's own machinery works without it).

The commands are spelled out here so you feel the cycle by hand. In real work an
agent in a session walks them — you speak in words.

---

## 1. Install

```bash
git clone https://github.com/tide-tools/tide && cd tide
./install.sh
```

**You'll see:** `✓ tide <version>` and a hint for the next gesture. If PATH
didn't pick it up, the script prints one line to add to your shell profile —
and that's the only thing here you ever "configure".

## 2. Unfold a home

The home (control-home) is one folder you lead every project from:

```bash
mkdir ~/tide-home && cd ~/tide-home
tide init --git
```

**You'll see** a list of what was created:

```
tide: tide control-home ready at /Users/you/tide-home
  + canon/CANON.md
  + state/strictness
  + .tide/
  + roster.md
  + .tide/plugins (core only)
  + README.md
  + git repo (birth commit)
  + Claude hooks → …/.claude/settings.json (6)
  + skills → …/skills: handoff, offload, tide-flow
```

Hooks and skills landed on their own — an agent opened in this home knows how to
hand off the thread and offload from day one. A fresh home gets the core only;
removable parts live in `tide plugins`.

Now tell the shell where the home is — otherwise the next step will create a
project but won't write it into the roster. It isn't an error, just one skipped
line in the middle of an otherwise successful run:

```
  · roster  no control-home — set $TIDE_HOME or run 'tide init' somewhere
```

Miss that line and the project never shows up on the board.

```bash
export TIDE_HOME=~/tide-home
```

Put the same line in your shell profile so `tide` finds the home from any folder
and in later sessions.

## 3. Adopt a project

A project is any folder with code, old or new:

```bash
mkdir -p ~/code/myapp && cd ~/code/myapp   # or step into a project you already have
tide adopt --goal "a small web app — trying tide"
```

**You'll see** the adoption steps:

```
tide: adopted myapp at /Users/you/code/myapp
  ✓ git     git init
  ✓ tide    scaffolded .tide/ (canon seeded with the goal)
  ✓ readme  README.md generated from canon
  ✓ commit  first commit (worktree-ready)
  · orca    orca CLI not on PATH — optional terminal manager, tide works without it
  ✓ roster  rostered → /Users/you/tide-home
ready: tide menu → myapp
```

The project is born speaking: its README and canon carry your goal, not template
filler. It's already in the home's roster. The `orca` line is about Orca, an
optional terminal manager for macOS: if you don't have it the step is simply
skipped and everything works anyway (to skip it entirely — `tide adopt --no-orca`).

## 4. Open the board

```bash
tide board --open
```

**You'll see** in the browser (http://127.0.0.1:8765): the home's stream, myapp's
stream, a HEALTH line on each. The page re-reads `.tide/` every 30 seconds —
everything you do next shows up there without a restart. The port is a flag,
`--port`; for your phone and as a service — [docs/board.md](docs/board.md).

## 5. First work — all the way to closed

A work is an agreement card: the agent proposes steps, you say yes, the agent
checks items only with proof, and you close it. The `tide work …` commands always
work, out of the box. The removable `work` part adds the tide-work skill for the
agent and a works tab on the board — switch it on so the agent already knows this
cycle in step 6 (once per home):

```bash
tide plugins on work        # skill for the agent + a place on the board; the verbs work without it
```

Walk one work by hand to see the cycle:

```bash
cd ~/code/myapp
tide work add "the app says hello — check that it starts"
tide work propose 01 "start the app and see it answer"   # an agent gesture: a proposal
tide work agree 01 --word "yes"                          # your word
tide work take 01 --by "first session"
tide work check 01 1 --proof "started it — it answers"   # a check won't pass without proof
tide work close 01 --word "accepted"                     # done is set by the human alone
```

**You'll see** a short answer after each gesture — what happened and a hint for
the next one (usually two lines) — and on the board, a card that went
open → taken → review → done. `tide work show 01` prints the journal: every
gesture is a line, with the human's words in it.

## 6. A session — from here on, in words

```bash
cd ~/tide-home
tide menu
```

**You'll see** a project picker; choose myapp. The second question is the
thread — `Thread for myapp — continue one, or start new:`, with
`0) + new thread` as the first row of the list. Press Enter for a new one and
name it (a thread is a line of work inside a project). The third is the session
inside that thread — `Session in thread … — continue one, or start new:`, with
`0) + new session` there too. Enter again. A terminal opens
with Claude already in context: its role, the project's canon, the live thread,
the roster. From here you say what you want ("add a work: …", "hand off the
thread", "show me the status") — the agent walks the verbs from step 5 itself.

Sessions from `tide menu` open with full tool permissions
(`--dangerously-skip-permissions`) — a deliberate default for an interactive
head; to get the usual confirmations back, `tide menu --no-skip-permissions`.

---

Want to go deeper: `tide help` — every command; [README.md](README.md) — the
whole model; [docs/board.md](docs/board.md) — the board as a service and on your
phone. Broken? `tide report "what happened"`: the source is yours, the pain goes
to the author.
