# proof — f2-upgrades
contract: f2-upgrades
accepted: yes

Automated headless Chrome (CDP, Node22 WebSocket) check — all 7 criteria PASS, zero console errors. (1) Shop panel renders 2 item buttons each with name+Lv N+effect+live cost [screenshot output/shop.png]. (2) Two distinct types: spawner(perSec) + click(clickGain). (3) Buy deducts: Tide Surge balance 11->1, Plankton Bloom 15->0; unaffordable items report disabled=true (cost 17/11 > balance 2). (4) Geometric scaling live: Tide Surge cost 10->11, Plankton Bloom 15->17 = floor(base*1.15^owned). (5) Passive at ZERO clicks: balance 0->2 over 2.6s at +1/sec (lifetime +2). (6) Click grants multiplier: base gain 1, after buy gain 2. (7) Single self-contained index.html, no build, consoleErrors=[].
