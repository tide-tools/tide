# Tide Pool — built end-to-end by `tide`

> A cozy single-file browser idle/clicker, built feature-by-feature through the
> **tide** orchestration machine. This doc is the showcase: *this is how tide works.*

The whole game is one self-contained file — [`index.html`](./index.html) — HTML, CSS
and JS inline, no build step, opens straight in a browser. Nothing about the game is
remarkable on its own. What's remarkable is *how it got there*: every feature was
driven through tide's file-based loop, and tide's rules — not discipline — kept the
build honest.

---

## The machine in one breath

tide keeps the **durable truth about the artifact** in one file, `cannon/CANON.md`.
Everything else is scaffolding around protecting that file:

- **arc** — one unit of work = one feature. Has its own `input/`, `workspace/`,
  `output/`, plus a `contract.md`, `delta.md`, `proof.md`, `report.md`.
- **contract** — the signed agreement for an arc: a `goal`, hard `criteria`, and a
  `cannon-rev` stamp pinning the exact cannon the work was started against.
- **worker** — writes the game code and the arc artifacts; proposes a `delta.md`
  (the change this arc makes to the durable truth). A worker **cannot** merge cannon
  or close contracts.
- **orchestrator** — opens/closes arcs, signs contracts, and is the *only* role that
  merges a delta into `CANON.md`.
- **candidates** — stray ideas, parked without polluting the truth.

The single load-bearing rule: **you cannot open the next arc while the last closed
arc's delta is still unmerged.** Truth gets reconciled before new work begins, every
time. It is a feature, not a friction.

---

## The loop, run three times

```
arc new ──▶ contract new + sign ──▶ WORKER builds + writes delta ──▶ proof
   ▲                                                                    │
   │                                                                    ▼
   └──────────  cannon merge (orchestrator)  ◀──────  contract close  ◀─┘
                         ↑ the gate ↑
```

Each pass closed one feature and folded its truth into the cannon before the next
arc could exist.

### Arc 01 · `f1-core` — the click that started it
**Contract goal:** clicking the pool spawns a plankton and bumps a visible counter,
with juicy feedback. Six hard criteria, down to *"no console errors on load or click."*
The worker built the canvas tide-pool — teal gradient water, drifting caustics,
ripple rings, pop-in plankton — proved it in headless Chrome (3 clicks → counter
delta exactly 3, 8 clean frames, zero errors), and proposed a delta. The orchestrator
merged it. `CANON.md` now *was* a clicker. Cannon revision: `ca0a6d0a…`.

### Arc 02 · `f2-upgrades` — raw counter becomes an economy
**Contract goal:** turn plankton into a spendable balance with a shop — an
auto-spawner (passive plankton/sec) and a click-multiplier (more per click),
geometric costs, and a passive tick that earns at zero clicks. The worker reshaped
the HUD number from a lifetime total into a spendable `balance`, added the glassy
**Tide Shop** panel, geometric pricing (`cost = floor(base · 1.15^owned)`), and an
`autoTick` that drips income inside the render loop. Proven over CDP (cost scaled
10→11 and 15→17, click gain 1→2 after a buy, passive 0→2 over 2.6s with zero clicks).
Merged. Cannon revision moved to `f3e6a799…`.

### Arc 03 · `f3-persist` — the game remembers itself
**Contract goal:** persist balance + upgrades to localStorage, restore on load, grant
offline progress, and a confirm-gated reset. The worker added a single namespaced,
versioned save key (`tidePool.save.v1`), a debounced auto-save + `beforeunload`
flush, defensive load (corrupt JSON → clean fresh start, never throws), an 8h-capped
offline grant with a cozy "while you were away" toast, and a warm-red **Reset
progress** button behind `window.confirm`. Proven with Playwright: exact
restore-on-reload, `floor(~53s × 4/sec) = +213` offline, reset-cancel a strict no-op.
Merged. Cannon revision `14c2d5f9…`.

---

## The drift-block moments — where the machine earned its keep

Twice during this build the orchestrator was poised to open the next arc *before*
the previous delta had been merged. tide refused both times. You cannot start arc 02
while `f1-core`'s `delta.md` reads `merged: no`; you cannot start arc 03 while
`f2-upgrades`'s delta is dangling.

This is visible in the artifacts as the **`cannon-rev` chain**: every contract is
signed against the exact cannon hash that was current when it began —
`ca0a6d0a…` → `f3e6a799…` → `14c2d5f9…`. Each new arc started against the cannon the
*previous* merge produced. The revisions only advance because each delta was merged
in order. There is no way to fork the truth, ship a feature whose contract was never
reconciled, or let `CANON.md` and `index.html` quietly drift apart. The gate makes
that class of mistake unrepresentable.

---

## The final artifact

`CANON.md` is the single source of truth, and its **Cannon journal** narrates the
build in order — f1-core, then f2-upgrades, then f3-persist — each entry describing
what the game *is* after that arc, with its own `### Verified` block. Read top to
bottom, the journal *is* the changelog and the spec at once.

The game itself:

- **Core** — click the pool → ripple + pop-in plankton + counter bump.
- **Shop** — Plankton Bloom (auto-spawner) and Tide Surge (click-multiplier),
  geometric costs, passive income that runs at zero clicks.
- **Persistence** — saved across reloads, offline progress with a welcome-back toast,
  confirm-gated reset.

### Verification at showcase time
- `node --check` on the extracted `<script>` → **clean**.
- Live browser smoke test (Chromium, served on localhost) drove the verification
  hook: 20 clicks `+1` each, buy Tide Surge (cost 10, click-gain 1→2), buy Plankton
  Bloom (cost 15, rate 0→1/sec), save → reload-with-backdated-timestamp → `+100`
  offline credited exactly. All passed.
- `tide status` → exactly **3 arcs, all `[done]`** (`__..__`), **zero** unmerged
  deltas, **zero** drift. Every `delta.md` reads `merged: yes`.
- The only console message in the browser is a `favicon.ico` 404 from the dev static
  server — environmental, not from the game (the single-file game ships no favicon).

---

## Why this is a clean showcase of tide

Three real features, three arcs, three signed-and-closed contracts, three merged
deltas — and a cannon journal that reads as a coherent story of what the artifact
became. At no point could the build run ahead of its own recorded truth, because the
merge-before-next-arc gate physically prevented it. That's the pitch: **tide doesn't
ask you to keep the docs in sync with the code — it refuses to let them diverge.**
