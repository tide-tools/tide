# f3-persist — proof

## 1. Syntax
Extracted the `<script>` body and ran `node --check` → **SYNTAX_OK**.

```
extracted 12146 chars
SYNTAX_OK
```

## 2. DOM + localStorage stub simulation
Harness: `output/persist-sim.js`. Stubs `localStorage` (object-backed), DOM
elements (textContent/disabled/classList/listeners), canvas ctx (no-op proxy),
`confirm`, `performance.now`, `requestAnimationFrame`/`setInterval`/`setTimeout`
(no real loops). Each "reload" boots a fresh game instance against the shared
store. Re-run: `node output/persist-sim.js`.

### Assertions (all PASS, no console errors)
- **A — save-then-restore round-trip**: buy 2× auto + 1× click (plankton 1000→954),
  save key written; reload → `plankton`, `auto.count`+`cost`, `click.level`+`perClick`+`cost`
  all survive identically.
- **B — offline grant**: save with `auto.count=5`, `lastSeen` 100s ago → on load
  grants `floor(5*1*100)=500` plankton; `#away-note` reads
  "while you were away (1m 40s): +500 plankton".
- **B2 — offline cap**: `lastSeen` 1000 days ago, `auto.count=1` → grant clamped to
  8h = **28800** (no absurd number / NaN).
- **C — corrupt JSON**: stored `"{not valid json"` → falls back to defaults
  (plankton 0, perClick 1, auto.count 0); no throw.
- **D — reset**: confirm=true → key removed, state back to defaults
  (plankton 0 / perClick 1 / auto 0,cost 10 / click 0,cost 25), and the post-reset
  save holds those defaults.
- **E — reset cancelled**: confirm=false → progress (plankton 555) untouched.

```
ALL SIM ASSERTIONS PASSED
```

## Verdict
All 5 contract criteria met. Survives reload, offline progress is bounded and
NaN-safe, reset is confirm-gated and immediate, corrupt/missing saves degrade to
defaults. No console errors in the sim.
