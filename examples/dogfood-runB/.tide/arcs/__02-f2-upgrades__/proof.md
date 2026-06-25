# proof — f2-upgrades
contract: f2-upgrades
accepted: yes

node --check on extracted inline JS = SYNTAX_OK. DOM-stub sim (sim_runB_f2.js, vm+stubbed document/canvas/setInterval): window.TidePool exposed; buy blocked when broke; auto buy deducts 100->90, click buy 90->65; costs scale 10->11 and 25->28 (~1.15x); 3 passive ticks raise counter 65->68 with ZERO clicks and spawn dots 0->3; manual click adds perClick=2 (68->70) +1 dot. ALL SIM ASSERTIONS PASSED, no console errors/exceptions.
