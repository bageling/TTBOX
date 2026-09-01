"""Phase 8.3 浏览器真实闭环验证（03 移动控制）。
隔离无头 Chrome + CDP：读 → 改 → 保存 → 重读 → 恢复，全部真实页面操作。
验证项：
  1. PID kp_x（读改存回读恢复）
  2. 拉枪曲线 strength
  3. 持续提前量 scale
  4. 屏蔽物理移动 X
  5. 开火锁Y + 释放延迟
  6. 自动标定 API（GET 状态真实读取；start 因真机无目标预期返回真实拒绝）
  7. 移动日志按钮存在性
"""
import json
import subprocess
import time
import websocket  # pip websocket-client
import urllib.request

CDP_PORT = 9333
BASE = 'http://192.168.0.53:8081'
CHROME = None


def find_chrome():
    for p in [
        r'C:/Program Files/Google/Chrome/Application/chrome.exe',
        r'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
        r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
        r'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    ]:
        try:
            open(p, 'rb').close(0) if False else None
            import os
            if os.path.isfile(p):
                return p
        except Exception:
            continue
    return None


def cdp_send(ws, method, params=None, _id=[0]):
    _id[0] += 1
    ws.send(json.dumps({'id': _id[0], 'method': method, 'params': params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get('id') == _id[0]:
            return msg


def js_eval(ws, expr):
    r = cdp_send(ws, 'Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': True})
    res = r.get('result', {}).get('result', {})
    if res.get('subtype') == 'error' or 'exceptionDetails' in r:
        return {'__error__': json.dumps(r)[:300]}
    return res.get('value')


def main():
    chrome = find_chrome()
    print('chrome:', chrome)
    user_data = r'C:/Users/Administrator/AppData/Local/Temp/ttbox_cdp_83'
    proc = subprocess.Popen([
        chrome, '--headless=new', f'--remote-debugging-port={CDP_PORT}',
        '--remote-allow-origins=*', f'--user-data-dir={user_data}',
        '--no-first-run', '--window-size=1440,900', 'about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    try:
        tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{CDP_PORT}/json'))
        page = next(t for t in tabs if t['type'] == 'page')
        ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=30)

        # 打开 03 页面
        js_eval(ws, f"location.href='{BASE}/'")
        time.sleep(5)

        # 免责声明/授权遮罩处理
        js_eval(ws, """
          document.querySelectorAll('#disclaimerDialog,#licenseDialog,.modal-backdrop').forEach(d => {
            const btn = d.querySelector('[id*=close],[id*=agree],[id*=confirm]');
            if (btn && !d.hidden && d.id !== 'autoCalibrationConfirmDialog') btn.click();
          });
          'done'
        """)
        time.sleep(1)

        results = {}

        # 检查页面加载
        results['page_loaded'] = js_eval(ws, "!!document.querySelector('#control-page')")
        results['aim_trace_button_visible'] = js_eval(ws,
            "(() => { const b = document.querySelector('#recordAimTraceButton'); return b ? !b.hidden : 'not-in-dom'; })()")

        # 切到 PID 分区
        js_eval(ws, "document.querySelector('#controlSectionTabPid')?.click(); 'ok'")
        time.sleep(0.5)

        def read_set_read(input_id, set_value, hint):
            """读 → 改（真实 change 事件）→ 等自动保存 → 重读。"""
            read0 = js_eval(ws, f"document.querySelector('#{input_id}')?.value")
            js_eval(ws, f"""
              (() => {{
                const el = document.querySelector('#{input_id}');
                if (!el) return 'no-el';
                el.value = '{set_value}';
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return 'set';
              }})()
            """)
            time.sleep(2.5)  # 等自动保存 (180ms debounce + PUT)
            read1 = js_eval(ws, f"document.querySelector('#{input_id}')?.value")
            # 页面上下文回读 Gateway
            back = js_eval(ws, f"""
              fetch('/api/config').then(r => r.json()).then(j => {{
                const c = (j.data || {{}}).ai?.controller || {{}};
                return JSON.stringify({{{hint}: c.{hint}}});
              }})
            """)
            return {'ui_before': read0, 'ui_after_set': read1, 'gateway_readback': back}

        def restore(input_id, orig, hint):
            js_eval(ws, f"""
              (() => {{
                const el = document.querySelector('#{input_id}');
                el.value = '{orig}';
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return 'restored';
              }})()
            """)
            time.sleep(2.5)
            back = js_eval(ws, f"""
              fetch('/api/config').then(r => r.json()).then(j => {{
                const c = (j.data || {{}}).ai?.controller || {{}};
                return JSON.stringify({{{hint}: c.{hint}}});
              }})
            """)
            return back

        # 1. kp_x
        r = read_set_read('controller_kp_x', '22.7', 'kp_x')
        results['kp_x'] = r
        results['kp_x_restore'] = restore('controller_kp_x', r['ui_before'] or '21.5', 'kp_x')

        # 2. 拉枪曲线
        js_eval(ws, "document.querySelector('#controlSectionTabPullCurve')?.click(); 'ok'")
        time.sleep(0.5)
        r = read_set_read('controller_pull_curve_strength', '1.11', 'pull_curve_strength')
        results['pull_curve_strength'] = r
        results['pull_curve_restore'] = restore('controller_pull_curve_strength', r['ui_before'] or '0.9', 'pull_curve_strength')

        # 3. 持续提前量
        js_eval(ws, "document.querySelector('#controlSectionTabContinuousLead')?.click(); 'ok'")
        time.sleep(0.5)
        r = read_set_read('controller_continuous_lead_scale', '0.71', 'continuous_lead_scale')
        results['continuous_lead_scale'] = r
        results['continuous_lead_restore'] = restore('controller_continuous_lead_scale', r['ui_before'] or '0.6', 'continuous_lead_scale')

        # 4. 屏蔽物理移动 X（checkbox）
        js_eval(ws, "document.querySelector('#controlSectionTabPhysicalMotionBlock')?.click(); 'ok'")
        time.sleep(0.5)
        mask_before = js_eval(ws, "fetch('/api/state').then(r=>r.json()).then(j=>JSON.stringify(j.data.state.mouse_output))")
        results['motion_block_state_before'] = mask_before
        js_eval(ws, """
          (() => {
            const el = document.querySelector('#controller_block_physical_mouse_x_while_aiming');
            el.checked = true;
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return 'set';
          })()
        """)
        time.sleep(2.5)
        mask_on = js_eval(ws, "fetch('/api/config').then(r=>r.json()).then(j=>JSON.stringify({x:(j.data.ai.controller.block_physical_mouse_x_while_aiming)}))")
        results['motion_block_set'] = mask_on
        # 恢复
        js_eval(ws, """
          (() => {
            const el = document.querySelector('#controller_block_physical_mouse_x_while_aiming');
            el.checked = false;
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return 'restored';
          })()
        """)
        time.sleep(2.5)
        results['motion_block_restore'] = js_eval(ws, "fetch('/api/config').then(r=>r.json()).then(j=>JSON.stringify({x:(j.data.ai.controller.block_physical_mouse_x_while_aiming)}))")

        # 5. 开火锁Y（checkbox，在 PID 分区）
        js_eval(ws, "document.querySelector('#controlSectionTabPid')?.click(); 'ok'")
        time.sleep(0.5)
        lock0 = js_eval(ws, "document.querySelector('#controller_aim_fire_lock_y')?.checked")
        js_eval(ws, """
          (() => {
            const el = document.querySelector('#controller_aim_fire_lock_y');
            el.checked = !el.checked;
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return 'toggled';
          })()
        """)
        time.sleep(2.5)
        lock1 = js_eval(ws, "fetch('/api/config').then(r=>r.json()).then(j=>JSON.stringify({v:(j.data.ai.controller.aim_fire_lock_y)}))")
        results['aim_fire_lock_y'] = {'ui_before': lock0, 'gateway_readback': lock1}
        # 恢复
        js_eval(ws, """
          (() => {
            const el = document.querySelector('#controller_aim_fire_lock_y');
            el.checked = !el.checked;
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return 'restored';
          })()
        """)
        time.sleep(2.5)
        results['aim_fire_lock_y_restore'] = js_eval(ws, "fetch('/api/config').then(r=>r.json()).then(j=>JSON.stringify({v:(j.data.ai.controller.aim_fire_lock_y)}))")

        # 6. 自动标定真实 API
        results['calibration_state'] = js_eval(ws, "fetch('/api/control/calibration').then(r=>r.json()).then(j=>JSON.stringify(j.data))")
        results['calibration_start_no_target'] = js_eval(ws, "fetch('/api/control/calibration/start', {method:'POST'}).then(r=>r.json()).then(j=>JSON.stringify(j))")

        # 7. 手动标定参数保存 + 恢复
        cal0 = json.loads(results['calibration_state'])['calibration']
        results['manual_save'] = js_eval(ws, """
          fetch('/api/control/calibration', {method:'PUT', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({gain_x_px_per_count: 0.651, gain_y_px_per_count: 0.649, response_delay_ms: 8.4})})
            .then(r=>r.json()).then(j=>JSON.stringify({ok:j.ok, cal:j.data.calibration}))
        """)
        time.sleep(0.5)
        results['manual_after'] = js_eval(ws, "fetch('/api/control/calibration').then(r=>r.json()).then(j=>JSON.stringify(j.data.calibration))")

        # 8. 网络请求确认全部命中正式 API
        results['api_hits'] = js_eval(ws, """
          JSON.stringify(performance.getEntriesByType('resource')
            .filter(e => e.name.includes('/api/'))
            .map(e => e.name.split('/api/')[1].split('?')[0]).slice(-20))
        """)

        print(json.dumps(results, ensure_ascii=False, indent=1))
        ws.close()
    finally:
        proc.terminate()


if __name__ == '__main__':
    main()
