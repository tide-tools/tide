# Session operations — offload / handoff / spark

> Naming: the dump op is **offload** (not "флот"); the new-work-line op is **spark**
> (was "branch" — an idea sparks a new thread).

The three human-triggered operations on a **session** (one run inside a thread).
Outside these, the agent leaves the stream alone (minimal mode). Captured from the
design conversation; OPEN questions marked ⛏.

## Model recap
- **thread (треда)** — container, the arc through which arcs are managed.
- **session** — one run inside a thread; numbered; chained by `from:`.
- Each session passport carries `## cursor` (resume point) + `## context` (memory).

## offload
- Dump the session's **current** work into its arc: append to `## context`, refresh `## cursor`.
- **Incremental** — only what's new since the last offload. Nothing new → writes nothing and
  says so.
- It's the intermediate step that **handoff and spark both run first**.
- **Marker — DECIDED: deterministic (B).** The session passport carries `offloaded-at: <N>` where
  `N` is the session's **transcript size** (message/line count of the live Claude session) at the
  last offload. On offload: read current size, distill the slice `[offloaded-at .. now]`, append it
  to `## context`, set `offloaded-at = now`. If `now == offloaded-at` → nothing new, say so.
  - Split of labor: the **skill** measures the transcript size (Claude Code session internals) and
    distills the slice; the **CLI** stores/reads the marker + appends text (deterministic, testable).

## handoff = continue the SAME thread in a fresh session
- Use when you want to keep going on this work-line but in a clean session.
- offload first, then create a NEW **session** in the **same thread** with `from: <this session>`.
- Opens a fresh **Orca terminal** (the existing `/handoff` skill already does this).
- Writes the session `title:` + `## summary`: **what was done · what's left undone · where it's
  heading** (longer if the session is large). The new session is seeded with that.

## spark = start a NEW thread from an idea that surfaced here
- Use when a tangential idea pops up that you do NOT want to continue in this work-line — spin it
  into its **own new thread** (a new нить), quickly, and jump there.
- offload first, then create a NEW **thread** (+ its first session) recording where it came from.
- Name: **spark** (an idea sparks a new thread; light-through-a-thread theme).

## picker sub-choice (on continuing an existing session)
- When you pick an existing session in `tide menu`, it asks:
  **continue in the same context** OR **handoff into a new session**.
- ⛏ "same context" = literal `claude --resume <id>` of that conversation, OR re-seed a fresh
  session from the session's arc/cursor? (Different mechanisms — needs a decision.)

## Session title + summary (for reading sessions later)
- Each session has an **index** (its NN number), a **title:** (human, one line), and a
  **## summary** — a few plain sentences: what got done, what's unfinished, where it's heading.
  Written on **handoff** (and offload); longer if the session is large.
- The picker shows the title so you can tell sessions apart. (Foundation shipped: `title:`,
  `## summary`, `offloaded-at:` in the session template; picker lists the title.)

## Interactive TUI picker (UX — requested)
- The picker must be **arrow-key navigable** (move ↑/↓ between options, Enter to choose) and
  nicely formatted — not "type a number". Applies to project → thread → session steps.
- Stdlib-only constraint → `curses` (no deps). Must **degrade gracefully**: when stdin/stdout is
  not a TTY (pipes, `--pick`, tests) fall back to the current numbered/`0=new` text path.

## ⛏ Open question — picker "same context vs handoff"
When continuing an existing session the picker asks: **same context** or **handoff to new**.
"Same context" = literal `claude --resume <id>` of that conversation, OR re-seed a fresh session
from the arc/cursor? Different mechanisms — needs a decision before the sub-choice is built.

## Build sketch (once OPEN questions close)
- CLI primitive: `tide session offload <thread> <session> --at <N> [text]` — appends text under
  `## context` + stores `offloaded-at: N` (marker B). The skill measures `N` (transcript size) and
  distills the slice. `tide arc new-session --from <ref>` already sets `from:` (shipped).
- Skills: `/offload`, `/spark` (and wire `/handoff` to write the thread session + set `from:`).
- Minimal: no contracts, no canon, no auto-actions.
