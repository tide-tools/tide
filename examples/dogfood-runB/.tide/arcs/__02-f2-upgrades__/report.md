# report — f2-upgrades
contract: f2-upgrades
accepted: yes

Added upgrades shop to index.html on window.TidePool store: auto-spawner (passive plankton/sec, spawns dots, scaling cost from 10) + click-multiplier (raises per-click, scaling cost from 25), both ~1.15x/level. Shop UI panel shows live count/level+cost; buy buttons deduct plankton and disable when unaffordable. setInterval(1s) passiveTick adds auto.count*RATE plankton with no clicking. Wrote output/notes.md + result.txt + delta.md.
