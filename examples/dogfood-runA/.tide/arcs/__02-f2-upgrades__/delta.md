# delta — f2-upgrades
merged: yes

## What it is
Tide Pool now has an **upgrades economy** layered on the f1 click loop. Plankton is no longer
just a tally — it's a currency you spend on two upgrade tracks, turning the clicker into an
idle/clicker: click to earn, buy upgrades to earn faster (per-click and passively).

## State & components
- `#shop` — fixed top-right upgrades panel (DOM), one button per upgrade track.
- `upgrades` — JS map `{ auto, click }`, each `{ level, base, ...elemIds }`; the source of truth
  for the economy.
- `Auto-Spawner` (`#buy-auto`) — passive income track; level N = +N plankton/sec.
- `Click-Multiplier` (`#buy-click`) — per-click track; level N = +N plankton per pool click.
- `#rate` — HUD chip showing current passive rate `(+N/s)`.
- `GROWTH = 1.15` — geometric cost growth constant.

## Interfaces / how used
- `clickValue() = 1 + auto/click level` → f1 `gather()` now does `plankton += clickValue()`
  (was `+= 1`); still spawns sprite + ripple + fades hint (f1 loop preserved).
- `costOf(u) = ceil(base * 1.15^level)` — geometric scaling; auto base 10, click base 15.
- `buy(key)` — guards affordability (`plankton < cost` → no-op), deducts cost, bumps level.
- `renderShop()` — repaints levels/costs and disables any button whose cost exceeds plankton;
  called on every click, buy, and passive tick so affordability stays live.
- Auto-spawner: a 1Hz `setInterval` adds `autoRate()` plankton, refreshes `#count`, and
  occasionally spawns a sprite (respecting `MAX_SPRITES = 220`).
- Still self-contained: zero external network dependencies, single IIFE / `"use strict"`.

## Cannon journal
- f2-upgrades: added the upgrades shop + economy (auto-spawner passive tick, click-multiplier,
  geometric costs, affordability-gated buying). Plankton becomes spendable currency. Builds
  directly on the f1 substrate without altering the core click+spawn+ripple feel. Sets up
  f3 (persistence) — there is now meaningful economic state worth saving across reloads.
