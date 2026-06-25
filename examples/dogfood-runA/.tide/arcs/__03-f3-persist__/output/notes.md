# f3-persist — result

Persistence layer added to `index.html`, in place, with the f1 click+spawn+ripple loop and
the f2 upgrades shop/economy fully preserved. Single-file, zero network deps, IIFE + "use strict".

## What was built

1. **save()** — serializes the minimal source-of-truth
   `{ plankton: Math.floor(plankton), autoLevel, clickLevel, savedAt: Date.now() }`
   to `localStorage["tidePoolA"]`. Wrapped in try/catch so private-mode / quota errors degrade
   gracefully (game keeps running in-memory). Called on **every click** (`gather`), **every buy**,
   a **15s periodic flush** (`setInterval(save, FLUSH_MS)`), and **`beforeunload`**.

2. **load()** on startup — reads + `JSON.parse`s the save, validating each field before trusting
   it (numbers, finite, non-negative). Restores `plankton` + both upgrade levels, then repaints
   `#count`, `#rate`, and the shop via `setCount()` + `renderShop()`. Corrupt/missing save → fresh
   start, never a crash.

3. **Offline progress** — inside `load()`: `elapsedSec = (Date.now() - savedAt)/1000`,
   grant `Math.floor(autoRate() * min(elapsedSec, OFFLINE_CAP_SEC))` with
   `OFFLINE_CAP_SEC = 8h`. Only granted when `autoRate() > 0`. A self-dismissing toast (`#offline`)
   shows "While you were away: +N plankton" for 4.5s. After boot we `save()` again to re-baseline
   `savedAt` so offline income is never double-counted on the next load.

4. **Reset button** (`#reset`, destructive red styling, sits under the upgrade buttons in `#shop`):
   `window.confirm(...)` → `resetGame()` wipes the save key, zeroes plankton + both levels, repaints,
   restores the "tap the pool" hint, and writes a fresh baseline save.

## Verification

- `node --check` on the extracted script → **JS_SYNTAX_OK**.
- Headless node harness mirroring the exact save/load/offline formulas — **ALL PASS**:
  - state (plankton + auto/click levels) restored across a simulated reload;
  - offline grant = `autoRate * elapsedSec` floored;
  - offline capped to 8h for huge gaps;
  - corrupt save handled gracefully (no crash, no restore);
  - no offline income when `autoRate() == 0`.
- Live in-browser check was blocked: the shared Playwright Chrome profile was locked by a
  concurrent dogfood run ("Browser is already in use … use --isolated"). Logic proven headlessly
  instead. (See friction.)

## Files
- `index.html` — edited in place (HTML reset button + offline toast, CSS, JS save/load/offline/reset).
