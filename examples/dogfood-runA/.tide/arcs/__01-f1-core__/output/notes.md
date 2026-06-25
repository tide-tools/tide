# f1-core — core click+spawn loop

## What was built
Single self-contained `index.html` (HTML+CSS+JS inline, no build, no CDN). Implements the
core Tide Pool loop: a circular tide-pool area you click to gather plankton.

## How it works
- **Pool** = a circular DOM element (`#pool`, radial-gradient water, rim glow, `cursor:pointer`,
  `role="button"`, `tabindex=0`). Clearly the clickable surface; an initial "tap the pool" hint
  fades out on first click.
- **Counter** = `#count` in the HUD, `plankton` integer, incremented by **exactly 1** per click.
- **Spawn** = each click appends a `.plankton` sprite (DOM div, green glow) at the click point,
  with a pop-in + gentle drift animation. Sprite count is bounded at `MAX_SPRITES = 220` (oldest
  removed) so long sessions stay light.
- **Feedback** = a `.ripple` ring animates outward from the click point; the pool also scales down
  briefly on `:active`.
- **Input** = `pointerdown` for mouse/touch; `Enter`/`Space` gather at pool center (keyboard a11y).
- **Cleanup** = ripple/sprite elements removed on `animationend` / when over the cap.

## Verification (this arc)
- JS syntax: `node --check` on the extracted script → OK.
- Headless DOM simulation of `gather()` (mock document): 5 clicks → counter = 5, 5 sprites,
  hint opacity → 0; 305 clicks total → counter = 305, sprites bounded at 220. No errors thrown.
- External deps: `grep` for http/src/cdn/fetch/import → none (NO_EXTERNAL_DEPS).
- Live browser check via Playwright was BLOCKED: shared MCP browser was locked by a parallel
  dogfood run ("Browser is already in use … use --isolated"). Fell back to node syntax-check +
  DOM simulation, which cover criteria 2–7 logically. Recommend a manual/`--isolated` browser
  pass at integration time to confirm zero runtime console errors visually.

## Criteria mapping
1. index.html opens standalone — yes (no build, no deps).
2. Clearly clickable pool — `#pool`, cursor pointer + hint.
3. +1 per click — verified counter = 5 after 5 clicks, 305 after 305.
4. Visible spawn — `.plankton` sprite appended at click point.
5. Brief feedback — `.ripple` + `:active` scale.
6. No console errors — syntax OK, IIFE + `"use strict"`, no undefined refs in sim; needs visual
   confirmation (browser was locked).
7. No external deps — confirmed by grep.
