# contract — f2-upgrades

slug: f2-upgrades
goal: Add an upgrades shop: spend plankton on at least two upgrades — an auto-spawner (passive plankton/sec) and a click-multiplier (more plankton per click) — with scaling costs and live auto-spawn ticking over time.
criteria: 1) A visible shop UI in index.html lists >=2 upgrades (auto-spawner, click-multiplier) each showing its current cost and level/count; 2) buying an upgrade deducts its cost from the plankton counter and is disabled/blocked when plankton < cost; 3) each purchase raises that upgrade's cost on a scaling curve (~1.15x per level); 4) auto-spawner adds plankton passively over time (per-second tick) proportional to its count, spawning visible dots and incrementing the counter without clicking; 5) click-multiplier increases plankton gained per manual click; 6) all logic self-contained in the single file, no JS console errors, reuses window.TidePool store.
project: /Users/socaseinpoint/Documents/projects/tide/examples/dogfood-runB
state: close
sign: orchestrator @ 2026-06-25
# supersedes: <slug of the contract this one pivots from — optional; alias: prev:>
cannon-rev: cc0c876d75c1

## IS → TO-BE
<where it is now → where this contract takes it>

## where we are
<current step / bottleneck>
