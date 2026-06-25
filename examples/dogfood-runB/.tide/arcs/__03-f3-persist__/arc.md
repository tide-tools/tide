# 03-f3-persist

goal: Persist Tide Pool to localStorage (plankton + all upgrades), restore on load, grant bounded offline progress from elapsed time, and add a confirm-gated Reset button.
status: done
cannon-rev: cc0c876d75c1
# supersedes: <slug of the arc this one replaces — optional; alias: prev:>

## input
input/ — empty; brief carried in contract.md (criteria 1-5). Builds on f2-upgrades economy in index.html (window.TidePool store).

## output → pointers
- output/notes.md — what was built + how it maps to the criteria
- output/proof.md — verification: node --check SYNTAX_OK + DOM/localStorage-stub sim (round-trip, offline grant, cap, corrupt-JSON, reset)
- output/persist-sim.js — runnable sim harness (re-run: `node output/persist-sim.js`)
- index.html — the durable artifact (save/restore/offline/reset added, single file kept working)
