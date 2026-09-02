import json, subprocess, time, urllib.request, websocket
port=9335
chrome=r'C:/Program Files/Google/Chrome/Application/chrome.exe'
proc=subprocess.Popen([chrome,'--headless=new',f'--remote-debugging-port={port}','--remote-allow-origins=*','--user-data-dir=C:/Users/Administrator/AppData/Local/Temp/ttbox_cdp_84_debug','--no-first-run','--window-size=1440,900','http://192.168.0.53:18081/'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def send(ws,method,params=None,i=[0]):
 i[0]+=1; ident=i[0]; ws.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
 while True:
  m=json.loads(ws.recv())
  if m.get('id')==ident:return m
try:
 time.sleep(5); tabs=json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json')); page=next(t for t in tabs if t['type']=='page')
 ws=websocket.create_connection(page['webSocketDebuggerUrl'],timeout=20)
 for expr in [
  "JSON.stringify({ready:document.readyState,training:!!document.querySelector('#motionTrainingCanvas'),progress:document.querySelector('#motionTrainingProgress')?.textContent,model:document.querySelector('#motionTrainingModelState')?.textContent,script:Array.from(document.scripts).map(s=>s.src).filter(Boolean)})",
  "JSON.stringify(performance.getEntriesByType('resource').map(e=>e.name).filter(x=>x.includes('motion_training')))",
  "JSON.stringify(window.__ttbox_motion_training_error || null)"
 ]:
  r=send(ws,'Runtime.evaluate',{'expression':expr,'returnByValue':True}); print(r.get('result',{}).get('result',{}).get('value'))
 ws.close()
finally: proc.terminate()
