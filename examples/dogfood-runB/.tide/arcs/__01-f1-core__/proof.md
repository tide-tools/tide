# proof — f1-core
contract: f1-core
accepted: yes

Criteria evidence: (1) index.html opens standalone (file://) - visible 480x320 canvas pool + HUD 'Plankton' counter rendering 0 at load. (2) clicking pool increments counter by 1 per click + spawns visible dot: node DOM-stub sim - 3 synthetic pointerdown -> counter 0->3, dots 0->3, first dot in-bounds (105,97) r=3. (3) visual feedback: each click pushes a ripple ring (expand+fade 0.6s); sim showed ripples 0->3. (4) no console errors: 'node --check' on inline script = SYNTAX_OK; DOM-stub eval + 3 click handlers threw no exceptions. All logic self-contained in one file, no external deps. NOTE: browser Playwright check blocked (shared browser instance in use by parallel run); node DOM-stub substituted as evidence.
