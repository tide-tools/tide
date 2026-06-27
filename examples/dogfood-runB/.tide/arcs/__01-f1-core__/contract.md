# contract — f1-core

slug: f1-core
goal: Clicking the tide pool spawns a plankton and increments a visible plankton counter, with small visual feedback (single-file index.html, DOM or canvas).
criteria: 1) index.html opens standalone in a browser with a visible tide-pool area and a plankton counter starting at 0; 2) clicking the pool increments the counter by 1 each click and spawns a visible plankton element/dot in the pool; 3) each click produces a small visual feedback (ripple/scale/flash); 4) no JS console errors; all logic self-contained in the one file.
project: ~/projects/tide/examples/dogfood-runB
state: close
sign: orchestrator @ 2026-06-25
# supersedes: <slug of the contract this one pivots from — optional; alias: prev:>
cannon-rev: fc9420237d6f

## IS → TO-BE
Empty dogfood workspace (no game) → a self-contained index.html with a clickable tide pool that spawns plankton, increments a counter, and shows ripple feedback; runtime state on window.TidePool for later arcs.

## where we are
Built + verified (node --check SYNTAX_OK; DOM-stub 3 clicks → counter/dots/ripples 0→3, in-bounds, no exceptions). Report + proof recorded. Ready for orchestrator accept + cannon merge.
