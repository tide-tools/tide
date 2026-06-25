# tide — 5-minute quickstart

A hands-on walk from zero to a closed unit of work. Every command below is real
and was run end-to-end. You need **Python ≥ 3.12**.

---

## 0. Install (≈ 30s)

```bash
cd tide
./install.sh
# › using Python 3.12.x (/opt/homebrew/bin/python3.12)
# ✓ tide 0.1.0
```

If `~/.local/bin` isn't on your PATH yet, the script tells you the one line to
add. Then:

```bash
tide --version        # tide 0.1.0
```

> No pipx? `install.sh` builds a private venv and symlinks the binary — you don't
> need anything but a 3.12 interpreter. Have pipx? It uses that instead.

---

## 1. Unfold a control-home (≈ 30s)

The control-home is the one place you lead everything from. Make an empty dir and
unfold tide into it:

```bash
mkdir ~/control && cd ~/control
tide init --name control
# tide: tide control-home ready at ~/control
#   + cannon/CANON.md
#   + state/strictness
#   + .tide/
#   + roster.md
```

You now have a `roster.md` (the projects you lead) and a dogfood `.tide/` (the
control-home is itself a tide project).

---

## 2. Register the projects you lead (≈ 30s)

Projects live anywhere on disk; the roster just points at them.

```bash
tide roster add myapp ~/code/myapp
tide roster ls
# myapp | /Users/you/code/myapp
```

Each rostered project gets its own `.tide/` the first time you work it.

---

## 3. Launch an orchestrator session (≈ 30s)

This is how you actually drive: pick projects, and tide opens a terminal seeded
as an **orchestrator** (it sets `TIDE_ROLE=orchestrator` and loads context).

```bash
tide                          # interactive picker
tide menu --pick all          # non-interactive
tide menu --pick all --dry-run   # see the seeds + commands without opening a terminal
```

Inside that session, **the agent runs the module CLI** (`tide arc …`,
`tide contract …`, `tide cannon …`). You steer in plain language. For this
walkthrough we'll run the verbs ourselves so you can see them.

> The inner verbs are role-gated. `cannon merge`, `candidate promote`, and
> `contract close` refuse to run unless `TIDE_ROLE=orchestrator`. Set it for the
> rest of this walkthrough: `export TIDE_ROLE=orchestrator`.

---

## 4. Carve a unit of work — an arc (≈ 1m)

An **arc** is one bounded piece of work: `input/` → `workspace/` (disposable) →
`output/`. Outsiders read `output/` only.

```bash
cd ~/code/myapp            # or stay in the control-home to dogfood
tide arc new ship-onboarding
# tide: created arc .../.tide/arcs/01-ship-onboarding

tide arc open ship-onboarding     # select it as active; stamps the cannon-rev
# tide: opened .../01-ship-onboarding (cannon-rev stamped)

tide status
# STREAM
#   01-ship-onboarding  [active]  <one line — what this arc closes>
```

**Arcs are addressed by their slug** (`ship-onboarding`), not the number.

---

## 5. Bind it to a contract (≈ 1m)

A **contract** binds the arc to a goal + acceptance criteria. Signing flips it to
`running`. In `strict` mode a human signs; in `loose` it's synchronous.

```bash
tide contract new ship-onboarding
# tide: drafted contract .../contract.md (state: draft)

tide contract sign ship-onboarding
# tide: signed → running (sign: human @ ...)

tide contract list
# ship-onboarding  running  → 01-ship-onboarding
```

Now do the work: produce output in the arc's `output/`, and (as the worker)
propose the durable change in the arc's `delta.md`.

```bash
echo "new onboarding flow shipped" > .tide/arcs/01-ship-onboarding/output/result.md
printf '## ship-onboarding\nNew onboarding flow shipped.\n' \
  > .tide/arcs/01-ship-onboarding/delta.md
```

---

## 6. Close it — fold truth into the cannon (≈ 1m)

Report what was done, prove the criteria, accept, and close. **Close is the one
serialization point**: it guards the arc, then merges the delta into `CANON.md`.

```bash
tide contract report ship-onboarding    # what was done
tide contract proof  ship-onboarding     # evidence criteria are met
tide contract accept ship-onboarding     # report+proof accepted: no → yes

tide cannon rev                          # truth fingerprint before
# e5a89c1aa2de
tide contract close ship-onboarding
# tide: closed contract → cannon-rev 6af49ae695ef (state: close)
tide cannon rev                          # changed — the delta is now in CANON.md
# 6af49ae695ef
```

The board flags anything left dangling — an unmerged delta, or an open arc still
stamped at an older cannon-rev (drift):

```bash
tide status            # current project
tide status --all      # roster-wide
```

---

## What you just learned

| step | command | what it means |
|---|---|---|
| home | `tide init` | unfold the control-home you lead from |
| roster | `tide roster add` | point at projects (they live anywhere) |
| session | `tide` / `tide menu` | launch a seeded orchestrator |
| work | `tide arc new/open` | carve a bounded unit, addressed by slug |
| bind | `tide contract new/sign` | goal + criteria, sign to run |
| land | `tide contract report/proof/accept/close` | prove it, merge the delta into `CANON.md` |
| board | `tide status [--all]` | see unmerged deltas + drift |

Everything is plain markdown under `<project>/.tide/` — `cat`, `grep`, and `git`
it freely. Next: read **[README.md](README.md)** for the full command surface and
the on-disk invariants, or run `tide help`.
