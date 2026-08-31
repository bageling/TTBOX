"""
TTBOX 文件说明

作用：
  Web API 后端服务器。
  处理来自浏览器的 HTTP 请求，通过 IPC 与 C++ 核心通信。

小白理解：
  你在浏览器里点的每个按钮、看的每个数据，
  都是通过这个 Python 程序转发的。
  它把 HTTP 请求翻译成 IPC 消息发给 C++ 核心。

注意：
  本文件说明不改变程序逻辑。
"""
#!/usr/bin/env python3
"""ttbox_gateway.py — TTBOX 生产 Web 网关（板端 0.0.0.0:8081）。

职责：
  1. 静态托管 ttbox-web 构建产物（dist/）
  2. /api/v1/* → TTBOX Core IPC（Unix socket /tmp/ttbox_core.sock）
  3. 与 yu（8080）完全独立，不占用其端口

只做转译，不缓存、不伪造：Core 返回什么就透传什么。
"""
import json
import base64
import os
import re
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

IPC_SOCKET = os.environ.get('TTBOX_IPC_SOCKET', '/tmp/ttbox_core.sock')
LISTEN_HOST = os.environ.get('TTBOX_WEB_HOST', '0.0.0.0')
LISTEN_PORT = int(os.environ.get('TTBOX_WEB_PORT', '8081'))
# 前端构建产物目录（/opt/ttbox/web/dist）
DIST_DIR = Path(os.environ.get('TTBOX_WEB_DIST', '/opt/ttbox/web/dist'))

IPC_STATUS_HTTP = {0: 200, 1: 400, 2: 404, 3: 502, 4: 501}
MIME = {
    '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css',
    '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg',
    '.woff2': 'font/woff2', '.woff': 'font/woff', '.ico': 'image/x-icon', '.json': 'application/json',
}


def ipc_request(req_type, params=None, timeout=5):
    """向 TTBOX Core IPC 发送一条 JSON 行请求，返回解析后的响应。"""
    payload = {'type': req_type}
    if params is not None:
        payload['params'] = params
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(IPC_SOCKET)
        s.sendall(json.dumps(payload).encode() + b'\n')
        buf = b''
        while b'\n' not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        if not buf:
            return {'status': 3, 'error': 'IPC 无响应（Core 未运行?）'}
        return json.loads(buf.decode())
    except (FileNotFoundError, ConnectionRefusedError):
        return {'status': 3, 'error': '无法连接 TTBOX Core IPC（' + IPC_SOCKET + '）'}
    except socket.timeout:
        return {'status': 3, 'error': 'IPC 响应超时'}
    finally:
        s.close()





import subprocess as _sp
import time as _time
_HDMI_CACHE = {'t': 0, 'data': None}

def _read_hdmi():
    """真机 V4L2 读取 HDMI 输入状态（网关本地执行，2s 缓存）。"""
    now = _time.time()
    if _HDMI_CACHE['data'] and now - _HDMI_CACHE['t'] < 2:
        return _HDMI_CACHE['data']
    out = {'connected': False, 'locked': False, 'width': 0, 'height': 0,
           'refresh': 0, 'pixel_format': '', 'device': '/dev/video0',
           'driver': 'rk_hdmirx', 'edid_name': '', 'edid_vendor': ''}
    try:
        r = _sp.run(['v4l2-ctl', '-d', '/dev/video0', '--query-dv-timing'],
                    capture_output=True, text=True, timeout=3)
        txt = r.stdout
        if r.returncode == 0 and 'Active width' in txt:
            w = re.search(r'Active width:\s*(\d+)', txt)
            h = re.search(r'Active height:\s*(\d+)', txt)
            fps = re.search(r'\(([\d.]+) frames per second\)', txt)
            out['connected'] = True
            out['locked'] = True
            if w: out['width'] = int(w.group(1))
            if h: out['height'] = int(h.group(1))
            if fps: out['refresh'] = float(fps.group(1))
        r2 = _sp.run(['v4l2-ctl', '-d', '/dev/video0', '--get-fmt-video'],
                     capture_output=True, text=True, timeout=3)
        m = re.search(r"Pixel Format\s+: '([\w]+)'", r2.stdout)
        if m: out['pixel_format'] = m.group(1)
    except Exception:
        pass
    _HDMI_CACHE['t'] = now
    _HDMI_CACHE['data'] = out
    return out

