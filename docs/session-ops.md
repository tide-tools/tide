# Session operations — offload / handoff / spark

> Naming: the dump op is **offload** (not "флот"); the new-work-line op is **spark**
> (was "branch" — an idea sparks a new thread).

The three human-triggered operations on a **session** (one run inside a thread).
Outside these, the agent leaves the stream alone (minimal mode). Captured from the
design conversation; OPEN questions marked ⛏.

## Model recap
- **thread (тред)** — a narrative work-line; a container whose sessions are chained
  by handoffs (real context transfer), not blank re-entries. The thread law: the
  first session is born with the thread; every later session comes from a handoff.
- **session** — one run inside a thread; numbered; chained by `from:` (its handoff
  parent). Sessions surface newest-first (the tip on top).
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
- **Two-stage / pull (no auto-terminal).** The handoff **hangs an offer** in the queue
  (`tide handoffs offer`); you pick it up yourself from `tide menu`, where the offered session
  shows as the thread's **⇄ tip**. Picking it up launches from the seed (so the distil is honoured,
  not a generic resume); the offer flips `offered → taken` on the picked-up session's first message.
  Changed your mind? `tide handoffs drop <id>` dismisses the offer (and prunes its untouched session).
- Writes the session `title:` + `## summary`: **what was done · what's left undone · where it's
  heading** (longer if the session is large). The new session is seeded with that.

## spark = start a NEW thread from an idea that surfaced here
- Use when a tangential idea pops up that you do NOT want to continue in this work-line — spin it
  into its **own new thread** (a new нить), quickly, and jump there.
- offload first, then create a NEW **thread** (+ its first session) recording where it came from.
- Name: **spark** (an idea sparks a new thread; light-through-a-thread theme).

## picker behaviour (RESOLVED by the thread law — shipped)
- Opening a thread lists its sessions **resume-only** (newest/tip first). There is **no blank
  "+ new session"** mid-thread — the next session is born from a handoff, not the picker. (An
  EMPTY thread auto-creates its first session — the one that begins the narrative.)
- "Resume" = literal `claude --resume <id>` of that conversation, with a fresh-seeded fallback
  (`|| <seeded>`) when the pinned id has no persisted conversation yet. This settles the old
  "same context vs re-seed" question: resume IS same-context; a fresh line is a handoff.
- A session carrying a **pending handoff** shows marked **⇄** and floats to the tip; picking it
  opens a **pick up / dismiss** sub-choice — pick up launches from the distil seed, dismiss calls
  `tide handoffs drop` (soft-archive + prune the untouched session).

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

## ✓ Resolved — picker "same context vs handoff"
Settled by the **thread law**: continuing an existing session = **same context** (literal
`claude --resume <id>`, fresh-seeded fallback). A new work-line is a **handoff** (a fresh
seeded session), never a blank picker entry. So there is no per-session "same vs new" prompt —
the picker is resume-only; only a **pending-handoff (⇄)** session offers a pick-up/dismiss choice.

## Build sketch (once OPEN questions close)
- CLI primitive: `tide session offload <thread> <session> --at <N> [text]` — appends text under
  `## context` + stores `offloaded-at: N` (marker B). The skill measures `N` (transcript size) and
  distills the slice. `tide arc new-session --from <ref>` already sets `from:` (shipped).
- Skills: `/offload`, `/spark` (and wire `/handoff` to write the thread session + set `from:`).
- Minimal: no contracts, no canon, no auto-actions.
