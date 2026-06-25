# report — f2-upgrades
contract: f2-upgrades
accepted: yes

Added an upgrades shop to the single-file index.html on top of the f1 click loop. #shop panel lists two upgrades (Auto-Spawner, Click-Multiplier), each showing name/level/next-cost/effect. Auto-Spawner: each level adds +1 plankton/sec via a 1Hz setInterval that increments plankton, refreshes #count, and occasionally spawns a sprite (respecting MAX_SPRITES=220). Click-Multiplier: gather() now does plankton += clickValue() (=1+clickLevel) instead of +=1. Buying deducts costOf(u)=ceil(base*1.15^level), guarded against unaffordable buys, with buttons auto-disabled when plankton<cost. f1 click+spawn+ripple loop preserved; single self-contained file, zero deps.
