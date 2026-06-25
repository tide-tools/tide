# report — f3-persist
contract: f3-persist
accepted: yes

Built persistence+offline+reset in single index.html on top of f1/f2. localStorage key tidePool.save.v1 stores balance/lifetime/all upgrade levels/lastSaved (versioned, defensive parse: missing keys->defaults, corrupt JSON->fresh start never throws). Debounced auto-save (800ms) on click/purchase/auto-tick + beforeunload flush. On load grants offline plankton = floor(elapsed * autoSpawnRate) capped 8h, with dismissible cozy 'while you were away' toast. Reset-progress button (shop footer) -> window.confirm -> wipe key+in-memory state+particles, re-render. Wrote output/notes.md + delta.md.
