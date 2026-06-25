# contract — f2-upgrades

slug: f2-upgrades
goal: Add an upgrades shop: spend plankton on >=2 upgrade types (auto-spawner = passive plankton/sec, click-multiplier = more plankton per click), with a small shop UI and scaling costs, integrated into the existing single-file index.html without breaking the f1 click loop.
criteria: 1) Shop UI lists >=2 upgrades each showing name, current level, and next cost. 2) Auto-spawner upgrade: each level adds passive plankton/sec that ticks over real time (setInterval) and updates #count. 3) Click-multiplier upgrade: each level increases plankton per pool click. 4) Buying deducts cost from plankton; button disabled when unaffordable. 5) Costs scale geometrically per level. 6) f1 core click+spawn+ripple loop still works; single self-contained index.html, no external deps.
project: /Users/socaseinpoint/Documents/projects/tide/examples/dogfood-runA
state: close
sign: orchestrator @ 2026-06-25
# supersedes: <slug of the contract this one pivots from — optional; alias: prev:>
cannon-rev: bb6d4109f145

## IS → TO-BE
<where it is now → where this contract takes it>

## where we are
<current step / bottleneck>
