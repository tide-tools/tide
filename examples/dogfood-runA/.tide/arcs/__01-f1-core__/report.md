# report — f1-core
contract: f1-core
accepted: yes

Built the core Tide Pool loop in a single self-contained index.html (inline HTML+CSS+JS, no build, no CDN). Clicking the circular #pool gathers plankton: counter +1 per click, a .plankton DOM sprite spawns at the click point (pop+drift, capped at 220), and a .ripple ring gives feedback. pointerdown for mouse/touch + Enter/Space for keyboard. Verified via node --check (syntax OK) and a headless DOM simulation (5 clicks->5, 305 clicks->305, sprites bounded 220, hint hides). grep confirms zero external deps. Live Playwright browser pass was blocked (shared MCP browser locked by parallel run); recommend an --isolated visual pass at integration.
