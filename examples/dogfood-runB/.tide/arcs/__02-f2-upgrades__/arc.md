# 02-f2-upgrades

goal: Upgrades shop — spend plankton on auto-spawner (passive/sec) + click-multiplier, scaling costs, live auto-spawn ticks.
status: done
cannon-rev: 034368a52579
# supersedes: <slug of the arc this one replaces — optional; alias: prev:>

## input
f1-core delivered the playable base: index.html with window.TidePool store (state.plankton, state.dots, ripples) + render loop + click→spawn→ripple. This arc layers a shop on top.

## output → pointers
- index.html — adds a shop UI panel + auto-spawner passive tick + click-multiplier, reusing window.TidePool.
- output/notes.md — build notes; output/result.txt — pointer to the game file.
