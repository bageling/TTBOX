
// ===== 完整 DOM mock =====
function makeEl(id) {
  return {
    id, style: {}, dataset: {}, children: [],
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    setAttribute(){}, getAttribute(){ return null; },
    addEventListener(){}, removeEventListener(){},
    appendChild(){}, insertBefore(){}, removeChild(){},
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    closest(){ return null; }, getBoundingClientRect(){ return {width:0,height:0,top:0,left:0}; },
    focus(){}, click(){}, blur(){},
    value: '', textContent: '', innerHTML: '', checked: false,
    options: [], files: [], disabled: false, hidden: false,
    min: '', max: '', step: '', type: '', name: '',
    readOnly: false, dataset: {},
    set selectionStart(v){}, set selectionEnd(v){},
  };
}
const els = {};
global.document = {
  getElementById: (id) => { if (!els[id]) els[id] = makeEl(id); return els[id]; },
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => makeEl(''),
  addEventListener(){}, removeEventListener(){},
  body: { classList: { add(){}, remove(){}, toggle(){} }, dataset: { activePage: 'home-page' } },
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
global.fetch = async () => ({ ok: true, json: async () => ({ ok: true, data: { models: [], presets: [], state: {}, config: {} } }), status: 200, headers: new Map() });
global.FormData = class { append(){} };
global.FileReader = class {};
global.navigator = {};
global.requestAnimationFrame = (cb) => setTimeout(cb, 0);
global.cancelAnimationFrame = () => {};
global.performance = { now: () => Date.now() };
global.HTMLInputElement = function(){};
global.HTMLElement = function(){};
global.URL = { createObjectURL: () => '', revokeObjectURL(){} };
global.Blob = class {};

try {
  require('C:/Users/Administrator/Desktop/TTbox0831/web/static/app.js');
  console.log('APP LOADED OK');
  setTimeout(() => { console.log('MAIN DONE (10s wait)'); process.exit(0); }, 10000);
} catch (e) {
  console.error('RUNTIME ERROR:', e.message);
  if (e.stack) console.error(e.stack.split('\n').slice(0, 8).join('\n'));
  process.exit(1);
}
