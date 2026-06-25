# report — f1-core
contract: f1-core
accepted: yes

Built index.html: self-contained HTML+CSS+JS Tide Pool. Canvas pool (480x320) + HUD plankton counter starting at 0. pointerdown on pool: counter+=1, spawns a visible teal plankton dot at click point (jittered/clamped, bobbing), and a ripple ring as feedback. Single rAF render loop; state in window.TidePool for later arcs. Output pointer + notes in output/, cannon-delta in delta.md.
