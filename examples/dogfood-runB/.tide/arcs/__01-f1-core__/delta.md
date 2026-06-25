# delta — f1-core
merged: yes

## What it is
Tide Pool is now a playable single-file browser idle/clicker base (`index.html`, no deps).

## State & components
- `index.html` — self-contained HTML+CSS+JS. Canvas pool (480x320) + HUD plankton counter.
- Runtime state lives in one object `state = { plankton:Number, dots:[], ripples:[] }`, exposed as `window.TidePool`.
  - `dots`: spawned plankton `{x,y,r,hue,phase,drift,born}`, bounded to `MAX_DOTS=600` (FIFO).
  - `ripples`: transient click-feedback rings `{x,y,t}`, life 0.6s.
- Single `requestAnimationFrame` loop renders background → dots → ripples.

## Interfaces / how used
- Open `index.html` in any browser. Counter starts at 0.
- `pointerdown` on the canvas: counter += 1, spawns a plankton dot at the click point (jittered, clamped to pool), emits a ripple.
- Click coords mapped CSS-px → canvas space, so it works under CSS scaling.
- Downstream arcs build on `window.TidePool`: a spawn loop pushes to `state.dots` + bumps `state.plankton`; upgrades read/write the same store.

## Cannon journal
- f1-core: established the single-file game + central mutable state store + render loop. Click → plankton + counter + ripple feedback. Verified by node syntax check and DOM-stub click simulation (0→3 counter, 3 dots, 3 ripples, in-bounds).