HOTKEY_BITS = {'left': 1, 'right': 2, 'middle': 4, 'back': 8, 'forward': 16}
BIT_HOTKEYS = {v: k for k, v in HOTKEY_BITS.items()}
CONTROLLER_MAP = {
    'kp_x': 'kp_x', 'kp_y': 'kp_y', 'kd_x': 'kd_x', 'kd_y': 'kd_y',
    'ki_x': 'ki_x', 'ki_y': 'ki_y', 'predict_x': 'predict_x', 'predict_y': 'predict_y',
    'rate_x': 'rate_x', 'rate_y': 'rate_y',
    'smooth_x': 'smooth_x', 'smooth_y': 'smooth_y',
    'output_deadzone': 'output_deadzone',
    'selector_lost_grace_ms': 'lost_grace_ms',
    'aim_reference_offset_x': 'aim_offset_x',
    'aim_reference_offset_y': 'aim_offset_y',
    'y_axis_fire_hotkey': 'y_axis_fire_hotkey',
    'y_axis_fire_release_delay_sec': 'y_axis_fire_release_delay_sec',
    'aim_fire_lock_y': 'aim_fire_lock_y',
}

def _yu_body_to_profile(body):
    ctrl = (body.get('ai') or {}).get('controller') or {}
    mouse = {}
    for yk, tk in CONTROLLER_MAP.items():
        if ctrl.get(yk) is not None:
            mouse[tk] = HOTKEY_BITS.get(ctrl[yk], ctrl[yk]) if yk == 'y_axis_fire_hotkey' else ctrl[yk]
    profiles = body.get('aim_profiles') or []
    p0 = profiles[0] if profiles else {}
    if p0.get('hotkey') is not None: mouse['aim_hotkey'] = HOTKEY_BITS.get(p0['hotkey'], 0)
    if p0.get('hotkey2') is not None: mouse['aim_hotkey2'] = HOTKEY_BITS.get(p0['hotkey2'], 0)
    if p0.get('hotkey_mode') is not None: mouse['aim_hotkey_mode'] = p0['hotkey_mode']
    if p0.get('sensitivity') is not None: mouse['sensitivity'] = p0['sensitivity']
    if body.get('sens') is not None: mouse['sensitivity'] = body['sens']
    inference = {}
    if body.get('video_detection_confidence') is not None: inference['confidence'] = body['video_detection_confidence']
    if body.get('video_detection_iou') is not None: inference['iou'] = body['video_detection_iou']
    if body.get('video_detection_class_filter') is not None:
        inference['class_filter'] = body['video_detection_class_filter']
    if body.get('video_detection_max_detections') is not None:
        inference['max_detections'] = body['video_detection_max_detections']
    capture = {}
    cap = body.get('capture') or {}
    if cap.get('crop_size') is not None:
        capture['width'] = cap['crop_size']; capture['height'] = cap['crop_size']
    if cap.get('crop_offset_x') is not None: capture['offset_x'] = cap['crop_offset_x']
    if cap.get('crop_offset_y') is not None: capture['offset_y'] = cap['crop_offset_y']
    fov = {}
    prev_fov = {}
    # 回读当前 canonical 保留 fov.enabled/shape/center（yu body 只有 radius）
    try:
        _p0 = ipc_request('GET_CONFIG')
        if _p0.get('status') == 0:
            _pf = _p0.get('data', {}).get('runtime_profile')
            if isinstance(_pf, str): _pf = json.loads(_pf)
            prev_fov = (_pf or {}).get('fov') or {}
    except Exception:
        pass
    if prev_fov.get('shape') is not None: fov['shape'] = prev_fov['shape']
    if prev_fov.get('center_x') is not None: fov['center_x'] = prev_fov['center_x']
    if prev_fov.get('center_y') is not None: fov['center_y'] = prev_fov['center_y']
    if body.get('range_factor') is not None:
        fov['radius'] = body['range_factor']
        # 与 bridge 拉齐：range_factor < 1.0 → FOV 启用；=1.0 → 全屏关闭
        fov['enabled'] = body['range_factor'] < 1.0
    elif prev_fov.get('enabled') is not None:
        fov['enabled'] = prev_fov['enabled']
    # latency → preview 帧率（yu preview_interval_ms 语义 = 每帧间隔毫秒）
    preview = {}
    lat = body.get('latency') or {}
    if lat.get('preview_interval_ms') is not None:
        iv = int(lat['preview_interval_ms'])
        if iv > 0:
            preview['fps'] = max(1, min(15, int(1000 / iv)))
    prof = {'mouse': mouse, 'inference': inference, 'capture': capture, 'fov': fov}
    if preview:
        prof['preview'] = preview
    if body.get('model_id') is not None: prof['model_id'] = body['model_id']
    return prof

