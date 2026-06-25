# delta — f1-core
merged: yes

## Proposed cannon update — Tide Pool now IS

Tide Pool is a single self-contained `index.html` (HTML+CSS+JS inline, no build step,
opens directly in a browser). It is a cozy idle/clicker rendered on a full-viewport
`<canvas>`.

### What it is
A calm tide-pool scene — teal gradient water with slowly drifting caustic light and a
soft vignette — that the player clicks to wake plankton. A large HUD counter (top-left,
label `PLANKTON`) shows the total ever spawned.

### State & components
- `count` — integer plankton total (in-memory only; **no persistence yet**).
- `plankton[]` — live drifting particles; capped at `MAX_PLANKTON = 240` for perf
  (oldest retired). The counter keeps climbing past the cap.
- `ripples[]` — transient expanding click rings.
- One `requestAnimationFrame` render loop draws water → ripples → plankton.

### Core loop (the durable truth of this arc)
Clicking anywhere in the pool (`pointerdown`) at point (x, y):
1. emits an expanding **ripple ring** at the click,
2. spawns a **plankton** particle (small cluster) that pops in (scale 0→1) and then
   gently drifts/bobs, tethered near its birth point,
3. increments the visible **plankton counter by exactly +1** (with a scale-bump).

### Feel / feedback model
Clicking is juicy: ripple expand-and-fade + plankton pop-in + ongoing drift/bob + soft
glowing halos. Warm, calm, tactile.

### Interfaces / how used
Open `index.html` in any browser. Click the pool to spawn plankton and grow the count.
No controls, no build, no storage. (Upgrades and persistence are future arcs.)

### Verified
Headless Chrome (CDP): 3 clicks → counter delta exactly 3; canvas rendered; 8 frames
ran clean; zero console errors; screenshot shows pool + ripples + glowing plankton.
