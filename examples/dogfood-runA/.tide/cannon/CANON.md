# CANON.md — tide-pool-A

## What it is

## State & components

## Interfaces / how used

## Cannon journal

### 2026-06-25 · f1-core

Tide Pool is a single-file (`index.html`) browser idle/clicker. No build, no CDN, opens directly.
As of f1-core it has its **core loop**: click the pool → gather plankton.

### 2026-06-25 · f2-upgrades

Tide Pool now has an **upgrades economy** layered on the f1 click loop. Plankton is no longer
just a tally — it's a currency you spend on two upgrade tracks, turning the clicker into an
idle/clicker: click to earn, buy upgrades to earn faster (per-click and passively).

### 2026-06-25 · f3-persist

Tide Pool now **persists across reloads and rewards idle time**. The economy state
(plankton + both upgrade levels) is saved to `localStorage` and restored on load, so a refresh
or a return visit no longer wipes progress. While away, the auto-spawner keeps "earning":
on load the game grants offline plankton for elapsed time (capped at 8h) and tells you what you
banked. A Reset button (confirm-gated) wipes the save and returns to a fresh pool. Still a single
self-contained `index.html`, zero network deps, f1 click loop and f2 shop untouched.

## State & components (append to CANON.md)
- `SAVE_KEY = "tidePoolA"` — stable localStorage key holding the whole save.
- Save shape: `{ plankton:int, autoLevel:int, clickLevel:int, savedAt:epoch_ms }` — the minimal
  source of truth (sprites/DOM are derived, not saved).
- `OFFLINE_CAP_SEC = 8h`, `FLUSH_MS = 15000` — offline-income ceiling and periodic-flush interval.
- `#reset` — destructive button in `#shop` (confirm-gated wipe).
- `#offline` — transient toast announcing offline earnings.

## Interfaces / how used (append to CANON.md)
- `save()` — floors plankton, writes the snapshot under `SAVE_KEY`; try/catch so storage failures
  degrade to in-memory only. Triggered on every click, every buy, a 15s flush, and `beforeunload`.
- `load()` — validates + restores plankton/levels (corrupt save → fresh start), then computes
  offline income = `floor(autoRate() * min(elapsedSec, OFFLINE_CAP_SEC))` (only if `autoRate() > 0`).
  Returns the granted amount; boot re-saves afterward to re-baseline `savedAt` (no double-count).
- `resetGame()` — removes the save key, zeroes plankton + both upgrade levels, repaints, restores
  the hint, writes a fresh baseline.
- f1/f2 preserved: `gather()` and `buy()` are unchanged except for an added `save()` call.

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

## State & components
- `index.html` — the whole game (inline HTML + CSS + JS, IIFE, `"use strict"`).
- `#pool` — circular clickable tide-pool surface (DOM, radial-gradient water).
- `#count` — visible plankton counter (integer `plankton`).
- `.plankton` — spawned sprite (DOM div), capped at `MAX_SPRITES = 220`.
- `.ripple` — click feedback ring.

## Interfaces / how used
- Input: `pointerdown` on `#pool` (mouse/touch); `Enter`/`Space` gathers at pool center.
- Each click: `plankton += 1` → updates `#count`, spawns one `.plankton` at the click point,
  emits one `.ripple`. Initial hint fades on first click.
- Self-contained: zero external network dependencies.

## Cannon journal
- f1-core: established the core click+spawn+count loop and the single-file architecture
  (DOM sprites, IIFE, sprite cap). This is the substrate later features (passive tick, upgrades,
  persistence) build on.
