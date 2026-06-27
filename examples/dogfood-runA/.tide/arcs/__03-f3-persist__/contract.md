# contract — f3-persist

slug: f3-persist
goal: Persist Tide Pool game state (plankton count + both upgrade levels) to localStorage and restore on load, grant offline progress for elapsed time via the auto-spawner rate, and add a Reset button with a confirm step — all within the single self-contained index.html.
criteria: 1) Plankton count + auto/click upgrade levels saved to localStorage and restored on reload (survives a full page reload). 2) On load, offline plankton granted = autoRate * elapsed_seconds since last saved timestamp, capped to a sane max. 3) A visible Reset button wipes saved state after a confirm prompt and returns the game to a fresh start. 4) Still single-file, zero external deps, f1+f2 loops preserved.
project: ~/projects/tide/examples/dogfood-runA
state: close
sign: orchestrator @ 2026-06-25
# supersedes: <slug of the contract this one pivots from — optional; alias: prev:>
cannon-rev: 3373234d4ccb

## IS → TO-BE
<where it is now → where this contract takes it>

## where we are
<current step / bottleneck>
