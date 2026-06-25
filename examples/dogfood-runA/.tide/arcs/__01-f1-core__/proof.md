# proof — f1-core
contract: f1-core
accepted: yes

Criteria evidence: (1) index.html is standalone — opens via file:// , no build/deps. (2) Clickable area = #pool (cursor:pointer, role=button, 'tap the pool' hint). (3) +1/click: sim 5 clicks->count=5, 305 clicks->count=305 (exactly 1 each). (4) Spawn: each click appends a .plankton sprite at click coords (sim: 5 sprites for 5 clicks). (5) Feedback: .ripple ring animation + #pool :active scale per click. (6) No console errors: 'node --check' on extracted script = JS_SYNTAX_OK; IIFE+use strict, no undefined refs in DOM sim, no throws. NOTE: live browser visual confirmation pending — Playwright MCP browser was locked by a parallel run. (7) No external deps: grep for http/src/cdn/fetch/import = NO_EXTERNAL_DEPS.
