# MOORLINE — canon at the center (proposal)

> Synthesized output of the canon-centrality-model workflow (2026-06-25).
> Status: **PROPOSAL** — on the human gate, not yet accepted.
> Distilled candidate: `22-moorline-canon-centrality-model.md`

---

## Canon at the center — the model

**MOORLINE** — canon is the one thing every unit of work is born from, stuck on until it lands, and serialized through.

### 1. Core principle

Canon is structurally central when **the definition of work is the only door work can pass through — at birth, at every transition, and at the exit — and that door is a single, two-axis, fail-loud oracle that no role, no interactivity, and no automation can route around.** Today's gates ask "does CANON.md hash agree with its own stamps?" That is the wrong axis: the neglect we are preventing is *reality outrunning canon*, plus *canon being write-only and never consulted*. So the keystone is one predicate, `tide cannon gate`, that fuses **structural health** (canon is not rotted), **two-axis freshness** (canon has absorbed the code that shipped — not just that its own sha is consistent), **substance** (the delta encodes what the work taught, not a stub), and **consultation** (the arc was opened *against* canon). Every work transition — arc open, arc close, session start, Orca cron, PR merge — calls this one oracle; mechanism, not willpower, beats discipline because there is no green path that leaves canon behind.

### 2. The mechanisms

**M1 — `tide cannon gate`: the single two-axis oracle (graft: enforcement-gate keystone, hardened).**
*What:* one pure predicate, `gate.decide(root) -> (code, reasons)`, that is THE definition of "canon is current." It is **tri-state**: `0 = current`, `1 = stale`, `2 = oracle-error` (binary/PATH/project unresolved/canon unreadable). It composes: (a) no unmerged deltas; (b) no open arc drifted on **cannon-rev** (sha of CANON.md) *or* on **reality-rev** (git-tree sha of the paths CANON.md claims to cover); (c) `cannon lint` passes (structural health); (d) no arc dirty-without-substantive-delta; (e) no open arc with an empty `canon basis`; (f) no unpaid `canon-debt` (open force/noop records).
*Enforced:* a real subcommand backed by `src/tide/gate.py`, POSIX exit code so it composes verbatim in shells, hooks, CI, and Orca `--precheck`. Every other gate delegates here — one definition of "current," nothing can drift from it. **Tri-state is load-bearing:** callers must treat `2` as FAIL-LOUD (notify + mark degraded), never as skip, so a broken oracle cannot silently disable enforcement. A canary cron stales a scratch project and asserts the gate trips.
*Prevents:* the root neglect — nothing forced attention back to canon. Now no progress path is green while canon is stale, and a silently-dead gate is itself detected.

**M2 — Two-axis freshness: `reality-rev` (resolves the fatal blind spot in all four lenses).**
*What:* every project's CANON.md carries a `canon-covers:` manifest (path globs it claims to describe). `reality-rev = git-tree sha over those globs`. `tide arc new` stamps BOTH cannon-rev and reality-rev; close re-stamps. The gate reports STALE when **reality-rev moved but the merged delta touched no canonical section** (`## What it is` / `## State & components` / `## Interfaces`) — i.e. "code shipped, canon didn't."
*Enforced:* new `cannon/reality.py`; clause (b) of M1. A configurable commit/time threshold turns "canon lags reality" from an unobservable state into a tripwire.
*Prevents:* the exact original failure — "forgot to update canon after changes." Bumping the rev with a trivial delta no longer clears the gate while the top sections rot.

