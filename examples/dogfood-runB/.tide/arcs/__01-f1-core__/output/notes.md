# f1-core — output

## Result pointer
- Game file: `index.html` (repo root of dogfood-runB) — self-contained HTML+CSS+JS, no external deps.

## What was built
"Tide Pool" idle/clicker base, canvas-rendered:
- Visible tide-pool area: 480x320 `<canvas id="pool">` with a radial-gradient water bg + faint animated caustic lines.
- HUD: "Plankton" label + counter starting at `0`.
- Click (pointerdown) on the pool:
  - `state.plankton += 1`, counter text updated.
  - spawns a visible plankton dot at the click point (jittered ±10px, clamped to pool, teal hue 140–190, bobbing wobble + pulse).
  - emits a ripple ring at the click point (expands + fades over 0.6s) as visual feedback.
- `requestAnimationFrame` render loop draws bg → dots → ripples.
- State exposed as `window.TidePool` for later arcs (spawn loop, upgrades).

## Design notes for downstream arcs
- `state = { plankton, dots:[], ripples:[] }` is the single mutable store; `window.TidePool` points at it.
- Plankton dots bounded at `MAX_DOTS = 600` (oldest shifted) — keep this when an idle spawn loop is added.
- Click handling maps CSS px → canvas space via `getBoundingClientRect`, so responsive scaling works.
- A spawn-loop arc can just push to `state.dots` and bump `state.plankton`; an upgrades arc can read/write the same store.

## Verification (evidence)
- `node --check` on the inline script: SYNTAX_OK (no parse errors).
- DOM-stub simulation in node: initial counter `0`, dots `0`; after 3 synthetic clicks → counter `3`, dots `3`, ripples `3`; first dot in-bounds at (105,97) r=3.
- No exceptions thrown during script eval or the 3 click handlers (would have aborted the node run).
- Browser Playwright check was blocked (shared browser instance in use by a parallel run; `--isolated` not exposable through the MCP) — substituted the node DOM-stub proof above.
