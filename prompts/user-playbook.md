# tide · user playbook (the human algorithm)

tide is led from a **control-home** (the dir where you ran `tide init`). You don't learn the
commands — the orchestrator session runs them. This is the loop you drive.

## The loop
1. **`tide`** — from the control-home, see the roster and pick the project(s) to lead this
   session (`tide menu` → `pick> 1,3` or `all`). A fresh **orchestrator** session opens for each,
   seeded with its canon + active arc + the roster.
2. **Pick N** — choose what you're working on. The orchestrator selects or creates the arc.
3. **Steer** — tell the orchestrator the goal. It drafts a contract (goal + criteria) and
   dispatches a worker:
   - **strict** project → it asks you to **sign** first (`👍`/confirm in the live session),
   - **loose** project → it dispatches now, you review after.
4. **Arc done** — the worker returns output; the orchestrator **merges the canon delta** and
   shows you what changed. It then offers:
   - **take another arc** — keep going in this session,
   - **promote a candidate** — turn a surfaced idea into the next arc,
   - **close** — stop, or `/tide-handoff` to carry the thread into a fresh session.
5. **Repeat** — pick the next arc, or close.

## Things to know
- **Candidates** are the parking lot: ideas that surfaced but aren't this arc. Say "park that" and
  the orchestrator drops a candidate; promote it later when it earns an arc.
- **The canon** (`CANON.md`) is the project's living truth. Every finished arc updates it through
  **one merge, in front of you** — that merge is also where two arcs that disagree get reconciled.
- **Strictness** is your dial per project: `strict` = you sign before work runs, `loose` = work
  runs, you review after. Ask the orchestrator to flip it any time.
- **Handoff** (`/tide-handoff`) when a chat gets heavy: it distils the thread into the arc and opens
  a clean session already working — continue this arc, start fresh on a candidate, or just close.

There is **no autonomy** — nothing runs without this session. tide is synchronous: you, the
orchestrator, and the workers it dispatches, one merge gate holding the truth together.