**M3 — DEFINE as a first-class lifecycle state with a substance + consultation guard (graft: ritual-loop + enforcement-gate's canon-as-INPUT fix).**
*What:* insert **DEFINE** into the contract state machine (`new → sign → running → output → DEFINE → accept → close`); no `output→close` edge exists. DEFINE's exit criterion is a delta that **routes content into ≥1 canonical section** (journal-only = worklog, not refinement) OR a **signed noop** (`--noop --reason`, which is itself journaled as auditable `canon-debt` that re-stales the gate until reconciled). Symmetrically, `tide arc new` pre-fills a **`canon basis`** block quoting the relevant CANON.md sections; an open arc with empty basis is STALE.
*Enforced:* transition table in `contract/lifecycle.py`; substance check = "delta adds content not already a substring of a canonical section." The `-f` / force escape is **not keyed on interactivity** (that exempts the exact human-present context where we failed) — any override writes a gate-blocking `canon-debt` entry. So overrides convert neglect into visible, payable debt, never a clean exit.
*Prevents:* delta-shaped tokens that clear a presence-check; canon being write-only and never consulted; "trusted interactive human types `close -f`."

**M4 — Atomic, serialized, idempotent, trunk-only merge (graft: ritual-loop "land" + orca-native lint + fixes the real duplicate-journal bug).**
*What:* `tide cannon merge` is the single serialization point and is now **safe under Orca parallelism**: (i) **idempotent** — read the delta's `merged:` flag first, no-op if already merged (this is the actual cause-fix for the duplicate-journal corruption, not M-lint papering the symptom); (ii) **locked + atomic** — `flock` on CANON.md for the read-modify-write, write-temp + `os.replace`, **compare-and-swap** on cannon-rev (re-read base under lock, abort if base moved since dry-run); (iii) **self-linting** — build merged text, run `cannon lint`, swap only on pass (else abort, old file untouched); (iv) **trunk-only** — refuses to run inside an Orca worktree. Workers only ever write `delta.md`; the orchestrator merges once, on the integration branch.
*Enforced:* `cannon/merge.py` guard at top; OS file lock; content-hash per journal entry (stamped entry-id) so reworded near-dups and double-runs both reject. CI fails any PR that modifies CANON.md from a worker branch (canon arrives only via the orchestrator's merge commit).
*Prevents:* the literal corruption observed (empty top sections + duplicate journal entries), lost-update races between concurrent automations, and the silent multi-worktree defeat of single-serialization.

**M5 — Write-time + read-time guardians: canon-pulse banner, canon-lint, PostToolUse hook (graft: visibility-recall).**
*What:* (a) **canon-pulse** — `session_start.render()` reordered so the canon block (rev, `updated:`, lint verdict, lag count, `## Где мы сейчас` pointer) is the FIRST text of every session, healthy or not. (b) **canon-lint** — pure structural invariant (canonical sections non-empty for any maintained project, unique top headings, dedup'd append-only journal, no template placeholders); a **substance floor** distinguishes "seeded-empty new project" from "hollow maintained canon" so the gate is not toothless in the early high-velocity phase. (c) **PostToolUse hook** on Edit/Write whose `file_path` resolves to CANON.md → runs lint and blocks (exit 2) on the bad write — closing the `.tide/`-always-allow hole for the single most load-bearing file.
*Enforced:* hook wired by `tide install-hooks`; the same mechanism that beat willpower for the orchestrator edit-ban, applied to canon's own file. A pre-commit hook additionally **forbids hand-edits**: any CANON.md diff not produced by `tide cannon merge` (verified against a stamped last-merge signature in `.tide/state`) is rejected — canon becomes append-only-via-merge.
*Prevents:* canon out of sight at entry; a corrupting hand-edit sitting broken until the next transition; "quickly fix the empty sections by hand" with zero provenance.

**M6 — Meta/factory canon is arc-shaped too (resolves the scope-mismatch that made this session's neglect unreachable).**
*What:* the neglect happened in **control-home / factory canon** (Orca integration, hooks, PR flow) — meta-work that no project arc touches. So **the tide repo and control-home are first-class projects** with their own CANON.md, arcs, contracts, and revs. A repo-level pre-push / CI gate `tide cannon gate --home` runs on ANY change to tide's own code or control-home and blocks the merge unless the home canon's delta is reconciled. **No "it's just config/infra" escape hatch** — that hatch is precisely what failed.
*Prevents:* the exact surface that broke being structurally outside every gate.

### 3. How it rides Orca-native execution

One arc ⇄ one worktree ⇄ one GitHub issue (the un-abandonable commitment) ⇄ one PR ⇄ one contract ⇄ one cannon-delta, all keyed by **both** cannon-rev and reality-rev stamped at birth into arc frontmatter, the issue body, and a committed `.tide/state/arc-rev`.

- **Bind-at-birth:** `tide arc new --issue` is the only entrypoint; it calls the gate BEFORE any `gh`/worktree side-effect, so an owed delta means no issue and no worktree exist.
- **Don't trust the entrypoint — enforce at exit:** Orca's native "new worktree" button cannot be removed, so `tide cannon gate` run as the **required PR status check** independently re-derives the arc↔issue↔rev binding from the PR branch and **fails any PR whose worktree was not born through `tide arc new`** (un-stamped work can commit but never reach a terminal state). The PostToolUse hook also refuses writes inside a worktree lacking a valid `arc-rev`.
- **Automation precheck = the same oracle:** every tide-managed Orca automation is created with `--precheck 'tide cannon gate --project <p>'`, non-optionally; a cron firing on stale canon is skipped. **Precheck escalates, never silently stalls:** on canon-debt it opens/raises a tracked `canon-debt` issue, pushes a notification, and after N aborted cycles auto-spawns a dedicated reconcile-canon arc.
- **Terminal-state binding:** the only path that closes the issue / merges the PR / sets `status: completed` is `tide arc close`, which runs the trunk-only `cannon merge` first and bumps both revs. Branch protection has **include-administrators ON** plus a server-side pre-receive ruleset, so even the solo owner's late-night force-merge runs the gate; a pre-receive hook rejects pushes that `Closes #N` while the arc still owes a delta. Enforcement is before the irreversible squash-merge, not a post-hoc reopen.

tide stays the METHOD (canon · arc · contract · two revs · DEFINE · drift). Orca stays the MECHANISM (worktree · agent launch · board · PR · cron). The two revs carried across both surfaces are the single binding that makes neglect technically impossible, not a remembered discipline.

### 4. How this would have prevented THIS session's neglect

The concrete moment: humans+agent got excited about Orca/worktree/issue/PR machinery, **left cannon-merge broken** (control-home CANON.md with empty top sections, duplicate journal entries), and **forgot to update canon after the changes**. Stop points, in order:

1. The Orca/hooks/PR work touched tide's own code and control-home — **M6** makes that arc-shaped, so the pre-push gate would have refused to merge the execution-machinery changes until the home canon's delta was reconciled. The work could not have "shipped past" canon.
2. Reality moved (lots of new code) while CANON.md's top sections stayed empty — **M2's reality-rev** trips STALE ("canon has not absorbed the last N commits"), which **M1** surfaces as non-zero on the very next arc-open and on the SessionStart banner (**M5 canon-pulse**, first text on screen).
3. The empty top sections / duplicate journal could not have been a resting state: **M4 idempotent + locked + self-linting merge** refuses to produce duplicate journal entries or swap in a file that fails lint, and **M5's PostToolUse + pre-commit guardians** would have rejected the corrupting write at the instant it happened.
4. Even under deadline pressure, closing with a stub or a `-f` would not clear the gate — **M3** demands a canonical-section delta or a signed noop that itself becomes gate-blocking `canon-debt`. There was no green path that left canon behind.

### 5. Build order (fastest path to canon-central)

1. **`cannon lint` + idempotent/locked/atomic `cannon merge` (M4 + M5b).** Cause-fix the confirmed duplicate-journal bug and make corruption non-representable. Smallest, highest-leverage, unblocks everything. Add the per-entry content-hash dedup and `flock`+`os.replace`+CAS.
2. **`tide cannon gate` tri-state oracle (M1) with clauses for lint + unmerged + drift.** One `gate.py`, POSIX exit. This is the keystone every later call site reuses.
3. **`reality-rev` two-axis freshness (M2).** Add `canon-covers:` manifest + reality-rev stamping; wire into the gate. This is what actually fixes the original neglect direction.
4. **DEFINE state + substance/consultation guard + non-interactive-proof force-debt (M3).** Make the loop's front and back halves structural.
5. **Hooks: canon-pulse banner reorder + PostToolUse CANON.md guardian + pre-commit hand-edit ban (M5a/M5c).** Cheap, high-visibility, closes the write-time hole.
6. **Orca wiring (§3) + M6 home-as-project gate.** Required PR check that re-derives the binding, automation precheck-with-escalation, terminal-state binding, include-administrators + pre-receive. Last because it depends on the oracle (step 2) existing and being trustworthy.

Relevant code surfaces: `src/tide/cannon/merge.py` (idempotency + lock), `src/tide/cannon/rev.py` (add reality-rev), `src/tide/sync.py` (drift two-axis), new `src/tide/gate.py`, `src/tide/contract/lifecycle.py` + `src/tide/arc/stream.py` (DEFINE state + non-forceable guard), `hooks/session_start.py` + `hooks/edit_gate.py` (canon-pulse + PostToolUse CANON.md guardian). Normalize the `cannon`/`canon` spelling and name the CI gate `tide cannon gate` (distinct from `tide verify` for artifacts) in the same pass.
