# f2-upgrades — worker notes

## What was built
Turned the raw f1 counter into a spendable economy with an upgrades shop, layered
on top of the existing single-file `index.html` (no build, still one self-contained
file, f1 click/spawn loop untouched).

### Economy
- `balance` — spendable currency the shop deducts from. The big HUD number now shows
  the **balance** (what you can spend), not a monotonic total.
- `lifetime` — total plankton ever earned (flavor, never spent).
- Clicking grants `clickGain()` plankton (base `+1`, plus the click-multiplier level).

### Shop UI (top-right panel, editorial-mono dark / teal)
Glassy teal panel titled **TIDE SHOP** with a live balance readout. Each item is a
button showing **name · level (Lv N) · effect text · current cost**. Affordable items
are active and hover-lit; unaffordable items grey out and are `disabled`. A small
`+N/sec` rate readout fades in under the HUD once passive income exists.

### Two upgrade types
- **Plankton Bloom** (`spawner`) — base cost 15; each level adds `+1 plankton/sec`
  passive income.
- **Tide Surge** (`click`) — base cost 10; each level adds `+1 plankton per click`.

### Cost scaling
Geometric: `cost = floor(base * 1.15^owned)`. Recomputed and re-rendered on every
purchase (e.g. click 10→11, spawner 15→17 after one buy).

### Auto-spawn tick
Driven inside the existing rAF loop via real frame `dt` (clamped to 0.25s to absorb
tab-away gaps). Owned spawners accrue fractional income into an accumulator; each whole
plankton credits the balance **and** drifts a fresh plankton in from a pool edge toward
the interior, so idle income reads on-canvas, not just in the number. Runs at zero clicks.

## Verification (automated, headless Chrome via CDP)
Script: launches `--headless=new` Chrome, drives the page over the DevTools Protocol
(Node 22 built-in WebSocket), exercises every criterion, screenshots, asserts zero
console errors. Results:

- click base gain = **+1** (before any multiplier).
- buy **Tide Surge**: cost **10 → 11**, owned **0 → 1**, balance deducted (11 → 1),
  clickGain **1 → 2**.
- click after buy grants **+2** (multiplier active).
- buy **Plankton Bloom**: cost **15 → 17**, owned **0 → 1**, perSec **0 → 1**,
  balance deducted (15 → 0).
- passive income at **zero clicks**: balance **0 → 2** over 2.6s at +1/sec (lifetime +2).
- unaffordable items report `disabled: true` (cost 17/11 > balance 2) — grey-out proven.
- **consoleErrors: []** — zero.
- screenshot: `output/shop.png` (panel + items + costs + HUD + on-canvas plankton).

A small `window.__tidePool` hook (`state()` / `buy()`) is exposed for automated checks
only; it has no effect on gameplay.

## Files touched
- `index.html` — economy + shop UI + auto-spawn tick (f1 loop preserved).
- `.tide/arcs/02-f2-upgrades/output/notes.md`, `output/shop.png`
- `.tide/arcs/02-f2-upgrades/delta.md`

## Stray ideas (not in scope — left as candidates if pursued)
- Persistence (localStorage) so balance/upgrades survive reload.
- A 3rd "offline earnings" upgrade or prestige reset.
