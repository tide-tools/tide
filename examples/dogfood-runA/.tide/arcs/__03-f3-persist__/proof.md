# proof — f3-persist
contract: f3-persist
accepted: yes

node --check on extracted script => JS_SYNTAX_OK. Headless node harness mirroring exact save/load/offline formulas => ALL PASS: (1) plankton+auto/click levels restored across simulated reload; (2) offline grant = autoRate*elapsedSec floored; (3) offline capped to 8h for huge gaps; (4) corrupt save handled gracefully no crash; (5) zero offline income when autoRate=0. Live browser check blocked: shared Playwright Chrome locked by concurrent dogfood run (Browser is already in use, use --isolated).
