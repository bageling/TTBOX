
// ===== 完整 DOM mock（带元素池，模拟真实 DOM 行为）=====
function makeEl(id) {
  const el = {
    id, style: {}, dataset: {}, _children: [],
    classList: { _s: new Set(), add(...c){ c.forEach(x=>this._s.add(x)); }, remove(...c){ c.forEach(x=>this._s.delete(x)); }, toggle(c,f){ f ? this._s.add(c) : (this._s.has(c)?this._s.delete(c):this._s.add(c)); }, contains(c){ return this._s.has(c); } },
    setAttribute(){}, getAttribute(){ return null; },
    addEventListener(){}, removeEventListener(){},
    appendChild(){}, insertBefore(){}, removeChild(){},
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    closest(){ return null; }, getBoundingClientRect(){ return {width:0,height:0,top:0,left:0}; },
    focus(){}, click(){}, blur(){},
    value: '', textContent: '', innerHTML: '', checked: false,
    options: [], files: [], disabled: false, hidden: false,
    min: '', max: '', step: '', type: '', name: '',
    readOnly: false,
  };
  return el;
}
const els = {};
function gEl(id) { if (!els[id]) els[id] = makeEl(id); return els[id]; }
global.document = {
  getElementById: gEl,
  querySelector: (sel) => { const id = String(sel).replace('#',''); return gEl(id); },
  querySelectorAll: () => [],
  createElement: () => makeEl(''),
  addEventListener(){}, removeEventListener(){},
  body: { classList: { _s: new Set(), add(){}, remove(){}, toggle(){} }, dataset: { activePage: 'home-page' } },
  documentElement: { dataset: { uiBrand: 'yu' } },
};
global.window = {
  addEventListener(){},
  location: { href: '', reload(){}, search: '' },
  localStorage: { getItem: () => null, setItem(){}, removeItem(){} },
  confirm: () => true, alert(){},
  navigator: {},
  setTimeout, clearTimeout, setInterval, clearInterval,
  innerWidth: 1280, innerHeight: 800,
};
global.localStorage = global.window.localStorage;

const STATE = JSON.parse(process.env.STATE_JSON || '{}');
const MODELS = JSON.parse(process.env.MODELS_JSON || '{}');
const DISPLAY = JSON.parse(process.env.DISPLAY_JSON || '{}');
const RESPONSES = {
  '/api/state': { ok: true, data: STATE },
  '/api/models': { ok: true, data: MODELS },
  '/api/hardware/display': { ok: true, data: DISPLAY },
};
global.fetch = async (url) => {
  const u = String(url).split('?')[0];
  const body = RESPONSES[u] || { ok: true, data: {} };
  return { ok: true, json: async () => body, status: 200, headers: new Map([['cache-control','no-store']]) };
};
global.FormData = class { append(){} };
global.FileReader = class {};
global.requestAnimationFrame = (cb) => setTimeout(cb, 0);
global.cancelAnimationFrame = () => {};
global.performance = { now: () => Date.now() };

// 捕获 unhandled rejection
process.on('unhandledRejection', (e) => {
  console.error('UNHANDLED REJECTION:', e && e.message ? e.message : e);
  if (e && e.stack) console.error(e.stack.split('\n').slice(0,6).join('\n'));
  process.exit(2);
});

try {
  require('C:/Users/Administrator/Desktop/TTbox0831/web/static/app.js');
  console.log('LOADED');
  setTimeout(() => { console.log('DONE'); process.exit(0); }, 5000);
} catch (e) {
  console.error('RUNTIME ERROR:', e.message);
  if (e.stack) console.error(e.stack.split('\n').slice(0, 10).join('\n'));
  process.exit(1);
}
