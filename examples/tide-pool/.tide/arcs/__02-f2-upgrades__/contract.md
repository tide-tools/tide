# contract — f2-upgrades

slug: f2-upgrades
goal: Add an upgrades shop so spent plankton converts into idle and active power: at least two buyable upgrades — an auto-spawner (passive plankton/sec) and a click-multiplier (more plankton per click) — with a small on-screen shop UI whose item costs scale as you buy, and an auto-spawn tick that adds plankton over time even without clicking.
criteria: 1) A visible shop UI panel lists buyable items with name, effect, current cost, and owned/level count. 2) At least two distinct upgrade types: auto-spawner (each level adds passive plankton/sec) and click-multiplier (each level increases plankton per click). 3) Buying deducts cost from a spendable plankton balance; item greys/disables when unaffordable. 4) Each upgrade cost scales upward per purchase (geometric growth) and updates live in the UI. 5) An auto-spawn loop ticks on a timer, increasing plankton over time from owned auto-spawners with counter+visuals updating at zero clicks. 6) Clicks grant plankton equal to current click-multiplier. 7) Still one self-contained index.html, no build, opens in browser, zero console errors; verified by an automated browser check (buy each upgrade, see cost scale, see passive income accrue).
project: /Users/socaseinpoint/Documents/projects/tide/examples/tide-pool
state: close
sign: orchestrator @ 2026-06-25
# supersedes: <slug of the contract this one pivots from — optional; alias: prev:>
cannon-rev: f3e6a799a7dc

## IS → TO-BE
<where it is now → where this contract takes it>

## where we are
<current step / bottleneck>
