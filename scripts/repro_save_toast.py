import json, subprocess, time, urllib.request, websocket
PORT=9340
URL='http://192.168.0.53:8081/'
CHROME=r'C:/Program Files/Google/Chrome/Application/chrome.exe'
PROFILE=r'C:/Users/Administrator/AppData/Local/Temp/ttbox84_toast'

def send(ws, method, params=None, state=[0]):
    state[0]+=1; ident=state[0]
    ws.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
    while True:
        msg=json.loads(ws.recv())
        if msg.get('id')==ident:return msg

def ev(ws, expr):
    r=send(ws,'Runtime.evaluate',{'expression':expr,'returnByValue':True,'awaitPromise':True})
    if 'exceptionDetails' in r: return {'error':str(r)[:500]}
    return r.get('result',{}).get('result',{}).get('value')

p=subprocess.Popen([CHROME,'--headless=new',f'--remote-debugging-port={PORT}','--remote-allow-origins=*',f'--user-data-dir={PROFILE}','--no-first-run','--window-size=1440,900',URL],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    time.sleep(5)
    tabs=json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json'))
    page=next(t for t in tabs if t['type']=='page')
    ws=websocket.create_connection(page['webSocketDebuggerUrl'],timeout=30)
    # 安装观测器；记录 fetch 请求和 toast/sync 文本变化
    ev(ws,"""
      (() => {
        window.__toastObs = {requests: [], toasts: [], sync: []};
        const oldFetch = window.fetch;
        window.fetch = function(...args) {
          const url = String(args[0]?.url || args[0] || '');
          const method = args[1]?.method || 'GET';
          if (url.includes('/api/config')) window.__toastObs.requests.push({url, method, t: performance.now()});
          return oldFetch.apply(this, args);
        };
        const toast = document.querySelector('#toast');
        const sync = document.querySelector('#applyIndicator');
        const ob = new MutationObserver(() => {
          if (toast) window.__toastObs.toasts.push({text:toast.textContent, cls:toast.className, t:performance.now()});
          if (sync) window.__toastObs.sync.push({text:sync.textContent, cls:sync.className, t:performance.now()});
        });
        if (toast) ob.observe(toast,{subtree:true,childList:true,attributes:true,characterData:true});
        if (sync) ob.observe(sync,{subtree:true,childList:true,attributes:true,characterData:true});
        return true;
      })()
    """)
    time.sleep(1)
    before=ev(ws,"document.querySelector('#controller_kp_x')?.value")
    # 只派发一次 input + 一次 change，这是浏览器真实事件链
    ev(ws,"""
      (() => {
        const el=document.querySelector('#controller_kp_x');
        if(!el) return 'missing';
        el.value='22.31';
        el.dispatchEvent(new Event('input',{bubbles:true}));
        el.dispatchEvent(new Event('change',{bubbles:true}));
        return 'changed';
      })()
    """)
    time.sleep(3)
    mid=ev(ws,"JSON.stringify(window.__toastObs)")
    # 恢复原值，再记录一次
    ev(ws,f"""
      (() => {{ const el=document.querySelector('#controller_kp_x'); el.value='{before}'; el.dispatchEvent(new Event('input',{{bubbles:true}})); el.dispatchEvent(new Event('change',{{bubbles:true}})); return 'restored'; }})()
    """)
    time.sleep(3)
    final=ev(ws,"JSON.stringify({obs:window.__toastObs, value:document.querySelector('#controller_kp_x')?.value})")
    print(json.dumps({'before':before,'after_change':json.loads(mid),'final':json.loads(final)},ensure_ascii=False,indent=1))
    ws.close()
finally:
    p.terminate()
