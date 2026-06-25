# delta — f2-upgrades
merged: yes

## Proposed CANON.md delta (durable truth)

### State & components — extend the `state` store
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
