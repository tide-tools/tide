# rule · subagents (dispatch discipline)

The orchestrator never does project work directly. Real work runs in a **worker subagent** scoped
to **one arc**. This rule fixes how dispatch works so isolation — the property that makes the canon
merge the only consistency boundary — actually holds.

## Roles are deterministic, set by env
- The launcher/seed starts orchestrator sessions with `TIDE_ROLE=orchestrator`.
- A dispatched worker subagent runs with `TIDE_ROLE=worker`.
- The CLI **hard-refuses** orchestrator-only ops (`tide canon merge`, `tide candidate promote`)
  unless `TIDE_ROLE=orchestrator`. Least privilege: an unset role defaults to **worker**.

## One worker = one arc
- A worker **always has an arc**. If none is chosen, the orchestrator creates one before dispatch —
  a worker is never loosed on a project at large.
- The worker writes **only** its own arc's `workspace/` (scratch) and `output/` (durable finish).
  It reads its `input/`. It touches **no other arc**, and it reads other arcs only via their
  `output/` (never their `workspace/`).
- The worker **proposes** a canon-delta (`output/delta.md`); it never merges. It **surfaces**
  candidates (`tide candidate add`); it never promotes.

## Parallel workers
Subagents may run concurrently. Safe **because** each writes only its own isolated arc — there is no
shared write surface. Two arcs can diverge or contradict and it stays invisible while they run. The
contradiction surfaces later, at the **canon merge**, which is orchestrator-only and
single-threaded — so no file-lock is needed; **serialization is the merge gate** (see
`canon-sync.md`).

## Dispatch checklist (orchestrator)
1. Arc selected/created and its contract drafted (goal + criteria; see `contract.md`).
2. Strictness gate satisfied (strict ⇒ human signed in the live session).
3. Worker handed `worker.md` + the arc passport (`arc.md` / goal doc).
4. On return: accept output against criteria, **merge the delta before the next arc**, triage
   surfaced candidates.
