# delta — f3-persist
merged: yes

## Proposed cannon update

### 2026-06-25 · f3-persist

Tide Pool now **remembers itself**: the game state survives a reload and rewards time
away. Still one self-contained `index.html` (HTML+CSS+JS inline, no build, opens in a
browser).

### What changed since f2-upgrades
The economy is no longer in-memory only. Spendable `balance`, `lifetime`, and every
upgrade level (Plankton Bloom, Tide Surge) are saved to `localStorage` and restored on
load, so no progress is lost across reloads. Idle time now pays out.

### State & components (additions)
- **Save key** — a single namespaced `localStorage` key `tidePool.save.v1` holding
  `{ v, balance, lifetime, upgrades:{spawner,click}, lastSaved }`. `v` is a schema
  version for forward migration; `lastSaved` is a `Date.now()` timestamp written on
  every save.
- **`saveGame()` / `loadGame()`** — save wraps storage writes in try/catch (never breaks
  on full / private-mode storage); load parses defensively (missing keys → defaults,
  corrupt JSON → fresh start, never throws; values sanitized and clamped, invariant
  `lifetime >= balance`).
- **Debounced auto-save** — `scheduleSave()` (800ms debounce) fires on every click,
  every purchase, and every passive auto-tick credit; plus a hard flush on
  `window 'beforeunload'`.
- **Offline cap** — `OFFLINE_CAP_SEC = 8h` caps idle credit.
- **Reset button** + **away toast** — new UI (see below).
- `window.__tidePool` verification hook extended with `saveGame`, `loadGame`,
  `resetGame`, `saveKey`, and a `setLastSaved` test helper.

### Persistence + offline (the durable truth of this arc)
- **On load**: restore balance, lifetime, and upgrade levels; then compute
  `elapsedSeconds = (now − lastSaved)/1000` (clamped `>=0`, capped at 8h) and grant
  `offlinePlankton = floor(elapsedSeconds × autoSpawnRate)`, where the rate derives from
  the restored Plankton-Bloom level. The grant is added to balance and lifetime.
- **While-you-were-away notice**: only when the offline grant is `> 0`, a cozy dismissible
  teal toast (editorial-mono house style) reads "while you were away (⟨elapsed⟩) —
  +N plankton drifted into the pool", with a × close button.
- **Auto-save**: every change/tick (debounced) and on tab unload, each save stamping a
  fresh timestamp — so the next load's offline math is accurate.

### Reset (durable truth)
A visible **Reset progress** button sits in a new shop footer, warm-red so it reads as
destructive vs the teal buy buttons. Clicking asks `window.confirm("Reset all progress?
This cannot be undone.")`. **Cancel** is a strict no-op. **Confirm** removes the save key,
zeros balance / lifetime / all upgrade levels, clears the live particles, hides the away
toast, re-renders the HUD + shop + rate, and writes a clean fresh save so a subsequent
reload stays fresh.

### Interfaces / how used
Open `index.html`, play, close the tab — your plankton and upgrades are still there next
time, plus whatever the auto-spawner earned while you were gone. Reset progress wipes it
back to a fresh pool after a confirm.

### Verified
Headless Chrome (Playwright, served on localhost): restore-on-reload exact (balance 500 /
lifetime 800 / spawner Lv3 / click Lv2 → identical after reload); offline grant
floor(~53s × 4/sec) = +213 with the away toast shown; reset-cancel = no-op, reset-confirm =
all zeros + fresh save; corrupt JSON / partial save / absent key all degrade to a clean
fresh start with zero thrown errors. Zero console errors.

