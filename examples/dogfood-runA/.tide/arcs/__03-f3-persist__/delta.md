# delta — f3-persist
merged: yes

## Cannon journal entry (proposed)

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
