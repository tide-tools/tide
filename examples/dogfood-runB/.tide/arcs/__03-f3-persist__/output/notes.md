# f3-persist — notes

Persistence layer added to `index.html` on top of the existing `window.TidePool`
store. Single file, no deps. Prior features (core click, upgrades, passive tick)
untouched and still working.

## What was added

- **Versioned key**: `SAVE_KEY = "tidepool-B-v1"`.
- **`defaultEconomy()`** — single source of truth for both fresh-start and reset:
  `{ plankton:0, perClick:1, auto:{count:0,cost:AUTO_BASE}, click:{level:0,cost:CLICK_BASE} }`.
- **`serializeState()`** — durable economy only (plankton, perClick, auto{count,cost},
  click{level,cost}) + `lastSeen: Date.now()`. Transient `dots`/`ripples` are NOT saved.
- **`saveState()`** — throttled (`SAVE_THROTTLE_MS=500`): writes immediately, then
  coalesces a trailing write. `lastSeen` is re-stamped on every write, so it stays
  fresh. Wrapped in try/catch (storage full/disabled → game keeps running in-memory).
  Called on: manual click, buyAuto, buyClick, every passiveTick, init, and reset.
- **`restoreState()`** — runs BEFORE the render loop / first paint. Reads the key,
  `JSON.parse` in try/catch, validates it's an object, then `applySave()` merges each
  field through a `num()` guard (rejects non-finite / wrong types → per-field default).
  Missing/corrupt save → silently keeps defaults.
- **Offline progress** (`applyOffline`): `elapsed = (Date.now()-lastSeen)/1000`,
  rejects `<=0`/NaN, capped at `OFFLINE_CAP_S = 8h`, grants
  `floor(auto.count * AUTO_RATE * elapsed)` plankton. Works with the tab closed
  (computed purely from the saved timestamp). Surfaced via a small
  `#away-note`: "while you were away (1m 40s): +500 plankton".
- **Reset** (`resetGame`): a `Reset progress` button in `#shop` → `confirm()`; on yes,
  `localStorage.removeItem(SAVE_KEY)`, restores `defaultEconomy()` into state, clears
  the away-note, re-renders HUD+shop, and persists defaults. Cancel keeps progress.

## Criteria mapping (contract.md)
1. Save on every state change under versioned key — done (5 call sites + init).
2. Restore plankton + upgrade counts/costs on load — done, before loop starts.
3. Offline grant from elapsed, bounded — done, 8h cap, NaN-guarded.
4. Reset button with confirm → clears key + defaults, updates UI now — done.
5. node --check SYNTAX_OK + DOM-stub sim (round-trip / offline / reset) — done, see proof.md.

## Design choices
- Only the economy is serialized; dots/ripples are cosmetic and regenerate, so the
  save stays tiny and version-stable.
- `lastSeen` re-stamped every tick means offline progress measures from the last
  moment the tab was actually alive, not the last buy.
- Reset reuses `defaultEconomy()` (DRY) so defaults can never drift from fresh-start.
