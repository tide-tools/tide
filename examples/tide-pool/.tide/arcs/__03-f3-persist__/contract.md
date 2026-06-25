# contract — f3-persist

slug: f3-persist
goal: Persist game state across reloads and grant offline progress. Save plankton count and all upgrade levels to localStorage; restore them on load so the game survives a page reload. On load, compute elapsed real time since last save and grant offline plankton from the current auto-spawn rate. Add a reset button that wipes the save after an explicit confirm.
criteria: 1) Plankton count + upgrade levels persist to localStorage and restore on reload (no progress lost). 2) Save happens automatically (on change/tick and before unload), writing a timestamp each save. 3) On load, offline progress is granted from elapsed time x auto-spawn rate. 4) Reset button clears the save and in-memory state after a confirm dialog, returning to a fresh start. 5) Single self-contained index.html, no build step, opens directly in a browser.
project: /Users/socaseinpoint/Documents/projects/tide/examples/tide-pool
state: close
sign: orchestrator @ 2026-06-25
# supersedes: <slug of the contract this one pivots from — optional; alias: prev:>
cannon-rev: 14c2d5f96056

## IS → TO-BE
<where it is now → where this contract takes it>

## where we are
<current step / bottleneck>