def _profile_to_yu(prof):
    mouse = prof.get('mouse') or {}
    ctrl = {}
    for yk, tk in CONTROLLER_MAP.items():
        if mouse.get(tk) is not None:
            ctrl[yk] = BIT_HOTKEYS.get(mouse[tk], mouse[tk]) if tk == 'y_axis_fire_hotkey' else mouse[tk]
    _fov_p = (prof.get('fov') or {})
    _prev_p = (prof.get('preview') or {})
    _lat = {}
    if _prev_p.get('fps') not in (None, 0):
        _lat['preview_interval_ms'] = max(1, int(1000 / int(_prev_p.get('fps'))))
    return {
        'model_id': prof.get('model_id', ''),
        'video_detection_confidence': (prof.get('inference') or {}).get('confidence'),
        'video_detection_iou': (prof.get('inference') or {}).get('iou'),
        'capture': {'device': '/dev/video0', 'crop_size': (prof.get('capture') or {}).get('width'),
                    'crop_offset_x': (prof.get('capture') or {}).get('offset_x'),
                    'crop_offset_y': (prof.get('capture') or {}).get('offset_y')},
        # fov.enabled=true 时回读收紧半径；关闭→全屏 1.0（与 bridge 保存侧语义一致）
        'range_factor': _fov_p.get('radius') if _fov_p.get('enabled') else 1.0,
        'sens': mouse.get('sensitivity'),
        'ai': {'controller': ctrl},
        'aim_profiles': [{'hotkey': BIT_HOTKEYS.get(mouse.get('aim_hotkey'), ''),
                          'hotkey2': BIT_HOTKEYS.get(mouse.get('aim_hotkey2'), ''),
                          'hotkey_mode': mouse.get('aim_hotkey_mode', 'any'),
                          'sensitivity': mouse.get('sensitivity'),
                          'offset_x': 0.5, 'offset_y': 0.5, 'class_filter_mask': 0, 'fov_scale': 1.0}],
        'recoil': {}, 'rapid_fire': {}, 'auto_back_flick': {}, 'crosshair': {},
        'hotkey_guard': {'enabled': False, 'toggle_hotkey': 'middle'},
        'mouse_output': {}, 'latency': _lat, 'fan_control': {}, 'loopout_overlay': {},
        'pos': 0.5,
    }

