# f2-upgrades — build notes

## What was built
Added an **upgrades shop** to the single-file `index.html` on top of the f1 click loop.
Two upgrade tracks, both spending plankton with geometric cost scaling:

- **Auto-Spawner** — each level adds +1 passive plankton/sec via a 1Hz `setInterval` tick
  that increments `plankton`, refreshes `#count`, and occasionally spawns a sprite
  (respecting `MAX_SPRITES = 220`).
- **Click-Multiplier** — each level adds +1 to plankton gained per pool click
  (f1 was `plankton += 1`; now `plankton += clickValue()` where `clickValue = 1 + clickLevel`).

The shop is a fixed `#shop` panel (top-right) listing each upgrade's name, current level,
next cost, and effect. Buttons disable when unaffordable; a HUD `#rate` chip shows `(+N/s)`.

## How it works (engine)
- `upgrades = { auto, click }`, each `{ level, base, ...elIds }`.
- `costOf(u) = Math.ceil(u.base * GROWTH^u.level)`, `GROWTH = 1.15` (geometric).
- `renderShop()` repaints levels/costs and toggles `button.disabled = plankton < cost`.
- `buy(key)` guards `plankton < cost` (never lets an unaffordable buy through),
  deducts, bumps level, re-renders.
- f1 path untouched structurally: `gather()` still spawns sprite + ripple + fades hint;
  the only change is `+= clickValue()` instead of `+= 1`, plus a `renderShop()` call so
  affordability updates as plankton accrues.

## Criteria → evidence
1. **Shop lists >=2 upgrades w/ name+level+cost** → `#shop` has `buy-auto` + `buy-click`,
   each rendering `lvl-*` and `cost-*`. OK.
2. **Auto-spawner passive/sec via setInterval, updates #count** → 1000ms interval adds
   `autoRate()` (= auto level) plankton, calls `setCount()`. OK.
3. **Click-multiplier increases plankton/click** → `clickValue() = 1 + click level`;
   sim: after buying click L1, click yields 2. OK.
4. **Buy deducts + disables when unaffordable** → `buy()` deducts `costOf`; `renderShop()`
   sets `.disabled = plankton < cost`; sim shows click upgrade un-affordable at 2 plankton. OK.
5. **Geometric cost scaling** → auto seq 10,12,14,16,18; click seq 15,18,20,23,27 (×1.15). OK.
6. **f1 loop intact, single self-contained file, no deps** → ripple/spawn/keyboard path
   preserved; structural check confirms one IIFE/`"use strict"`, zero external src/href. OK.

## Manual verification
- Node simulation of cost/click/affordability math (all expected values matched).
- Static structural scan of `index.html` — 11/11 checks OK (shop present, two buttons,
  setInterval, MAX_SPRITES guard in tick, clickValue in gather, geometric cost, disabled
  gate, buy guard, f1 ripple intact, single IIFE, no external network refs).
- Browser smoke deferred: the shared Playwright Chrome profile was locked by a parallel
  run ("Browser is already in use … use --isolated"); the MCP tool exposes no isolated
  flag, so live-browser verification was substituted with the deterministic checks above.

## Deliverable
`index.html` (single self-contained file) — open directly in a browser.
