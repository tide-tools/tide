# report — f3-persist
contract: f3-persist
accepted: yes

Persistence shipped in index.html: versioned localStorage key tidepool-B-v1, saves full economy (plankton, perClick, auto{count,cost}, click{level,cost}) + lastSeen on every change (click/buy/tick/init/reset), throttled 500ms. Restore-before-loop with per-field finite guards (corrupt/missing -> defaults). Offline progress floor(auto.count*AUTO_RATE*elapsed) bounded at 8h, NaN-safe, shown via #away-note. Reset button in #shop with confirm() -> removeItem + defaultEconomy() + immediate re-render. Prior features intact, single file.
