# contract — f3-persist

slug: f3-persist
goal: Persist Tide Pool progress across reloads: save plankton + all upgrades (perClick, auto, click levels/costs) to localStorage, restore on load, award offline progress from elapsed time, and a reset button (with confirm) that wipes the save.
criteria: 1) On any state change (click, buy, passive tick) the full state (plankton, perClick, auto{count,cost}, click{level,cost}, lastSeen timestamp) is serialized to localStorage under a versioned key. 2) On page load the saved state is restored so plankton + upgrade counts/costs survive a reload. 3) Offline progress: on load compute elapsed seconds since lastSeen and grant auto.count*AUTO_RATE*elapsed plankton (bounded sanely), without the tab being open. 4) A Reset button in the shop asks confirm() and on yes clears the save key and returns state to defaults (plankton 0, perClick 1, upgrades reset). 5) node --check passes (SYNTAX_OK) and a DOM-stub sim proves save-then-restore round-trip, offline grant for a faked elapsed gap, and reset-to-defaults. No console errors.
project: ~/projects/tide/examples/dogfood-runB
state: close
sign: orchestrator @ 2026-06-25
# supersedes: <slug of the contract this one pivots from — optional; alias: prev:>
cannon-rev: 01937a9abc23

## IS → TO-BE
<where it is now → where this contract takes it>

## where we are
<current step / bottleneck>
