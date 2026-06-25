# f2-upgrades — build notes

## What was added (on top of f1-core)
Layered an upgrades shop onto the existing single-file `index.html`, reusing the
`window.TidePool` store (`state.plankton`, `state.dots`, `state.ripples`) and the
existing `requestAnimationFrame` render loop. No new files, no deps.

### State (extended on the same `state` object)
- `state.perClick` (starts 1) — plankton gained per manual click.
- `state.auto = { count, cost }` — auto-spawner; `cost` starts 10.
- `state.click = { level, cost }` — click-multiplier; `cost` starts 25.

### Tuning constants
- `AUTO_BASE=10`, `AUTO_SCALE=1.15`, `AUTO_RATE=1` (plankton/sec per spawner).
- `CLICK_BASE=25`, `CLICK_SCALE=1.15`, `CLICK_STEP=1` (+per-click per level).
- `TICK_MS=1000`, `PASSIVE_DOT_CAP=12` (cap dots spawned per passive tick).
- `scaledCost(base, scale, n) = floor(base * scale^n)` — per-purchase scaling.

### UI
A `#shop` panel below the pool lists two upgrades. Each row shows live count/level,
current rate/per-click, and a **Buy · <cost>** button. `updateShop()` writes
textContent and toggles `button.disabled` when `plankton < cost`.

### Behaviour
- `buyAuto()` / `buyClick()` guard on affordability, deduct cost, bump count/level,
  recompute the scaled cost, then refresh HUD + shop.
- `passiveTick()` (via `setInterval(1000)`): gains `auto.count * AUTO_RATE` plankton
  with no clicking, spawns up to `PASSIVE_DOT_CAP` visible dots at random pool
  positions, repaints the counter, and re-evaluates button affordability.
- Manual click now adds `state.perClick` (was hard +1) so the multiplier matters.

## Verification
- `node --check` on the extracted inline script → `SYNTAX_OK`.
- DOM-stub sim (`sim_runB_f2.js`): stubs document/canvas/setInterval, loads the
  script via `vm`, then asserts:
  - store + initial economy values exposed;
  - buy blocked when broke; buy deducts exactly (100→90 auto, 90→65 click);
  - costs scale 10→11 and 25→28 (~1.15x);
  - 3 passive ticks raise counter +3 with zero clicks and spawn dots (0→3);
  - manual click adds perClick=2 and spawns 1 dot.
  - Result: **ALL SIM ASSERTIONS PASSED**, no exceptions/console errors.

## Notes / follow-ups
- Passive gain is integer here (AUTO_RATE=1); `renderHud()` floors so fractional
  rates are safe for a future arc.
- No persistence (localStorage) yet — captured as a candidate.
