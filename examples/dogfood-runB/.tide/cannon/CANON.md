# CANON.md — tide-pool-B

## What it is
Tide Pool — a playable single-file browser idle/clicker base (`index.html`, no external deps). Click the tide pool to seed plankton.

## State & components
- `index.html` — self-contained HTML+CSS+JS. Canvas pool (480x320) + HUD plankton counter.
- Runtime state lives in one object `state = { plankton:Number, dots:[], ripples:[] }`, exposed as `window.TidePool`.
  - `dots`: spawned plankton `{x,y,r,hue,phase,drift,born}`, bounded to `MAX_DOTS=600` (FIFO).
  - `ripples`: transient click-feedback rings `{x,y,t}`, life 0.6s.
- Single `requestAnimationFrame` loop renders background → dots → ripples.

## Interfaces / how used
- Open `index.html` in any browser. Counter starts at 0.
- `pointerdown` on the canvas: counter += 1, spawns a plankton dot at the click point (jittered, clamped to pool), emits a ripple.
- Click coords mapped CSS-px → canvas space, so it works under CSS scaling.
- Downstream arcs build on `window.TidePool`: a spawn loop pushes to `state.dots` + bumps `state.plankton`; upgrades read/write the same store.

## Cannon journal

### 2026-06-25 · f1-core
Established the single-file game + central mutable state store + render loop. Click → plankton + counter + ripple feedback. Verified by `node --check` (SYNTAX_OK) and DOM-stub click simulation (counter/dots/ripples 0→3, first dot in-bounds, no exceptions).

### 2026-06-25 · f2-upgrades

Tide Pool now has an **upgrades economy** on the same `window.TidePool` store:
- `state.perClick` (start 1) — plankton per manual click.
- `state.auto = { count, cost }` — auto-spawner; passive plankton/sec ∝ count.
- `state.click = { level, cost }` — click-multiplier; +1 per-click per level.

Tuning: `AUTO_BASE=10`, `CLICK_BASE=25`, both scale `cost = floor(base * 1.15^n)`;
`AUTO_RATE=1` plankton/sec per spawner; passive tick every `1000ms`, capped at
`PASSIVE_DOT_CAP=12` dots/tick.

### Interfaces / how used
- A `#shop` panel under the pool lists ≥2 upgrades (Auto-spawner, Click power), each
  showing live count/level + current cost on a **Buy** button.
- Buying deducts `cost` from plankton, bumps the upgrade, and re-scales its cost;
  buttons auto-`disabled` when `plankton < cost`.
- `passiveTick()` (setInterval 1s) adds `auto.count * AUTO_RATE` plankton with **no
  clicking**, spawning visible dots into `state.dots` and bumping the HUD counter.
- Manual click now grants `state.perClick` (not a flat +1).
- Downstream arcs read/write `state.auto/state.click/state.perClick` the same way.

### Cannon journal
#### 2026-06-25 · f2-upgrades
Added the upgrades shop: auto-spawner (passive plankton/sec, spawns dots untouched)
and click-multiplier (more plankton per click), with ~1.15x/level scaling costs and
affordability-gated buy buttons. Verified by `node --check` (SYNTAX_OK) and a DOM-stub
sim asserting buy/deduct/gate/cost-scaling, passive +3 growth with zero clicks, and
perClick=2 manual gain — ALL SIM ASSERTIONS PASSED, no console errors.

### 2026-06-25 · f3-persist

Tide Pool now **persists across reloads and rewards time away**.

- The durable economy (`plankton`, `perClick`, `auto{count,cost}`, `click{level,cost}`)
  plus a `lastSeen` timestamp is serialized to `localStorage` under the **versioned
  key `tidepool-B-v1`** on every state change (manual click, each buy, every passive
  tick, init, reset). Write is throttled (~500ms, trailing-coalesced); `lastSeen` is
  re-stamped fresh on each write. Only the economy is saved — transient `dots`/`ripples`
  are not.
- On load the save is **restored before the render loop starts**, so plankton AND
  upgrade counts/costs survive a reload. Every field is merged through a finite-number
  guard; missing or corrupt JSON silently falls back to defaults.
- **Offline progress**: on load, `elapsed = (Date.now()-lastSeen)/1000` grants
  `floor(auto.count * AUTO_RATE * elapsed)` plankton, computed purely from the saved
  timestamp (works with the tab closed), **bounded by `OFFLINE_CAP_S = 8h`** and
  NaN/negative-guarded. Surfaced via a `#away-note` ("while you were away (…): +N plankton").
- **Reset**: a `Reset progress` button in `#shop` calls `confirm()`; on yes it
  `removeItem`s the key, returns state to `defaultEconomy()` (plankton 0, perClick 1,
  auto/click back to base count/level/cost), clears the away-note, and re-renders
  HUD+shop immediately. Cancel keeps progress.

Tuning: `SAVE_KEY="tidepool-B-v1"`, `SAVE_THROTTLE_MS=500`, `OFFLINE_CAP_S=8*3600`.
New helper `defaultEconomy()` is the single source of truth shared by fresh-start
and reset (defaults can't drift).

### Interfaces / how used
- Refresh the page → progress is right where you left it, plus any offline plankton.
- `Reset progress` button (in the shop, under the upgrades) wipes the save after a
  confirm dialog.
- Downstream arcs: call `saveState()` after any new economy mutation; extend
  `serializeState()`/`applySave()`/`defaultEconomy()` together when adding a new
  persisted field, and bump the key version (`tidepool-B-v2`) on a breaking schema change.

Verified: `node --check` SYNTAX_OK + DOM/localStorage-stub sim — round-trip,
offline grant, 8h cap, corrupt-JSON fallback, reset-to-defaults, cancelled-reset —
ALL SIM ASSERTIONS PASSED, no console errors.
