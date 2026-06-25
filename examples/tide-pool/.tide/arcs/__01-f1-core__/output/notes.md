# f1-core — worker notes

## What was built
A single self-contained `index.html` (HTML+CSS+JS inline, no build step) — the core
Tide Pool click loop.

### Scene
- Full-viewport `<canvas>` rendering a cozy tide pool: vertical teal gradient water
  (`#0d3340 → #0f4a59 → #0a2a36`), drifting caustic light blobs (4 `lighter`-blended
  radial gradients in slow sinusoidal motion), and a soft vignette for a contained feel.
- Animation runs on a single `requestAnimationFrame` loop.

### Click → spawn loop
- `pointerdown` on the canvas maps client coords → canvas coords, then:
  1. spawns an expanding **ripple ring** at the click point,
  2. spawns a small **plankton cluster** (2 particles) at/near the point,
  3. increments the **plankton counter** by exactly **+1**.
- Counter is a large HUD number (top-left) with the label `PLANKTON`; it bumps
  (scale 1.16) on every click via a re-triggered CSS transition.

### Juice
- Ripple: ring eases outward and fades (`lighter` blend).
- Plankton pop-in: `scale` eases 0 → 1.
- Drift/bob: each particle has damped velocity lightly tethered to its spawn point
  plus a sine bob, so plankton settle and gently breathe near where they were born.
- Soft glow halo + pulsing core per particle; 5-hue teal/green palette.

### Perf / state
- Live particle count capped at `MAX_PLANKTON = 240` (oldest retired) — the **counter
  keeps climbing** past the cap.
- All state in plain JS (`count`, `plankton[]`, `ripples[]`). **No localStorage** —
  persistence is deferred to a later arc per the brief.

## Verification (headless Chrome via CDP, real-time)
- 3 synthetic `pointerdown` clicks → counter `0 → 3` (delta **exactly 3**, +1/click).
- Canvas rendered at 900×557 (`rendered: true`).
- 8 animation frames executed (particle + ripple update paths exercised).
- `errors: []` — no console errors / uncaught exceptions on load or click.
- Screenshot confirms pool + ripple rings + glowing plankton + "3 PLANKTON" HUD.

## Files touched
- `index.html` (the game)
- `.tide/arcs/01-f1-core/output/notes.md` (this file)
- `.tide/arcs/01-f1-core/delta.md` (proposed cannon delta)
