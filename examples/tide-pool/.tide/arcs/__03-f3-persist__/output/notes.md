# f3-persist — worker result

Built persistence + offline progress + reset on top of f1-core and f2-upgrades, all
inside the single `index.html` (no build, opens via file://). No prior features regressed.

## What was added

### 1. Persistence (localStorage)
- Single namespaced key: **`tidePool.save.v1`**.
- Serialized state: `{ v, balance, lifetime, upgrades:{spawner,click}, lastSaved }`.
  `v` is a schema version for future migration; `lastSaved` is `Date.now()` stamped on
  every write.
- `saveGame()` wraps `localStorage.setItem` in try/catch — storage-full / private-mode
  never breaks the game.
- `loadGame()` parses defensively: missing key / empty → fresh start; corrupt JSON →
  fresh start (returns `null`, never throws); numbers sanitized (`Number.isFinite && >=0`,
  else default); upgrade levels floored to `>=0`; missing upgrade keys → default 0;
  invariant `lifetime >= balance` enforced.
- Auto-save is **debounced** (800ms): `scheduleSave()` is called on every click, every
  purchase, and on every passive auto-tick credit. Plus a hard flush on
  `window 'beforeunload'`.

### 2. Offline progress
- On load, `loadGame` computes `elapsed = (now - lastSaved)/1000`, clamped to `>=0` and
  capped at **8h** (`OFFLINE_CAP_SEC`) so the number stays sane.
- `offline = floor(elapsed * perSec())` where `perSec` derives from the restored
  Plankton-Bloom level — so it must be computed AFTER upgrades are restored (it is).
- Grant is added to both `balance` and `lifetime`. Only when `offline > 0` a cozy,
  dismissible toast appears: "while you were away (Nm) — +N plankton drifted into the
  pool", styled in the teal editorial-mono house style. Close button (×) hides it.

### 3. Reset
- Visible **Reset progress** button in a new shop footer (warm red-tinted, distinct from
  the teal buy buttons).
- Click → `window.confirm('Reset all progress? This cannot be undone.')`.
  - Cancel → strict no-op.
  - Confirm → `removeItem` the key, zero `balance`/`lifetime`/all upgrade levels +
    `incomeAcc`, clear live particles/ripples, hide away-toast, re-render HUD + shop +
    rate, then write a clean fresh save so a reload stays fresh.

## Verification (headless Chrome over Playwright, served on http://localhost:8799)
- **Restore on reload**: seeded balance 500 / lifetime 800 / spawner Lv3 / click Lv2 →
  after reload state matched exactly (clickGain 3, perSec 3, costs scaled).
- **Offline**: backdated `lastSaved` by 50s with perSec 4 → reload granted floor(~53s*4)=
  **+213**, balance 700→913+, away toast showed "+213 / 53s".
- **Auto-save**: changes persisted across reload with a fresh timestamp each write;
  beforeunload handler wired.
- **Reset cancel**: balance unchanged (1046→1046), save intact.
- **Reset confirm**: state all zeros, count "0", rate hidden, away hidden, save reset to
  fresh defaults.
- **Resilience**: corrupt JSON → `null`, no throw; partial save (only `balance:42`) →
  balance 42, lifetime clamped 42, upgrades default 0; absent key → `null`.
- Zero console errors (only a favicon 404 from the static server, irrelevant).

## Files touched
- `index.html` — CSS (reset button + away toast), HTML (shop footer + away toast), JS
  (persistence/offline/reset block, `scheduleSave()` calls, beforeunload, verification
  hook extended with `saveGame/loadGame/resetGame/saveKey/setLastSaved`).
