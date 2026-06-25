# proof — f1-core
contract: f1-core
accepted: yes

Verified in headless Chrome (Chrome for Testing) driven over CDP, real-time. (1) Single self-contained index.html opened directly via file:// — no build, no network deps. (2) Pool rendered: canvas 900x557, rendered=true; screenshot shows teal water + caustics + vignette (output/proof-screenshot.png). (3) Click spawns visible plankton at click point — screenshot shows glowing plankton at the 3 click sites. (4) Counter incremented exactly: 3 synthetic pointerdown clicks -> count 0->3 (delta=3, +1/click). (5) Juicy feedback present: screenshot shows 3 expanding ripple rings + pop/glow plankton; drift/bob update path exercised over 8 rendered frames. (6) No console errors: errors=[] across load + clicks + 8 animation frames. CDP result: {before:'0',after:'3',delta:3,canvasW:900,canvasH:557,rendered:true,framesRan:8,errors:[]}.
