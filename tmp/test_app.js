
// Minimal browser stubs
const noop = () => {};
global.document = {
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: noop,
  createElement: () => ({ style: {}, classList: { add: noop, remove: noop, toggle: noop }, setAttribute: noop, appendChild: noop }),
  body: { classList: { add: noop, remove: noop, toggle: noop }, dataset: {} },
  documentElement: { dataset: {} },
};
global.window = { addEventListener: noop, location: { href: '', reload: noop }, localStorage: { getItem: () => null, setItem: noop }, confirm: () => true, navigator: {} };
global.fetch = () => Promise.reject(new Error('no network'));
global.localStorage = { getItem: () => null, setItem: noop };
global.alert = noop;
global.requestAnimationFrame = noop;
try {
  require('C:/Users/Administrator/Desktop/TTbox0831/web/static/app.js');
  console.log('LOADED OK');
} catch(e) {
  console.error('RUNTIME ERROR:', e.message);
  console.error(e.stack ? e.stack.split('\n').slice(0,5).join('\n') : '');
}
