# Routines (рутины) — a reusable-procedure arc kind

A **routine** is a fourth arc kind (alongside arc / goal / thread): a **reusable
procedure** — work you did once in an arc and now want to re-run, with its own
accumulated internal experience. Example: **invite-codes** (a runbook you re-run
each batch).

## Model
- A routine is a `kind: routine` container (goal-shaped, like a thread): a
  procedure passport + a nested `arcs/` whose sub-arcs are its **runs**.
- The procedure passport holds the **steps** (the runbook) + a `## experience`
  section that accrues lessons across runs (so the routine gets smarter).
- A **run** is one execution of the routine (a session-like sub-arc). Runs are
  numbered and chained by `from:`, exactly like thread sessions.

## Picker flow
```
tide menu → project → TYPE: [ task | routine ]
   task    → thread → session         (the existing flow)
   routine → routine → run/continue  (new)
```
- After the project, the human picks a **type**: Task (regular work) or Routine
  (a reusable procedure). Back navigation applies (← /Esc) like everywhere.
- Routine path: list the project's routines (with a distinct **icon/marker** so
  they read differently from tasks), pick one (or `0` = new routine), then start a
  new run or continue one. The run's seed carries the procedure + experience.

## What to build (delegated to a sub-agent)
- `arc.stream`: `KIND_ROUTINE`, `new_routine()` (goal-shaped, `kind: routine`),
  `is_routine()`, `routine_entries()`; runs reuse `new_session()`/`session_entries()`
  (a run IS a session inside the routine). CLI: `tide arc new-routine`.
- `arc.templates`: `routine_md()` — `## steps` (the runbook) + `## experience`.
- `launcher.menu`: a Type step after the project (Task/Routine) inside
  `navigate_interactive`, preserving Back; a routine branch that lists routines
  (marked with an icon) and binds a run; the tab title / seed frame it as a routine
  run. Keep all non-interactive/flag paths working.
- Seed: frame a routine run (procedure + experience) the way a session is framed.
- Seed **invite-codes** as a routine in mitehq from `docs/invite-codes-runbook.md`.

Minimal mode still holds: no canon, no contracts, no self-initiated ceremony.
