# delta — f3-persist
merged: yes

## 2026-06-25 · f3-persist

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