def _collect_yu_state():
    """合成 yu /api/state 的 data 形状：config/state/models/presets 全来自 TTBOX Core。"""
    st = ipc_request('GET_STATUS')
    cf = ipc_request('GET_CONFIG')
    ml = ipc_request('MODEL_LIST')
    status_data = st.get('data', {}) if st.get('status') == 0 else {}
    prof = cf.get('data', {}).get('runtime_profile', {}) if cf.get('status') == 0 else {}
    if isinstance(prof, str):
        try: prof = json.loads(prof)
        except Exception: prof = {}
    models = (ml.get('data', {}) or {}).get('models', []) if ml.get('status') == 0 else []
    m = status_data.get('metrics', {})
    mouse = prof.get('mouse', {})
    inf = prof.get('inference', {})
    cap = prof.get('capture', {})
    fov = prof.get('fov', {})
    running = bool(status_data.get('running')) and bool(status_data.get('runtime_running'))
    return {
        'ok': True,
        'data': {
            'app_version': 'ttbox-' + str(status_data.get('version', '')),
            'version': str(status_data.get('version', '')),
            'config': {
                'ai': {'controller': {
                    'kp_x': mouse.get('kp_x'), 'kp_y': mouse.get('kp_y'),
                    'kd_x': mouse.get('kd_x'), 'kd_y': mouse.get('kd_y'),
                    'ki_x': mouse.get('ki_x'), 'ki_y': mouse.get('ki_y'),
                    'predict_x': mouse.get('predict_x'), 'predict_y': mouse.get('predict_y'),
                    'rate_x': mouse.get('rate_x'), 'rate_y': mouse.get('rate_y'),
                    'smooth_x': mouse.get('smooth_x'), 'smooth_y': mouse.get('smooth_y'),
                    'output_deadzone': mouse.get('output_deadzone'),
                    'selector_lost_grace_ms': mouse.get('lost_grace_ms'),
                    'y_axis_fire_hotkey': mouse.get('y_axis_fire_hotkey'),
                    'y_axis_fire_release_delay_sec': mouse.get('y_axis_fire_release_delay_sec'),
                }},
                'aim_profiles': [],
                'capture': {'crop_size': cap.get('width'), 'crop_offset_x': cap.get('offset_x'), 'crop_offset_y': cap.get('offset_y'), 'device': '/dev/video0'},
                'hotkey_guard': {'enabled': False},
                'model_id': prof.get('model_id', ''),
                'pos': mouse.get('aim_offset_x', 0.5) / 100.0 if mouse.get('aim_offset_x') is not None else 0.5,
                'sens': mouse.get('sensitivity', 1.0),
                'range_factor': (prof.get('fov') or {}).get('radius', 1.0),
                'video_detection_confidence': inf.get('confidence'),
                'video_detection_iou': inf.get('iou'),
            },
            'models': {'models': models},
            'presets': {'presets': []},
            'state': {
                'aim': {'active': False, 'last_error': ''},
                'capture': {'input_width': 0, 'input_height': 0, 'capture_fps': m.get('capture_fps', 0)},
                'core': {'installed': True, 'loaded': True, 'status': 'loaded', 'message': 'TTBOX Core 已加载', 'version': str(status_data.get('version', ''))},
                'detection': {'detections': m.get('detect_count', 0), 'inference_fps': m.get('fps', 0), 'inference_ms': m.get('infer_ms', 0), 'model_loaded': False},
                'latency': {'capture_to_mouse_send_ms': m.get('e2e_ms', 0)},
                'license': {'activated': True, 'valid': True, 'mode': 'ttbox', 'status': 'valid', 'message': ''},
                'mouse_output': {},
                'preview_path': '/api/preview.jpg',
                'running': running,
                'selected_model_id': prof.get('model_id', ''),
                'status': 'running' if running else 'stopped',
            },
            'ui': {'app_title': 'TTBOX 控制台', 'brand_name': 'TTBOX', 'brand_mark': 'TT', 'brand_eyebrow': 'TTBOX', 'brand_title': 'TTBOX 控制台', 'ui_brand': 'yu', 'default_theme': 'dark', 'allow_theme_switch': True},
            'ui_brand': 'yu',
        },
    }

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    # ---------- 静态文件 ----------
    def _serve_static(self, path):
        if path == '/' or not os.path.splitext(path)[1]:
            path = '/index.html'
        f = (DIST_DIR / path.lstrip('/')).resolve()
        try:
            f.relative_to(DIST_DIR.resolve())  # 防目录穿越
        except ValueError:
            self._json(403, {'error': 'forbidden'})
            return
        if not f.is_file():
            self._json(404, {'error': 'not found'})
            return
        body = f.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', MIME.get(f.suffix, 'application/octet-stream'))
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache' if f.suffix == '.html' else 'max-age=3600')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length <= 0 or length > 4 * 1024 * 1024:
            return None
        return json.loads(self.rfile.read(length).decode())

    # ---------- API ----------
    def do_GET(self):
        p = urlparse(self.path).path
        # yu 兼容层：非 /api/v1/ 的 /api/* 端点 = 开发中（保留 UI 的功能）
        if p.startswith('/api/') and not p.startswith('/api/v1/'):
            # yu→v1 已就绪端点转发
            yu_ready_get = {'/api/state': '/api/v1/state', '/api/models': '/api/v1/models',
                            '/api/hardware/display': '/api/v1/hdmi', '/api/config': '/api/v1/config-yu'}
            if p in ('/api/config', '/api/v1/config-yu'):
                r = ipc_request('GET_CONFIG')
                if r.get('status') != 0:
                    self._json(502, {'ok': False, 'error': r.get('error')}); return
                d = r.get('data', {})
                prof = d.get('runtime_profile')
                if isinstance(prof, str): prof = json.loads(prof)
                self._json(200, {'ok': True, 'data': _profile_to_yu(prof)})
                return
            if p == '/api/state':
                self._json(200, _collect_yu_state())
                return
            if p == '/api/models':
                r = ipc_request('MODEL_LIST')
                self._json(200, {'ok': True, 'data': {'models': (r.get('data', {}) or {}).get('models', []), 'ok': True}})
                return
            if p == '/api/hardware/display':
                self._json(200, {'ok': True, 'data': _read_hdmi()})
                return
            if p == '/api/preview.jpg':
                r = ipc_request('GET_PREVIEW', timeout=3)
                if r.get('status') == 0 and r.get('data', {}).get('jpeg_base64'):
                    px = base64.b64decode(r['data']['jpeg_base64'])
                else:
                    px = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(px)))
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(px)
                return
            if p == '/api/preview.mjpg':
                # 真实 MJPEG 流：前端 img.src 只设置一次，依赖流持续推帧。
                # 此前返回单帧 → 浏览器显示一帧后不再刷新（预览卡死）。
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=ttboxframe')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    while True:
                        r = ipc_request('GET_PREVIEW', timeout=2)
                        if r.get('status') == 0 and r.get('data', {}).get('jpeg_base64'):
                            px = base64.b64decode(r['data']['jpeg_base64'])
                            if px:
                                self.wfile.write(b'--ttboxframe\r\n')
                                self.wfile.write(b'Content-Type: image/jpeg\r\n')
                                self.wfile.write(b'Content-Length: ' + str(len(px)).encode() + b'\r\n\r\n')
                                self.wfile.write(px)
                                self.wfile.write(b'\r\n')
                                self.wfile.flush()
                        self.connection.settimeout(1.0)
                except (BrokenPipeError, ConnectionResetError, OSError, socket.timeout):
                    pass  # 浏览器关闭页面 → 正常结束
                return
            self._json(200, {'ok': True, 'data': {'planned': True, 'message': '开发中：TTBOX 后端尚未接入此功能（' + p + '）'}})
            return
        if p.startswith('/api/v1/'):
            if p == '/api/v1/state':
                self._json(200, _collect_yu_state())
            elif p == '/api/v1/config-yu':
                # yu 形状 config（populateForm 需要）
                r = ipc_request('GET_CONFIG')
                if r.get('status') != 0:
                    self._json(502, {'error': r.get('error')}); return
                d = r.get('data', {})
                prof = d.get('runtime_profile')
                if isinstance(prof, str): prof = json.loads(prof)
                self._json(200, {'ok': True, 'data': _profile_to_yu(prof)})
            elif p == '/api/v1/status':
                r = ipc_request('GET_STATUS')
                self._json(IPC_STATUS_HTTP.get(r['status'], 502), r.get('data', r.get('error')))
            elif p == '/api/v1/config':
                r = ipc_request('GET_CONFIG')
                code = IPC_STATUS_HTTP.get(r['status'], 502)
                if code != 200:
                    self._json(code, {'error': r.get('error')})
                    return
                data = r.get('data', {})
                prof = data.get('runtime_profile')
                if isinstance(prof, str):
                    prof = json.loads(prof)
                self._json(200, {'profile': prof, 'flat': {}, 'config_file': data.get('config_file')})
            elif p == '/api/preview.jpg':
                # 单帧快照（轮询模式：前端每次请求拿最新一帧）
                r = ipc_request('GET_PREVIEW', timeout=3)
                if r.get('status') == 0 and r.get('data', {}).get('jpeg_base64'):
                    px = base64.b64decode(r['data']['jpeg_base64'])
                else:
                    px = b''
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(px)))
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(px)
            elif p == '/api/preview.mjpg':
                # 真实 MJPEG 流（multipart/x-mixed-replace）：前端 img.src 只设置一次，
                # 依赖流持续推帧；此前返回单帧导致浏览器显示一帧后不再刷新（预览卡死）。
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=ttboxframe')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'close')
                self.end_headers()
                deadline = _time.time() + 3600  # 客户端断开由写失败自然结束
                try:
                    while _time.time() < deadline:
                        r = ipc_request('GET_PREVIEW', timeout=2)
                        if r.get('status') == 0 and r.get('data', {}).get('jpeg_base64'):
                            px = base64.b64decode(r['data']['jpeg_base64'])
                            if px:
                                self.wfile.write(b'--ttboxframe\r\n')
                                self.wfile.write(b'Content-Type: image/jpeg\r\n')
                                self.wfile.write(b'Content-Length: ' + str(len(px)).encode() + b'\r\n\r\n')
                                self.wfile.write(px)
                                self.wfile.write(b'\r\n')
                                self.wfile.flush()
                        _time.sleep(0.08)  # 约 12fps 上限；帧新则立即下一轮
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass  # 浏览器关闭页面 → 正常结束
            elif p == '/api/v1/models':
                r = ipc_request('MODEL_LIST')
                self._json(IPC_STATUS_HTTP.get(r['status'], 502), r.get('data', r.get('error')))
            elif p == '/api/v1/preview':
                r = ipc_request('GET_PREVIEW', timeout=3)
                code = IPC_STATUS_HTTP.get(r['status'], 502)
                if code != 200:
                    self._json(code, {'error': r.get('error', '暂无预览帧')})
                    return
                import base64 as _b64
                self._json(200, {'jpeg_base64': r['data'].get('jpeg_base64', ''),
                                 'bytes': r['data'].get('bytes', 0)})
            elif p == '/api/v1/hdmi':
                self._json(200, _read_hdmi())
            else:
                # yu 兼容层：未接入的 yu 端点统一返回"开发中"
                self._json(200, {'ok': True, 'data': {'planned': True, 'message': '开发中：TTBOX 后端尚未接入此功能（' + p + '）'}})
            return
        self._serve_static(p)

    def do_PUT(self):
        p = urlparse(self.path).path
        if p == '/api/v1/config':
            body = self._read_json_body()
            if not isinstance(body, dict) or not isinstance(body.get('profile'), dict):
                self._json(400, {'error': '缺少字段 profile（必须是 JSON 对象）'})
                return
            r = ipc_request('SET_CONFIG', {'profile': body['profile']})
            code = IPC_STATUS_HTTP.get(r['status'], 502)
            if code != 200:
                self._json(code, {'error': r.get('error')})
                return
            # 保存成功后强制回读 canonical（UI 永远不领先 Core）
            rr = ipc_request('GET_CONFIG')
            if rr['status'] == 0:
                data = rr.get('data', {})
                prof = data.get('runtime_profile')
                if isinstance(prof, str):
                    prof = json.loads(prof)
                self._json(200, {'profile': prof, 'config_file': data.get('config_file')})
                return
            self._json(200, r.get('data', {}))
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self):
        p = urlparse(self.path).path
        if p == '/api/config':
            # yu 前端保存：body = collectConfig() 扁平结构化 → 转 RuntimeProfile → SET_CONFIG
            body = self._read_json_body()
            if not isinstance(body, dict):
                self._json(400, {'ok': False, 'error': '非法请求体'}); return
            translated = _yu_body_to_profile(body)
            rr0 = ipc_request('GET_CONFIG')
            base = {}
            if rr0.get('status') == 0:
                p0 = rr0.get('data', {}).get('runtime_profile')
                if isinstance(p0, str): p0 = json.loads(p0)
                base = p0 or {}
            prof = dict(base)
            prof.update(translated)
            r = ipc_request('SET_CONFIG', {'profile': prof})
            if r.get('status') != 0:
                self._json(502, {'ok': False, 'error': r.get('error')}); return
            # 回读 canonical → yu 形状
            rr = ipc_request('GET_CONFIG')
            d = rr.get('data', {})
            prof2 = d.get('runtime_profile')
            if isinstance(prof2, str): prof2 = json.loads(prof2)
            self._json(200, {'ok': True, 'data': {'config': _profile_to_yu(prof2), 'state': None}})
            return
        mapping = {'/api/v1/runtime/start': 'start', '/api/v1/runtime/stop': 'stop',
                   '/api/v1/runtime/restart': 'restart'}
        if p in mapping:
            r = ipc_request('RUNTIME_CONTROL', {'action': mapping[p]})
            code = IPC_STATUS_HTTP.get(r['status'], 502)
            if code != 200:
                self._json(code, {'error': r.get('error')})
                return
            self._json(200, r.get('data', {}))
            return
        m = re.match(r'^/api/v1/models/([A-Za-z0-9_-]+)/(validate|install|activate|remove)$', p)
        if m:
            r = ipc_request('MODEL_' + m.group(2).upper(), {'model_id': m.group(1)})
            code = IPC_STATUS_HTTP.get(r['status'], 502)
            if code != 200:
                self._json(code, {'error': r.get('error')})
                return
            self._json(200, r.get('data', {}))
            return
        # POST 兜底：未接入的端点返回开发中
        self._json(200, {'ok': True, 'data': {'planned': True, 'message': '开发中：TTBOX 后端尚未接入此功能（' + urlparse(self.path).path + '）'}})

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f'TTBOX_GATEWAY_READY http://{LISTEN_HOST}:{LISTEN_PORT}  ipc={IPC_SOCKET}  dist={DIST_DIR}', flush=True)
    httpd.serve_forever()
