# CANON.md — tide-pool

## What it is

## State & components

## Interfaces / how used

## Canon journal

### 2026-06-25 · f1-core

Tide Pool is a single self-contained `index.html` (HTML+CSS+JS inline, no build step,
opens directly in a browser). It is a cozy idle/clicker rendered on a full-viewport
`<canvas>`.

### What it is
A calm tide-pool scene — teal gradient water with slowly drifting caustic light and a
soft vignette — that the player clicks to wake plankton. A large HUD counter (top-left,
label `PLANKTON`) shows the total ever spawned.

### State & components
- `count` — integer plankton total (in-memory only; **no persistence yet**).
- `plankton[]` — live drifting particles; capped at `MAX_PLANKTON = 240` for perf
  (oldest retired). The counter keeps climbing past the cap.
- `ripples[]` — transient expanding click rings.
- One `requestAnimationFrame` render loop draws water → ripples → plankton.

### Core loop (the durable truth of this arc)
Clicking anywhere in the pool (`pointerdown`) at point (x, y):
1. emits an expanding **ripple ring** at the click,
2. spawns a **plankton** particle (small cluster) that pops in (scale 0→1) and then
   gently drifts/bobs, tethered near its birth point,
3. increments the visible **plankton counter by exactly +1** (with a scale-bump).

### Feel / feedback model
Clicking is juicy: ripple expand-and-fade + plankton pop-in + ongoing drift/bob + soft
glowing halos. Warm, calm, tactile.

### Interfaces / how used
Open `index.html` in any browser. Click the pool to spawn plankton and grow the count.
No controls, no build, no storage. (Upgrades and persistence are future arcs.)

### Verified
Headless Chrome (CDP): 3 clicks → counter delta exactly 3; canvas rendered; 8 frames
ran clean; zero console errors; screenshot shows pool + ripples + glowing plankton.

### 2026-06-25 · f2-upgrades

Tide Pool is now an **idle/clicker with a spendable economy and an upgrades shop**,
still one self-contained `index.html` (HTML+CSS+JS inline, no build, opens in a browser).

### What changed since f1-core
The plankton counter is no longer a raw lifetime total — it is now a **spendable
balance** you grow by clicking and spend in a shop. (A separate in-memory `lifetime`
tracks total ever earned; still no persistence.)

### State & components (additions)
- `balance` — spendable plankton currency; the big HUD number shows this.
- `lifetime` — total plankton ever earned (flavor, never spent).
- `upgrades[]` — buyable items, each `{ id, name, owned, baseCost, effect }`.
- Pricing is geometric: `cost = floor(baseCost * 1.15^owned)`.
- `incomeAcc` — fractional passive-income accumulator drained inside the rAF loop.
- `window.__tidePool` — read-only verification hook (`state()`, `buy()`); no gameplay effect.

### The shop (durable truth)
A glassy teal **TIDE SHOP** panel (top-right; bottom dock on narrow screens) in the
editorial-mono dark house style. It shows the live balance and lists buyable items;
each item shows **name · level (Lv N) · effect text · current cost**. Affordable items
are active and hover-lit; unaffordable items grey out and disable. Costs update live
after every purchase.

### Two upgrades
- **Plankton Bloom** (auto-spawner) — base 15; each level adds **+1 plankton/sec** of
  passive income.
- **Tide Surge** (click-multiplier) — base 10; each level adds **+1 plankton per click**.

### Loop changes
- **Clicking** now grants `1 + clickLevel` plankton (not a flat +1), plus the f1 ripple
  + plankton pop-in feedback.
- **Auto-spawn tick**: owned spawners accrue plankton over time (frame-`dt` driven inside
  the rAF loop, clamped for tab-away gaps). Each whole plankton credits the balance **and**
  drifts a new plankton in from a pool edge, so idle income is visible on-canvas. Runs at
  zero clicks. A `+N/sec` rate readout appears under the HUD once passive income exists.

