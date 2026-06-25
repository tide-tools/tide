const fs = require('fs');
const path = '/Users/socaseinpoint/Documents/projects/tide/examples/dogfood-runB/index.html';
const html = fs.readFileSync(path,'utf8');
const js = html.match(/<script>([\s\S]*?)<\/script>/)[1];

let store = {};
const localStorage = {
  getItem:k=> k in store ? store[k] : null,
  setItem:(k,v)=>{ store[k]=String(v); },
  removeItem:k=>{ delete store[k]; },
};
let confirmReturn = true;
const ctxStub = new Proxy({}, { get:(t,p)=> (p==='createRadialGradient'||p==='createLinearGradient') ? (()=>({addColorStop(){}})) : (()=>{}) });
const ids = ['pool','count','auto-count','auto-rate','auto-cost','click-level','per-click','click-cost','buy-auto','buy-click','away-note','reset-btn'];

function mkEl(id){
  return { id, textContent:'', disabled:false, _listeners:{},
    classList:{ _s:new Set(), add(c){this._s.add(c);}, remove(c){this._s.delete(c);}, contains(c){return this._s.has(c);} },
    addEventListener(ev,fn){ (this._listeners[ev]=this._listeners[ev]||[]).push(fn); },
    getBoundingClientRect(){ return {left:0,top:0,width:480,height:320}; },
    getContext(){ return ctxStub; }
  };
}

const window = {};
window.confirm = ()=>confirmReturn;
const performance = { now:()=>0 };
const requestAnimationFrame = ()=>0;
const setInterval = ()=>0;
const setTimeout = (fn,ms)=>0;
const runner = new Function('localStorage','document','window','confirm','performance',
  'requestAnimationFrame','setInterval','setTimeout','console', js + '\n;return window;');

// boot a FRESH game instance with its own fresh elements; returns {S, el}
function boot(){
  const el = {}; ids.forEach(i=>el[i]=mkEl(i));
  const document = { getElementById:id=> el[id] || (el[id]=mkEl(id)) };
  const w = runner(localStorage,document,window,window.confirm,performance,requestAnimationFrame,setInterval,setTimeout,console);
  return { S:w.TidePool, el, fire:(id,ev='click')=>el[id]._listeners[ev][el[id]._listeners[ev].length-1]() };
}

const fail=[];
function assert(c,m){ if(!c){ fail.push(m); console.log('FAIL:',m);} else console.log('ok:',m); }

// === TEST A: buy upgrades, save, reload, restore round-trip ===
store={};
let g = boot();
g.S.plankton = 1000;
g.fire('buy-auto'); g.fire('buy-auto'); g.fire('buy-click');
const snap = JSON.parse(JSON.stringify({p:g.S.plankton,ac:g.S.auto.count,acost:g.S.auto.cost,cl:g.S.click.level,pc:g.S.perClick,ccost:g.S.click.cost}));
assert(snap.ac===2,'auto bought to 2');
assert(snap.pc===2,'perClick raised to 2');
assert('tidepool-B-v1' in store, 'save key written on buy');
let g2 = boot();  // reload
assert(g2.S.plankton===snap.p, 'plankton survived reload ('+g2.S.plankton+' vs '+snap.p+')');
assert(g2.S.auto.count===snap.ac && g2.S.auto.cost===snap.acost, 'auto count+cost survived');
assert(g2.S.click.level===snap.cl && g2.S.perClick===snap.pc && g2.S.click.cost===snap.ccost, 'click level+perClick+cost survived');

// === TEST B: offline progress for faked elapsed gap ===
store={};
store['tidepool-B-v1'] = JSON.stringify({v:1,plankton:0,perClick:1,auto:{count:5,cost:50},click:{level:0,cost:25},lastSeen:Date.now()-100*1000});
g = boot();
assert(g.S.plankton>=495 && g.S.plankton<=505, 'offline grant ~500 (5 spawners*100s), got '+g.S.plankton);
assert(g.el['away-note'].textContent.indexOf('while you were away')!==-1, 'away note shown: "'+g.el['away-note'].textContent+'"');

// === TEST B2: offline cap (huge gap doesn't explode/NaN) ===
store={};
store['tidepool-B-v1'] = JSON.stringify({v:1,plankton:0,perClick:1,auto:{count:1,cost:11},click:{level:0,cost:25},lastSeen:Date.now()-1000*24*3600*1000});
g = boot();
assert(g.S.plankton===28800, 'offline capped at 8h => 28800, got '+g.S.plankton);

// === TEST C: corrupt JSON falls back to defaults ===
store={}; store['tidepool-B-v1']='{not valid json';
g = boot();
assert(g.S.plankton===0 && g.S.perClick===1 && g.S.auto.count===0, 'corrupt JSON -> defaults');

// === TEST D: reset clears key + returns defaults ===
store={};
g = boot();
g.S.plankton=999; g.S.auto.count=7; g.S.auto.cost=200; g.S.click.level=3; g.S.perClick=4; g.S.click.cost=80;
confirmReturn=true;
g.fire('reset-btn');
assert(g.S.plankton===0,'reset plankton=0');
assert(g.S.perClick===1,'reset perClick=1');
assert(g.S.auto.count===0 && g.S.auto.cost===10,'reset auto base');
assert(g.S.click.level===0 && g.S.click.cost===25,'reset click base');
const after = JSON.parse(store['tidepool-B-v1']);
assert(after.plankton===0 && after.auto.count===0, 'post-reset save holds defaults');

// === TEST E: reset cancelled (confirm=false) keeps progress ===
store={};
g = boot();
g.S.plankton=555;
confirmReturn=false;
g.fire('reset-btn');
assert(g.S.plankton===555,'cancelled reset keeps progress');

console.log('\n'+(fail.length? ('SIM FAILED: '+fail.length+' assertion(s)') : 'ALL SIM ASSERTIONS PASSED'));
process.exit(fail.length?1:0);
