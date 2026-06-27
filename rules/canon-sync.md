# rule · canon-sync (the serialization point)

`CANON.md` is the project's living truth **and** its only consistency boundary. Every finished arc
folds back into it through a single merge — and that merge is the only place divergent work is
reconciled. This rule fixes the discipline that forces every change through that one gate.

## canon-rev
- `canon-rev` is a short content hash of **`CANON.md` only** (not the whole `canon/` dir — so a
  note/changelog tweak never spams drift).
- It bumps on **every merge**.
- When an arc opens it **stamps** the current `canon-rev` into its `arc.md` frontmatter.

## The flow
1. **Open** → stamp `canon-rev`.
2. **Work** → the worker produces output + a **proposed** canon-delta (`output/delta.md`).
3. **Merge (orchestrator-only)** → `tide canon merge <arc>` folds the delta into `CANON.md`,
   appends a line to the **Canon journal**, and **bumps `canon-rev`**. Workers never merge.
4. **Before the next arc** → the orchestrator merges any owed delta first.

## Drift + the block (forced reconciliation)
- **Drift:** on dispatch/close, an open arc's stamped `canon-rev` is compared to the current one.
  If canon moved underneath it → **flag drift**, force the worker to re-read `CANON.md` and
  re-stamp (`tide arc resume <arc>`) before its output is trusted.
- **Block:** opening a **new** arc is **refused** while a **closed** arc's canon-delta is still
  unmerged. This is what pushes deltas through the gate **one at a time**.

## Why it's the conflict-resolution mechanism
Workers run parallel in isolated arcs, so two arcs can contradict invisibly. The contradiction can
only become visible where writes converge — the merge. Because merge is orchestrator-only +
single-threaded, conflicts surface there **one delta at a time, in the live session, in front of the
human** (or resolved by the orchestrator per strictness). The drift-stamp + the block force every
delta through this single point. ⇒ canon is simultaneously the single source of truth and the only
place consistency is enforced. There is no separate merge machinery — **the gate is the mechanism.**
