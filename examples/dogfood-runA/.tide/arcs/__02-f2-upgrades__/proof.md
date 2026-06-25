# proof — f2-upgrades
contract: f2-upgrades
accepted: yes

Verified deterministically (browser smoke deferred: shared Playwright Chrome profile was locked by a parallel run, MCP exposes no --isolated flag). (1) Shop lists 2 upgrades w/ name+level+cost: #buy-auto + #buy-click render lvl-* and cost-*. (2) Passive/sec: 1000ms interval adds autoRate() plankton, calls setCount(); node sim 20s@L1 -> +20. (3) Click-mult: clickValue()=1+level; after buying click L1 a click yields 2. (4) Affordability: buy() no-ops when plankton<cost, renderShop() sets button.disabled=plankton<cost; sim shows click upgrade unaffordable at 2 plankton. (5) Geometric costs: auto 10,12,14,16,18 / click 15,18,20,23,27 (x1.15). (6) f1 intact + single-file: 11/11 static checks OK (one IIFE/use strict, no external src/href, ripple+spawn path preserved, MAX_SPRITES guard in tick). Evidence in output/notes.md.
