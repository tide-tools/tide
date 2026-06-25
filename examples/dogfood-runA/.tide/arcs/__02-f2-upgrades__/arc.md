# 02-f2-upgrades

goal: Add an upgrades shop — spend plankton on auto-spawner (passive/sec) + click-multiplier, scaling costs, in the single-file index.html.
status: done
cannon-rev: ce43a1d5ffd5
# supersedes: <slug of the arc this one replaces — optional; alias: prev:>

## input
f1-core's working click loop in index.html (plankton += 1, sprite spawn, ripple, MAX_SPRITES=220).

## output → pointers
- ../../../index.html — shop UI + auto-spawner tick + click-multiplier added (f1 loop intact).
- output/notes.md — build notes, criteria→evidence mapping, manual verification.
- delta.md — proposed cannon-delta ("Tide Pool now has an upgrades economy").
