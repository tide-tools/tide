# 03-f3-persist

goal: persist game state to localStorage (balance/lifetime/upgrades) with restore-on-load, offline progress, and a confirm-gated reset
status: done
cannon-rev: f3e6a799a7dc
# supersedes: <slug of the arc this one replaces — optional; alias: prev:>

## input
f1-core + f2-upgrades game state (in-memory balance/lifetime/upgrades) — make it durable.

## output → pointers
- output/notes.md — persistence/offline/reset design + verification notes
- index.html — save/load + offline grant + reset wired into the single-file game
- delta.md — cannon update (merged into CANON.md on close)
