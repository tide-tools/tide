# delta — f2-upgrades
merged: yes

## Cannon delta — the durable truth after f2-upgrades

Tide Pool is now an **idle/clicker with a spendable economy and an upgrades shop**,
still one self-contained `index.html` (HTML+CSS+JS inline, no build, opens in a browser).

### What changed since f1-core
The plankton counter is no longer a raw lifetime total — it is now a **spendable
balance** you grow by clicking and spend in a shop. (A separate in-memory `lifetime`
tracks total ever earned; still no persistence.)

### State & components (additions)
- `balance` — spendable plankton currency; the big HUD number shows this.
- `lifetime` — total plankton ever earned (flavor, never spent).
- `upgrades[]` — buyable items, each `{ id, name, owned, baseCost, effect }`.
- Pricing is geometric: `cost = floor(baseCost * 1.15^owned)`.
- `incomeAcc` — fractional passive-income accumulator drained inside the rAF loop.
- `window.__tidePool` — read-only verification hook (`state()`, `buy()`); no gameplay effect.

### The shop (durable truth)
A glassy teal **TIDE SHOP** panel (top-right; bottom dock on narrow screens) in the
editorial-mono dark house style. It shows the live balance and lists buyable items;
each item shows **name · level (Lv N) · effect text · current cost**. Affordable items
are active and hover-lit; unaffordable items grey out and disable. Costs update live
after every purchase.

### Two upgrades
- **Plankton Bloom** (auto-spawner) — base 15; each level adds **+1 plankton/sec** of
  passive income.
- **Tide Surge** (click-multiplier) — base 10; each level adds **+1 plankton per click**.

### Loop changes
- **Clicking** now grants `1 + clickLevel` plankton (not a flat +1), plus the f1 ripple
  + plankton pop-in feedback.
- **Auto-spawn tick**: owned spawners accrue plankton over time (frame-`dt` driven inside
  the rAF loop, clamped for tab-away gaps). Each whole plankton credits the balance **and**
  drifts a new plankton in from a pool edge, so idle income is visible on-canvas. Runs at
  zero clicks. A `+N/sec` rate readout appears under the HUD once passive income exists.

### Verified
Headless Chrome over CDP: bought each upgrade type and observed cost scale (Tide Surge
10→11, Plankton Bloom 15→17), owned increment, balance deduction, click gain 1→2 after a
multiplier buy, passive balance 0→2 over 2.6s at +1/sec with zero clicks, and unaffordable
items disabled. Zero console errors. Screenshot: `output/shop.png`.
