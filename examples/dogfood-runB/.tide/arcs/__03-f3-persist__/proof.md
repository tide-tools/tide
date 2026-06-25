# proof — f3-persist
contract: f3-persist
accepted: yes

node --check on extracted script = SYNTAX_OK. DOM+localStorage stub sim (output/persist-sim.js, re-runnable): A save-then-restore round-trip preserves plankton+auto+click across reload; B offline grant floor(5*1*100)=500 for faked 100s gap + away-note rendered; B2 1000-day gap capped to 8h=28800 (no NaN/absurd); C corrupt JSON -> defaults; D reset(confirm=true) clears key + returns defaults and persists them; E reset(confirm=false) keeps progress. ALL SIM ASSERTIONS PASSED, no console errors. See output/proof.md.