### Verified
Headless Chrome over CDP: bought each upgrade type and observed cost scale (Tide Surge
10→11, Plankton Bloom 15→17), owned increment, balance deduction, click gain 1→2 after a
multiplier buy, passive balance 0→2 over 2.6s at +1/sec with zero clicks, and unaffordable
items disabled. Zero console errors. Screenshot: `output/shop.png`.

### 2026-06-25 · f3-persist

Tide Pool now **remembers itself**: the game state survives a reload and rewards time
away. Still one self-contained `index.html` (HTML+CSS+JS inline, no build, opens in a
browser).

### What changed since f2-upgrades
The economy is no longer in-memory only. Spendable `balance`, `lifetime`, and every
upgrade level (Plankton Bloom, Tide Surge) are saved to `localStorage` and restored on
load, so no progress is lost across reloads. Idle time now pays out.

### State & components (additions)
- **Save key** — a single namespaced `localStorage` key `tidePool.save.v1` holding
  `{ v, balance, lifetime, upgrades:{spawner,click}, lastSaved }`. `v` is a schema
  version for forward migration; `lastSaved` is a `Date.now()` timestamp written on
  every save.
- **`saveGame()` / `loadGame()`** — save wraps storage writes in try/catch (never breaks
  on full / private-mode storage); load parses defensively (missing keys → defaults,
  corrupt JSON → fresh start, never throws; values sanitized and clamped, invariant
  `lifetime >= balance`).
- **Debounced auto-save** — `scheduleSave()` (800ms debounce) fires on every click,
  every purchase, and every passive auto-tick credit; plus a hard flush on
  `window 'beforeunload'`.
- **Offline cap** — `OFFLINE_CAP_SEC = 8h` caps idle credit.
- **Reset button** + **away toast** — new UI (see below).
- `window.__tidePool` verification hook extended with `saveGame`, `loadGame`,
  `resetGame`, `saveKey`, and a `setLastSaved` test helper.

### Persistence + offline (the durable truth of this arc)
- **On load**: restore balance, lifetime, and upgrade levels; then compute
  `elapsedSeconds = (now − lastSaved)/1000` (clamped `>=0`, capped at 8h) and grant
  `offlinePlankton = floor(elapsedSeconds × autoSpawnRate)`, where the rate derives from
  the restored Plankton-Bloom level. The grant is added to balance and lifetime.
- **While-you-were-away notice**: only when the offline grant is `> 0`, a cozy dismissible
  teal toast (editorial-mono house style) reads "while you were away (⟨elapsed⟩) —
  +N plankton drifted into the pool", with a × close button.
- **Auto-save**: every change/tick (debounced) and on tab unload, each save stamping a
  fresh timestamp — so the next load's offline math is accurate.

### Reset (durable truth)
A visible **Reset progress** button sits in a new shop footer, warm-red so it reads as
destructive vs the teal buy buttons. Clicking asks `window.confirm("Reset all progress?
This cannot be undone.")`. **Cancel** is a strict no-op. **Confirm** removes the save key,
zeros balance / lifetime / all upgrade levels, clears the live particles, hides the away
toast, re-renders the HUD + shop + rate, and writes a clean fresh save so a subsequent
reload stays fresh.

### Interfaces / how used
Open `index.html`, play, close the tab — your plankton and upgrades are still there next
time, plus whatever the auto-spawner earned while you were gone. Reset progress wipes it
back to a fresh pool after a confirm.

### Verified
Headless Chrome (Playwright, served on localhost): restore-on-reload exact (balance 500 /
lifetime 800 / spawner Lv3 / click Lv2 → identical after reload); offline grant
floor(~53s × 4/sec) = +213 with the away toast shown; reset-cancel = no-op, reset-confirm =
all zeros + fresh save; corrupt JSON / partial save / absent key all degrade to a clean
fresh start with zero thrown errors. Zero console errors.
