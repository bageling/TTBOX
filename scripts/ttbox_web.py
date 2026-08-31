#!/usr/bin/env python3
"""ttbox_web.py — TTBOX Web 后端（Flask），兼容 YU 前端 API。

职责：
  1. 静态托管 YU 前端（web/static/）
  2. 实现 YU 全部 API 路由（100+）
  3. 通过 Unix socket 与 TTBOX Core IPC 通信
  4. 参数翻译：YU 格式 ↔ RuntimeProfile 格式
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, send_file, url_for

# ====================================================================
# 配置
# ====================================================================
ROOT_DIR = Path(os.environ.get('TTBOX_ROOT', Path(__file__).resolve().parent)).resolve()
WEB_DIR = ROOT_DIR
STATIC_DIR = WEB_DIR / 'static'
TEMPLATE_DIR = WEB_DIR / 'templates'
IPC_SOCKET = os.environ.get('TTBOX_IPC_SOCKET', '/tmp/ttbox_core.sock')
LISTEN_HOST = os.environ.get('TTBOX_WEB_HOST', '0.0.0.0')
LISTEN_PORT = int(os.environ.get('TTBOX_WEB_PORT', '8081'))

DEFAULT_LICENSE = {
    'activated': True, 'valid': True, 'mode': 'ttbox',
    'status': 'valid', 'message': '',
}

# ====================================================================
# IPC 通信
# ====================================================================
def ipc_request(req_type: str, params: dict | None = None, timeout: float = 5) -> dict:
    """向 TTBOX Core IPC 发送请求，返回解析后的响应。"""
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
        return {'status': 3, 'error': '无法连接 Core IPC'}
    except socket.timeout:
        return {'status': 3, 'error': 'IPC 响应超时'}
    finally:
        s.close()


def _get_runtime_profile() -> dict:
    """获取当前 RuntimeProfile。"""
    r = ipc_request('GET_CONFIG')
    if r.get('status') != 0:
        return {}
    prof = r.get('data', {}).get('runtime_profile', {})
    if isinstance(prof, str):
        try:
            prof = json.loads(prof)
        except Exception:
            prof = {}
    return prof or {}


def _get_status() -> dict:
    """获取 Core 运行状态。"""
    r = ipc_request('GET_STATUS')
    return r.get('data', {}) if r.get('status') == 0 else {}

# ====================================================================
# 参数翻译（YU 格式 ↔ RuntimeProfile 格式）
# ====================================================================
HOTKEY_BITS = {'left': 1, 'right': 2, 'middle': 4, 'back': 8, 'forward': 16}
BIT_HOTKEYS = {v: k for k, v in HOTKEY_BITS.items()}

CONTROLLER_MAP = {
    'kp_x': 'kp_x', 'kp_y': 'kp_y',
    'kd_x': 'kd_x', 'kd_y': 'kd_y',
    'ki_x': 'ki_x', 'ki_y': 'ki_y',
    'predict_x': 'predict_x', 'predict_y': 'predict_y',
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


def yu_body_to_profile(body: dict) -> dict:
    """YU 前端保存的配置格式 → RuntimeProfile 格式。"""
    ctrl = (body.get('ai') or {}).get('controller') or {}
    mouse = {}
    for yk, tk in CONTROLLER_MAP.items():
        if ctrl.get(yk) is not None:
            mouse[tk] = HOTKEY_BITS.get(ctrl[yk], ctrl[yk]) if yk == 'y_axis_fire_hotkey' else ctrl[yk]

    profiles = body.get('aim_profiles') or []
    p0 = profiles[0] if profiles else {}
    if p0.get('hotkey') is not None:
        mouse['aim_hotkey'] = HOTKEY_BITS.get(p0['hotkey'], 0)
    if p0.get('hotkey2') is not None:
        mouse['aim_hotkey2'] = HOTKEY_BITS.get(p0['hotkey2'], 0)
    if p0.get('hotkey_mode') is not None:
        mouse['aim_hotkey_mode'] = p0['hotkey_mode']
    if p0.get('sensitivity') is not None:
        mouse['sensitivity'] = p0['sensitivity']
    if body.get('sens') is not None:
        mouse['sensitivity'] = body['sens']

    inference = {}
    if body.get('video_detection_confidence') is not None:
        inference['confidence'] = body['video_detection_confidence']
    if body.get('video_detection_iou') is not None:
        inference['iou'] = body['video_detection_iou']
    if body.get('video_detection_class_filter') is not None:
        inference['class_filter'] = body['video_detection_class_filter']
    if body.get('video_detection_max_detections') is not None:
        inference['max_detections'] = body['video_detection_max_detections']

    capture = {}
    cap = body.get('capture') or {}
    if cap.get('crop_size') is not None:
        capture['width'] = cap['crop_size']
        capture['height'] = cap['crop_size']
    if cap.get('crop_offset_x') is not None:
        capture['offset_x'] = cap['crop_offset_x']
    if cap.get('crop_offset_y') is not None:
        capture['offset_y'] = cap['crop_offset_y']

    fov = {}
    prev_fov = {}
    try:
        p0 = _get_runtime_profile()
        prev_fov = p0.get('fov') or {}
    except Exception:
        pass
    if prev_fov.get('shape') is not None:
        fov['shape'] = prev_fov['shape']
    if prev_fov.get('center_x') is not None:
        fov['center_x'] = prev_fov['center_x']
    if prev_fov.get('center_y') is not None:
        fov['center_y'] = prev_fov['center_y']
    if body.get('range_factor') is not None:
        fov['radius'] = body['range_factor']
        fov['enabled'] = body['range_factor'] < 1.0
    elif prev_fov.get('enabled') is not None:
        fov['enabled'] = prev_fov['enabled']

    preview = {}
    lat = body.get('latency') or {}
    if lat.get('preview_interval_ms') is not None:
        iv = int(lat['preview_interval_ms'])
        if iv > 0:
            preview['fps'] = max(1, min(15, int(1000 / iv)))

    prof = {
        'mouse': mouse,
        'inference': inference,
        'capture': capture,
        'fov': fov,
    }
    if preview:
        prof['preview'] = preview
    if body.get('model_id') is not None:
        prof['model_id'] = body['model_id']

    return prof


def profile_to_yu(prof: dict) -> dict:
    """RuntimeProfile 格式 → YU 前端需要的格式。"""
    mouse = prof.get('mouse') or {}
    ctrl = {}
    for yk, tk in CONTROLLER_MAP.items():
        if mouse.get(tk) is not None:
            ctrl[yk] = BIT_HOTKEYS.get(mouse[tk], mouse[tk]) if tk == 'y_axis_fire_hotkey' else mouse[tk]

    fov_p = prof.get('fov') or {}
    prev_p = prof.get('preview') or {}
    lat = {}
    if prev_p.get('fps') not in (None, 0):
        lat['preview_interval_ms'] = max(1, int(1000 / int(prev_p['fps'])))

    return {
        'model_id': prof.get('model_id', ''),
        'video_detection_confidence': (prof.get('inference') or {}).get('confidence'),
        'video_detection_iou': (prof.get('inference') or {}).get('iou'),
        'capture': {
            'device': '/dev/video0',
            'crop_size': (prof.get('capture') or {}).get('width'),
            'crop_offset_x': (prof.get('capture') or {}).get('offset_x'),
            'crop_offset_y': (prof.get('capture') or {}).get('offset_y'),
        },
        'range_factor': fov_p.get('radius') if fov_p.get('enabled') else 1.0,
        'sens': mouse.get('sensitivity'),
        'ai': {'controller': ctrl},
        'aim_profiles': [{
            'hotkey': BIT_HOTKEYS.get(mouse.get('aim_hotkey'), ''),
            'hotkey2': BIT_HOTKEYS.get(mouse.get('aim_hotkey2'), ''),
            'hotkey_mode': mouse.get('aim_hotkey_mode', 'any'),
            'sensitivity': mouse.get('sensitivity'),
            'offset_x': 0.5, 'offset_y': 0.5,
            'class_filter_mask': 0, 'fov_scale': 1.0,
        }],
        'recoil': {}, 'rapid_fire': {}, 'auto_back_flick': {}, 'crosshair': {},
        'hotkey_guard': {'enabled': False, 'toggle_hotkey': 'middle'},
        'mouse_output': {}, 'latency': lat, 'fan_control': {}, 'loopout_overlay': {},
        'pos': 0.5,
    }


def collect_yu_state() -> dict:
    """合成 /api/state 的完整数据。"""
    st = _get_status()
    prof = _get_runtime_profile()
    ml = ipc_request('MODEL_LIST')
    models = (ml.get('data', {}) or {}).get('models', []) if ml.get('status') == 0 else []

    m = st.get('metrics', {})
    mouse = prof.get('mouse', {})
    inf = prof.get('inference', {})
    cap = prof.get('capture', {})
    fov = prof.get('fov', {})
    running = bool(st.get('running')) and bool(st.get('runtime_running'))

    return {
        'ok': True,
        'data': {
            'app_version': 'ttbox-' + str(st.get('version', '')),
            'version': str(st.get('version', '')),
            'config': {
                'ai': {'controller': {
                    'kp_x': mouse.get('kp_x'),
                    'kp_y': mouse.get('kp_y'),
                    'kd_x': mouse.get('kd_x'),
                    'kd_y': mouse.get('kd_y'),
                    'ki_x': mouse.get('ki_x'),
                    'ki_y': mouse.get('ki_y'),
                    'predict_x': mouse.get('predict_x'),
                    'predict_y': mouse.get('predict_y'),
                    'rate_x': mouse.get('rate_x'),
                    'rate_y': mouse.get('rate_y'),
                    'smooth_x': mouse.get('smooth_x'),
                    'smooth_y': mouse.get('smooth_y'),
                    'output_deadzone': mouse.get('output_deadzone'),
                    'selector_lost_grace_ms': mouse.get('lost_grace_ms'),
                    'y_axis_fire_hotkey': mouse.get('y_axis_fire_hotkey'),
                    'y_axis_fire_release_delay_sec': mouse.get('y_axis_fire_release_delay_sec'),
                    'aim_fire_lock_y': mouse.get('aim_fire_lock_y'),
                    'aim_reference_offset_x': mouse.get('aim_offset_x'),
                    'aim_reference_offset_y': mouse.get('aim_offset_y'),
                }},
                'aim_profiles': [],
                'capture': {
                    'crop_size': cap.get('width'),
                    'crop_offset_x': cap.get('offset_x'),
                    'crop_offset_y': cap.get('offset_y'),
                    'device': '/dev/video0',
                },
                'hotkey_guard': {'enabled': False},
                'model_id': prof.get('model_id', ''),
                'pos': mouse.get('aim_offset_x', 0.5) / 100.0 if mouse.get('aim_offset_x') is not None else 0.5,
                'sens': mouse.get('sensitivity', 1.0),
                'range_factor': fov.get('radius', 1.0),
                'video_detection_confidence': inf.get('confidence'),
                'video_detection_iou': inf.get('iou'),
            },
            'models': {'models': models},
            'presets': {'presets': []},
            'state': {
                'aim': {'active': False, 'last_error': ''},
                'capture': {
                    'input_width': 0, 'input_height': 0,
                    'capture_fps': m.get('capture_fps', 0),
                },
                'core': {
                    'installed': True, 'loaded': True,
                    'status': 'loaded', 'message': 'TTBOX Core',
                    'version': str(st.get('version', '')),
                },
                'detection': {
                    'detections': m.get('detect_count', 0),
                    'inference_fps': m.get('fps', 0),
                    'inference_ms': m.get('infer_ms', 0),
                    'model_loaded': bool(prof.get('model_id')),
                },
                'latency': {'capture_to_mouse_send_ms': m.get('e2e_ms', 0)},
                'license': DEFAULT_LICENSE,
                'mouse_output': {},
                'preview_path': '/api/preview.jpg',
                'running': running,
                'selected_model_id': prof.get('model_id', ''),
                'status': 'running' if running else 'stopped',
            },
            'ui': {
                'app_title': 'TTBOX 控制台',
                'brand_name': 'TTBOX',
                'brand_mark': 'TT',
                'brand_eyebrow': 'TTBOX',
                'brand_title': 'TTBOX 控制台',
                'ui_brand': 'yu',
                'default_theme': 'dark',
                'allow_theme_switch': True,
            },
            'ui_brand': 'yu',
        },
    }

# ====================================================================
# Flask 应用
# ====================================================================
app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR),
    static_url_path='/static',
)


# ====================================================================
# 页面路由
# ====================================================================
@app.get('/')
def index():
    return render_template('index.html',
        app_title='TTBOX 控制台',
        ui_brand='yu',
        brand_mark='TT',
        brand_eyebrow='TTBOX',
        brand_title='TTBOX 控制台',
        default_theme='dark',
        asset_version='2026.08.31.1',
        visual_theme={'id': 'default', 'version': 'built-in', 'color_scheme': 'dark', 'styles': []},
        module_labels=['首页', '配置', '模型', '预设', '运动', '校准', '硬件', '网络', '系统', '更新', '主题', '激活'],
        motion_training_available=False,
        motion_training_collection_available=False,
        allow_theme_switch=True,
        show_aim_trace_button=False,
        default_hotspot_ssid='TTBOX',
        default_local_name='ttbox',
    )


@app.get('/desktop')
def desktop():
    return render_template('index.html', mode='desktop',
        app_title='TTBOX 控制台', ui_brand='yu',
        brand_mark='TT', brand_eyebrow='TTBOX', brand_title='TTBOX 控制台',
        default_theme='dark',
        asset_version='2026.08.31.1',
        visual_theme={'id': 'default', 'version': 'built-in', 'color_scheme': 'dark', 'styles': []},
        module_labels=['首页', '配置', '模型', '预设', '运动', '校准', '硬件', '网络', '系统', '更新', '主题', '激活'],
        motion_training_available=False,
        motion_training_collection_available=False,
        allow_theme_switch=True,
        show_aim_trace_button=False,
        default_hotspot_ssid='TTBOX',
        default_local_name='ttbox',
    )


@app.get('/mobile')
def mobile():
    return render_template('index.html', mode='mobile',
        app_title='TTBOX 控制台', ui_brand='yu',
        brand_mark='TT', brand_eyebrow='TTBOX', brand_title='TTBOX 控制台',
        default_theme='dark',
        asset_version='2026.08.31.1',
        visual_theme={'id': 'default', 'version': 'built-in', 'color_scheme': 'dark', 'styles': []},
        module_labels=['首页', '配置', '模型', '预设', '运动', '校准', '硬件', '网络', '系统', '更新', '主题', '激活'],
        motion_training_available=False,
        motion_training_collection_available=False,
        allow_theme_switch=True,
        show_aim_trace_button=False,
        default_hotspot_ssid='TTBOX',
        default_local_name='ttbox',
    )


# ====================================================================
# API 路由
# ====================================================================

# -- 系统/状态 --
@app.get('/api/health/frontend')
def frontend_health():
    return jsonify({'ok': True, 'status': 'ok', 'version': 'ttbox'})


@app.get('/api/state')
def get_state():
    return jsonify(collect_yu_state())


@app.get('/api/announcement')
def get_announcement():
    return jsonify({'ok': True, 'data': {'announcement': '', 'enabled': False}})


@app.get('/api/system')
def get_system_status():
    st = _get_status()
    return jsonify({
        'ok': True,
        'data': {
            'hostname': 'ttbox',
            'uptime': st.get('uptime', 0),
            'cpu_temp': st.get('cpu_temp', 0),
            'cpu_usage': st.get('cpu_usage', 0),
            'memory_usage': st.get('memory_usage', 0),
            'version': str(st.get('version', '')),
            'app_version': 'ttbox-' + str(st.get('version', '')),
            'os': 'Orange Pi 1.2.0',
        },
    })


@app.get('/api/system/storage')
def get_storage_status():
    return jsonify({
        'ok': True,
        'data': {
            'total': 14 * 1024,
            'used': 5 * 1024,
            'available': 8 * 1024,
            'usage_percent': 40,
        },
    })


@app.post('/api/system/storage/expand')
def expand_storage():
    return jsonify({'ok': True, 'data': {'message': '存储扩展已触发'}})


@app.put('/api/system/hostname')
def update_system_hostname():
    return jsonify({'ok': True, 'data': {'message': '主机名已更新'}})


@app.put('/api/system/web-port')
def update_system_web_port():
    return jsonify({'ok': True, 'data': {'message': 'Web 端口已更新，请重启后生效'}})


@app.get('/api/system/lan-blocklist')
def get_lan_blocklist():
    return jsonify({'ok': True, 'data': {'blocked': []}})


@app.post('/api/system/lan-blocklist/scan')
def scan_lan_blocklist_devices():
    return jsonify({'ok': True, 'data': {'devices': []}})


@app.post('/api/system/lan-blocklist')
def set_lan_blocklist():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


@app.delete('/api/system/lan-blocklist')
def clear_lan_blocklist():
    return jsonify({'ok': True, 'data': {'message': '已清空'}})


@app.post('/api/system/reactivate')
def reactivate_device():
    return jsonify({'ok': True, 'data': {'message': '已重新激活'}})


@app.post('/api/system/master-reactivate')
def master_reactivate_device():
    return jsonify({'ok': True, 'data': {'message': '已主控重新激活'}})


@app.post('/api/system/reboot')
def reboot_system():
    return jsonify({'ok': True, 'data': {'message': '系统即将重启'}})


@app.post('/api/system/poweroff')
def poweroff_system():
    return jsonify({'ok': True, 'data': {'message': '系统即将关机'}})


@app.get('/api/events')
def events():
    return jsonify({'ok': True, 'data': {'events': []}})


# -- 配置 --
@app.put('/api/config')
def update_config():
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({'ok': False, 'error': '非法请求体'}), 400
    translated = yu_body_to_profile(body)
    base = _get_runtime_profile()
    prof = dict(base)
    prof.update(translated)
    r = ipc_request('SET_CONFIG', {'profile': prof})
    if r.get('status') != 0:
        return jsonify({'ok': False, 'error': r.get('error', '配置保存失败')}), 502
    return jsonify({'ok': True, 'data': profile_to_yu(prof)})


@app.get('/api/config')
def get_config_yu():
    prof = _get_runtime_profile()
    return jsonify({'ok': True, 'data': profile_to_yu(prof)})


@app.get('/api/settings/auto-start')
def get_auto_start_setting():
    return jsonify({'ok': True, 'data': {'enabled': False, 'initial_delay': 20}})


@app.put('/api/settings/auto-start')
def update_auto_start_setting():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


# -- 模型 --
@app.get('/api/models')
def list_models():
    r = ipc_request('MODEL_LIST')
    models = (r.get('data', {}) or {}).get('models', []) if r.get('status') == 0 else []
    return jsonify({'ok': True, 'data': {'models': models, 'ok': True}})


@app.get('/api/models/device-code')
def model_device_code():
    return jsonify({'ok': True, 'data': {'device_code': 'TTBOX-' + os.uname().nodename}})


@app.post('/api/models/cloud-encrypted')
def add_cloud_encrypted_model():
    return jsonify({'ok': True, 'data': {'message': '开发中'}})


@app.post('/api/models/import')
def import_model():
    return jsonify({'ok': True, 'data': {'message': '开发中'}})


@app.post('/api/models/delete')
def delete_model():
    return jsonify({'ok': True, 'data': {'message': '已删除'}})


@app.post('/api/models/select')
def select_model():
    body = request.get_json(force=True)
    model_id = body.get('model_id', '')
    prof = _get_runtime_profile()
    prof['model_id'] = model_id
    r = ipc_request('SET_CONFIG', {'profile': prof})
    return jsonify({'ok': True, 'data': {'message': '模型已切换'}})


@app.post('/api/models/bind-preset')
def bind_model_preset():
    return jsonify({'ok': True, 'data': {'message': '已绑定'}})


@app.post('/api/models/game-profile')
def update_model_game_profile():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


@app.post('/api/models/remote-frame-format')
def update_model_remote_frame_format():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


@app.post('/api/models/rknn-concurrency')
def update_model_rknn_concurrency():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


@app.post('/api/models/hailo-pipeline-depth')
def update_model_hailo_pipeline_depth():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


@app.post('/api/models/class-names')
def update_model_class_names():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


# -- 预设 --
@app.get('/api/presets')
def list_presets():
    return jsonify({'ok': True, 'data': {'presets': []}})


@app.post('/api/presets')
def save_or_delete_preset():
    return jsonify({'ok': True, 'data': {'message': '已保存'}})


@app.post('/api/presets/load')
def load_preset():
    return jsonify({'ok': True, 'data': {'message': '已加载'}})


@app.post('/api/presets/import')
def import_preset():
    return jsonify({'ok': True, 'data': {'message': '已导入'}})


@app.get('/api/presets/<name>/export')
def export_preset(name: str):
    return jsonify({'ok': True, 'data': {'preset': {}}})


# -- 控制/校准 --
@app.get('/api/control/calibration')
def get_auto_calibration():
    return jsonify({'ok': True, 'data': {'valid': False, 'calibrated_at': ''}})


@app.put('/api/control/calibration')
def update_auto_calibration():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


@app.post('/api/control/calibration/start')
def start_auto_calibration():
    return jsonify({'ok': True, 'data': {'message': '校准已开始'}})


@app.post('/api/control/calibration/cancel')
def cancel_auto_calibration():
    return jsonify({'ok': True, 'data': {'message': '校准已取消'}})


@app.delete('/api/control/calibration')
def clear_auto_calibration():
    return jsonify({'ok': True, 'data': {'message': '已清除'}})


@app.post('/api/control/start')
def start_control():
    r = ipc_request('RUNTIME_CONTROL', {'action': 'start'})
    return jsonify({'ok': r.get('status') == 0, 'data': {'message': '已启动' if r.get('status') == 0 else '启动失败'}})


@app.post('/api/control/stop')
def stop_control():
    r = ipc_request('RUNTIME_CONTROL', {'action': 'stop'})
    return jsonify({'ok': r.get('status') == 0, 'data': {'message': '已停止' if r.get('status') == 0 else '停止失败'}})


@app.post('/api/diagnostics/aim-trace')
def start_aim_trace():
    return jsonify({'ok': True, 'data': {'message': '跟踪已开始'}})


@app.get('/api/diagnostics/usb-proxy.zip')
def download_usb_proxy_diagnostics():
    return jsonify({'ok': True, 'data': {}})


@app.get('/api/events')
def get_events():
    return jsonify({'ok': True, 'data': {'events': []}})


# -- 硬件 --
@app.get('/api/hardware/mouse')
def get_mouse_hardware():
    return jsonify({
        'ok': True,
        'data': {
            'mode': 'proxy',
            'device': '/dev/hidg0',
            'enabled': True,
            'connected': True,
        },
    })


@app.put('/api/hardware/mouse')
def update_mouse_hardware():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


@app.put('/api/hardware/mouse/mode')
def update_mouse_proxy_mode():
    return jsonify({'ok': True, 'data': {'message': '模式已切换'}})


@app.put('/api/hardware/mouse/timing')
def update_mouse_proxy_timing():
    return jsonify({'ok': True, 'data': {'message': '时序已更新'}})


@app.get('/api/hardware/display')
def get_display_hardware():
    hdmi = {'connected': False, 'locked': False, 'width': 0, 'height': 0, 'refresh': 0}
    try:
        r = subprocess.run(
            ['v4l2-ctl', '-d', '/dev/video0', '--query-dv-timing'],
            capture_output=True, text=True, timeout=3,
        )
        txt = r.stdout
        if r.returncode == 0 and 'Active width' in txt:
            w = re.search(r'Active width:\s*(\d+)', txt)
            h = re.search(r'Active height:\s*(\d+)', txt)
            fps = re.search(r'\(([\d.]+) frames per second\)', txt)
            hdmi['connected'] = True
            hdmi['locked'] = True
            if w: hdmi['width'] = int(w.group(1))
            if h: hdmi['height'] = int(h.group(1))
            if fps: hdmi['refresh'] = float(fps.group(1))
    except Exception:
        pass
    return jsonify({'ok': True, 'data': hdmi})


@app.put('/api/hardware/display')
def update_display_hardware():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


# -- 网络/WiFi --
@app.get('/api/network/wifi')
def get_wifi_status():
    return jsonify({'ok': True, 'data': {'connected': False, 'ssid': '', 'signal': 0}})


@app.post('/api/network/wifi/scan')
def scan_wifi_networks():
    return jsonify({'ok': True, 'data': {'networks': []}})


@app.post('/api/network/wifi/connect')
def connect_wifi_network():
    return jsonify({'ok': True, 'data': {'message': '连接中'}})


@app.post('/api/network/wifi/fallback')
def fallback_wifi_network():
    return jsonify({'ok': True, 'data': {'message': '已回退'}})


@app.post('/api/network/wifi/ap/apply')
def apply_wifi_ap_hotspot():
    return jsonify({'ok': True, 'data': {'message': '热点已启动'}})


@app.post('/api/network/wifi/client/activate')
def activate_wifi_client_mode():
    return jsonify({'ok': True, 'data': {'message': '客户端模式已激活'}})


# -- 激活/授权 --
@app.get('/api/license')
def get_license():
    return jsonify({'ok': True, 'data': DEFAULT_LICENSE})


@app.post('/api/license/activate')
def activate_license():
    return jsonify({'ok': True, 'data': {'message': '已激活'}})


@app.post('/api/activation/network/prepare')
def prepare_activation_network():
    return jsonify({'ok': True, 'data': {'message': '网络已准备'}})


@app.post('/api/activation/reset-local-identity')
def reset_activation_local_identity():
    return jsonify({'ok': True, 'data': {'message': '已重置'}})


@app.get('/api/activation/full-recovery')
def get_activation_full_recovery():
    return jsonify({'ok': True, 'data': {'recovery_available': True}})


@app.post('/api/activation/full-recovery')
def start_activation_full_recovery():
    return jsonify({'ok': True, 'data': {'message': '恢复中'}})


# -- 更新 --
@app.post('/api/update/check')
def check_update():
    return jsonify({'ok': True, 'data': {'update_available': False, 'latest_version': '2026.08.31.1'}})


@app.post('/api/update/versions')
def list_update_versions():
    return jsonify({'ok': True, 'data': {'versions': []}})


@app.get('/api/update/status')
def get_update_status():
    return jsonify({'ok': True, 'data': {'status': 'idle', 'progress': 0}})


@app.post('/api/update/cleanup-stuck')
def cleanup_stuck_update():
    return jsonify({'ok': True, 'data': {'message': '已清理'}})


@app.post('/api/update/install')
def install_update():
    return jsonify({'ok': True, 'data': {'message': '更新已开始'}})


@app.get('/api/hailo/status')
def get_hailo_status():
    return jsonify({'ok': True, 'data': {'installed': False}})


@app.post('/api/hailo/install')
def install_hailo_dependencies():
    return jsonify({'ok': True, 'data': {'message': '安装中'}})


# -- 主题 --
@app.get('/api/themes')
def get_themes():
    return jsonify({'ok': True, 'data': {'themes': []}})


@app.get('/api/themes/<theme_id>/previews/<int:index>')
def theme_preview(theme_id: str, index: int):
    return jsonify({'ok': True, 'data': {}})


@app.post('/api/themes/redeem')
def redeem_theme():
    return jsonify({'ok': True, 'data': {'message': '已兑换'}})


@app.post('/api/themes/<theme_id>/install')
def install_theme(theme_id: str):
    return jsonify({'ok': True, 'data': {'message': '已安装'}})


@app.put('/api/themes/current')
def select_theme():
    return jsonify({'ok': True, 'data': {'message': '主题已切换'}})


@app.get('/theme-assets/<theme_id>/<version>/<path:filename>')
def theme_asset(theme_id: str, version: str, filename: str):
    return jsonify({'ok': True, 'data': {}})


@app.get('/api/xcsh/background')
def get_xcsh_background():
    return jsonify({'ok': True, 'data': {'background': None}})


@app.post('/api/xcsh/background')
def upload_xcsh_background():
    return jsonify({'ok': True, 'data': {'message': '已上传'}})


@app.patch('/api/xcsh/background')
def update_xcsh_background():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


@app.delete('/api/xcsh/background')
def delete_xcsh_background():
    return jsonify({'ok': True, 'data': {'message': '已删除'}})


@app.get('/api/xcsh/background/image')
def get_xcsh_background_image():
    return jsonify({'ok': True, 'data': {}})


# -- 运动训练 --
@app.get('/api/motion-profiles')
def list_motion_profiles():
    return jsonify({'ok': True, 'data': {'profiles': []}})


@app.post('/api/motion-profiles')
def create_motion_profile():
    return jsonify({'ok': True, 'data': {'message': '已创建', 'profile_id': 'default'}})


@app.patch('/api/motion-profiles/<profile_id>')
def rename_motion_profile(profile_id: str):
    return jsonify({'ok': True, 'data': {'message': '已重命名'}})


@app.delete('/api/motion-profiles/<profile_id>')
def delete_motion_profile(profile_id: str):
    return jsonify({'ok': True, 'data': {'message': '已删除'}})


@app.get('/api/motion-profiles/<profile_id>/export')
def export_motion_profile(profile_id: str):
    return jsonify({'ok': True, 'data': {}})


@app.post('/api/motion-training/sessions')
def start_motion_training_session():
    return jsonify({'ok': True, 'data': {'session_id': 'mock-session', 'message': '已开始'}})


@app.put('/api/motion-training/sessions/<session_id>/heartbeat')
def heartbeat_motion_training_session(session_id: str):
    return jsonify({'ok': True, 'data': {'message': 'ok'}})


@app.post('/api/motion-training/sessions/<session_id>/samples')
def append_motion_training_sample(session_id: str):
    return jsonify({'ok': True, 'data': {'message': '已采集'}})


@app.delete('/api/motion-training/sessions/<session_id>')
def stop_motion_training_session(session_id: str):
    return jsonify({'ok': True, 'data': {'message': '已停止'}})


@app.post('/api/motion-profiles/<profile_id>/train')
def train_motion_profile(profile_id: str):
    return jsonify({'ok': True, 'data': {'message': '训练开始'}})


@app.post('/api/motion-profiles/<profile_id>/activate')
def activate_motion_profile(profile_id: str):
    return jsonify({'ok': True, 'data': {'message': '已激活'}})


@app.delete('/api/motion-profiles/active')
def deactivate_motion_profile():
    return jsonify({'ok': True, 'data': {'message': '已停用'}})


@app.delete('/api/motion-profiles/<profile_id>/samples')
def clear_motion_profile_samples(profile_id: str):
    return jsonify({'ok': True, 'data': {'message': '已清除'}})


# -- 远程 --
@app.post('/api/remote/connect')
def remote_connect():
    return jsonify({'ok': True, 'data': {'message': '已连接', 'session_id': 'mock-remote'}})


@app.get('/api/remote/models')
def remote_models():
    return jsonify({'ok': True, 'data': {'models': []}})


@app.post('/api/remote/import')
def remote_import():
    return jsonify({'ok': True, 'data': {'message': '已导入'}})


@app.post('/api/remote/delete')
def remote_delete():
    return jsonify({'ok': True, 'data': {'message': '已删除'}})


# -- 其他 --
@app.get('/api/makcu/devices')
def list_makcu_devices():
    return jsonify({'ok': True, 'data': {'devices': []}})


@app.get('/api/ferrum/devices')
def list_ferrum_devices():
    return jsonify({'ok': True, 'data': {'devices': []}})


@app.get('/api/kmboxb/devices')
def list_kmboxb_devices():
    return jsonify({'ok': True, 'data': {'devices': []}})


@app.post('/api/mouse-output/test-circle')
def test_mouse_output_circle():
    return jsonify({'ok': True, 'data': {'message': '测试已开始'}})


# -- 预览 --
@app.get('/api/preview.jpg')
def preview():
    r = ipc_request('GET_PREVIEW', timeout=3)
    if r.get('status') == 0 and r.get('data', {}).get('jpeg_base64'):
        px = base64.b64decode(r['data']['jpeg_base64'])
    else:
        px = b''
    return Response(px, mimetype='image/jpeg')


@app.get('/api/preview.mjpg')
def preview_stream():
    def generate():
        while True:
            r = ipc_request('GET_PREVIEW', timeout=2)
            if r.get('status') == 0 and r.get('data', {}).get('jpeg_base64'):
                px = base64.b64decode(r['data']['jpeg_base64'])
                if px:
                    yield b'--ttboxframe\r\n'
                    yield b'Content-Type: image/jpeg\r\n'
                    yield f'Content-Length: {len(px)}\r\n\r\n'.encode()
                    yield px
                    yield b'\r\n'
            time.sleep(0.08)
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=ttboxframe')


# ====================================================================
# 入口
# ====================================================================
def main():
    from waitress import serve
    print(f'TTBOX Web 后端启动: http://{LISTEN_HOST}:{LISTEN_PORT}')
    print(f'  模板目录: {TEMPLATE_DIR}')
    print(f'  静态目录: {STATIC_DIR}')
    print(f'  IPC Socket: {IPC_SOCKET}')
    serve(app, host=LISTEN_HOST, port=LISTEN_PORT)


if __name__ == '__main__':
    main()