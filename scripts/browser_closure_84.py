"""Phase 8.4 浏览器真实闭环：仅访问板端隔离 TTBOX Gateway 18081。
不使用 YU；通过页面中的 window.ttbox.api 真实请求 TTBOX API。
不碰正式 8081，不停止/重启任何服务。
"""
import json
import os
import subprocess
import time
import urllib.request

import websocket

CDP_PORT = 9337
BASE = "http://192.168.0.53:18083"
CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
USER_DATA = r"C:/Users/Administrator/AppData/Local/Temp/ttbox_cdp_84c"


def send(ws, method, params=None, counter=[0]):
    counter[0] += 1
    ident = counter[0]
    ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == ident:
            return msg


def evaluate(ws, expression):
    result = send(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    })
    if "exceptionDetails" in result:
        raise RuntimeError(json.dumps(result, ensure_ascii=False)[:800])
    value = result.get("result", {}).get("result", {}).get("value")
    return value


def make_sample(mode):
    return {
        "schema": "ttbox.motion-sample.v1",
        "mode": mode,
        "completion": "dwell",
        "canvas": {"width": 640, "height": 480},
        "start": {"x": 10, "y": 10},
        "target": {"x": 100, "y": 100},
        "radius": 20,
        "browser": {"pointer_lock": True, "raw_update": True, "coalesced_events": False},
        "points": [{"dt": 10, "dx": 40, "dy": 40}, {"dt": 10, "dx": 50, "dy": 50}],
    }


def main():
    proc = subprocess.Popen([
        CHROME, "--headless=new", f"--remote-debugging-port={CDP_PORT}",
        "--remote-allow-origins=*", f"--user-data-dir={USER_DATA}",
        "--no-first-run", "--window-size=1440,900", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json"))
        page = next(t for t in tabs if t["type"] == "page")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
        evaluate(ws, f"location.href='{BASE}/'")
        time.sleep(4)
        # 页面真实点击：打开 03 页和训练分区
        evaluate(ws, "document.querySelector('[data-page-target=\\\"control-page\\\"]')?.click(); 'page'")
        time.sleep(1)
        evaluate(ws, "document.querySelector('#controlSectionTabMotionTraining')?.click(); 'training'")
        time.sleep(1)
        result = {}
        result["page_loaded"] = evaluate(ws, "!!document.querySelector('#control-section-motion-training')")
        result["training_controls"] = evaluate(ws, "['motionTrainingStart','motionTrainingTrain','motionTrainingActivate'].every(id => !!document.querySelector('#'+id))")
        # 通过页面加载的统一 API Client 创建档案并读取
        result["client_loaded"] = evaluate(ws, "!!(window.ttbox && window.ttbox.api && window.ttbox.api.request)")
        created = evaluate(ws, "window.ttbox.api.request('POST','/api/motion-profiles',{name:'浏览器验收曲线' }).then(x=>x)")
        result["profile_create"] = created
        profile_id = (created.get("data") or {}).get("id")
        if not profile_id:
            raise RuntimeError(f"profile create failed: {created}")
        started = evaluate(ws, f"window.ttbox.api.request('POST','/api/motion-training/sessions',{{profile_id:'{profile_id}'}}).then(x=>x)")
        result["session_start"] = started
        session_id = (started.get("data") or {}).get("session_id")
        if not session_id:
            raise RuntimeError(f"session start failed: {started}")
        # 真实页面上下文上传 12 个合法样本，覆盖两种模式
        samples_json = json.dumps([make_sample("reaction")] * 6 + [make_sample("continuous")] * 6, ensure_ascii=False)
        upload_expr = f"""
          (async () => {{
            const samples = {samples_json};
            const out = [];
            for (const sample of samples) {{
              out.push(await window.ttbox.api.request('POST','/api/motion-training/sessions/{session_id}/samples', sample));
            }}
            return out;
          }})()
        """
        uploads = evaluate(ws, upload_expr)
        result["sample_upload_count"] = len(uploads)
        result["sample_upload_all_ok"] = all(x.get("ok") for x in uploads)
        stopped = evaluate(ws, f"window.ttbox.api.request('DELETE','/api/motion-training/sessions/{session_id}').then(x=>x)")
        result["session_stop"] = stopped
        trained = evaluate(ws, f"window.ttbox.api.request('POST','/api/motion-profiles/{profile_id}/train').then(x=>x)")
        result["train"] = trained
        activated = evaluate(ws, f"window.ttbox.api.request('POST','/api/motion-profiles/{profile_id}/activate',{{curve_blend:0.8,speed_blend:0.9,reaction_blend:0.7,max_reaction_delay_ms:240}}).then(x=>x)")
        result["activate"] = activated
        refreshed = evaluate(ws, "window.ttbox.api.request('GET','/api/motion-profiles').then(x=>x)")
        result["refresh_active"] = refreshed
        # 页面刷新，确认训练区仍存在并显示样本/模型状态
        evaluate(ws, "location.reload(); 'reload'")
        time.sleep(3)
        evaluate(ws, "document.querySelector('#controlSectionTabMotionTraining')?.click(); 'training'")
        time.sleep(1)
        result["after_reload"] = {
            "training_present": evaluate(ws, "!!document.querySelector('#control-section-motion-training')"),
            "progress": evaluate(ws, "document.querySelector('#motionTrainingProgress')?.textContent"),
            "model_state": evaluate(ws, "document.querySelector('#motionTrainingModelState')?.textContent"),
        }
        deactivated = evaluate(ws, "window.ttbox.api.request('DELETE','/api/motion-profiles/active').then(x=>x)")
        result["deactivate"] = deactivated
        final = evaluate(ws, "window.ttbox.api.request('GET','/api/motion-profiles').then(x=>x)")
        result["final_enabled"] = (final.get("data") or {}).get("enabled")
        # Core 回读：明确记录当前运行二进制是否已认 personal_motion
        core_config = evaluate(ws, "window.ttbox.api.request('GET','/api/config').then(x=>x)")
        result["core_personal_motion_readback"] = (((core_config.get("data") or {}).get("ai") or {}).get("controller") or {}).get("personal_motion_enabled")
        print(json.dumps(result, ensure_ascii=False, indent=1))
        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
