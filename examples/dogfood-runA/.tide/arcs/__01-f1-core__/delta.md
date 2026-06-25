# delta — f1-core
merged: yes

## What it is
Tide Pool is a single-file (`index.html`) browser idle/clicker. No build, no CDN, opens directly.
As of f1-core it has its **core loop**: click the pool → gather plankton.

## State & components
- `index.html` — the whole game (inline HTML + CSS + JS, IIFE, `"use strict"`).
- `#pool` — circular clickable tide-pool surface (DOM, radial-gradient water).
- `#count` — visible plankton counter (integer `plankton`).
- `.plankton` — spawned sprite (DOM div), capped at `MAX_SPRITES = 220`.
- `.ripple` — click feedback ring.

## Interfaces / how used
- Input: `pointerdown` on `#pool` (mouse/touch); `Enter`/`Space` gathers at pool center.
- Each click: `plankton += 1` → updates `#count`, spawns one `.plankton` at the click point,
  emits one `.ripple`. Initial hint fades on first click.
- Self-contained: zero external network dependencies.

## Cannon journal
- f1-core: established the core click+spawn+count loop and the single-file architecture
  (DOM sprites, IIFE, sprite cap). This is the substrate later features (passive tick, upgrades,
  persistence) build on.

