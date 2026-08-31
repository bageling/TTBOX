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

try:
    import wifi_manager
except ImportError:
    wifi_manager = None

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

PRESETS_DIR = '/opt/ttbox/presets'

DEFAULT_LICENSE = {
    'activated': True, 'valid': True, 'mode': 'ttbox',
    'status': 'valid', 'message': '',
}


# ====================================================================
# 板载资源采集（真实 procfs/sysfs 读数，非占位）
# ====================================================================
_CPU_T0 = [0, 0.0]  # [样本次数, 累计 idle] —— 双采样差分算 CPU%
_CPU_TOTAL0 = [0, 0.0]


def _read_float(path, default=0.0):
    try:
        with open(path, 'r') as f:
            return float(f.read().strip())
    except Exception:
        return default


def _read_int(path, default=0):
    try:
        with open(path, 'r') as f:
            return int(f.read().strip())
    except Exception:
        return default


def _cpu_percent():
    """两次 /proc/stat 采样差分 → CPU 占用%（0~100）。"""
    try:
        with open('/proc/stat') as f:
            parts = f.readline().split()
        vals = [int(x) for x in parts[1:]]
        if len(vals) < 4:
            return 0.0
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        if _CPU_T0[0] > 0:
            didle = idle - _CPU_T0[1]
            dtotal = total - _CPU_TOTAL0[1]
            _CPU_T0[0] += 1
            _CPU_TOTAL0[0] += 1
            if dtotal > 0:
                return round(max(0.0, min(100.0, 100.0 * (1.0 - didle / dtotal))), 1)
        _CPU_T0[0] += 1
        _CPU_TOTAL0[0] += 1
        _CPU_T0[1] = idle
        _CPU_TOTAL0[1] = total
        return 0.0
    except Exception:
        return 0.0


def _memory():
    try:
        with open('/proc/meminfo') as f:
            mem = {}
            for line in f:
                k, _, v = line.partition(':')
                if k in ('MemTotal', 'MemFree', 'MemAvailable', 'Buffers', 'Cached'):
                    mem[k] = int(v.strip().split()[0]) * 1024
        total = mem.get('MemTotal', 0)
        avail = mem.get('MemAvailable', mem.get('MemFree', 0))
        used = max(0, total - avail)
        return {
            'total': total, 'used': used, 'free': avail,
            'percent': round(100.0 * used / total, 1) if total else 0.0,
        }
    except Exception:
        return {'total': 0, 'used': 0, 'free': 0, 'percent': 0.0}


def _temperature():
    """优先 SoC 温度（thermal_zone0 soc-thermal），回退第一个可用 zone。"""
    best = None
    try:
        import glob
        for z in sorted(glob.glob('/sys/class/thermal/thermal_zone*')):
            try:
                with open(z + '/type') as f:
                    ztype = f.read().strip()
            except Exception:
                continue
            temp = _read_float(z + '/temp', 0.0) / 1000.0
            if temp <= 0:
                continue
            if ztype == 'soc-thermal':
                return {'celsius': round(temp, 1), 'label': 'SoC', 'zone': ztype}
            if best is None:
                best = (temp, ztype)
    except Exception:
        pass
    if best:
        return {'celsius': round(best[0], 1), 'label': best[1], 'zone': best[1]}
    return {'celsius': 0.0, 'label': 'thermal', 'zone': ''}


def _storage():
    try:
        st = os.statvfs('/')
        total = st.f_blocks * st.f_frsize
        free = st.f_bfree * st.f_frsize
        used = total - free
        avail = st.f_bavail * st.f_frsize
        return {
            'total': total, 'used': used, 'free': avail,
            'percent': round(100.0 * used / total, 1) if total else 0.0,
        }
    except Exception:
        return {'total': 0, 'used': 0, 'free': 0, 'percent': 0.0}


def _load_average():
    try:
        with open('/proc/loadavg') as f:
            parts = f.read().split()
        return [float(x) for x in parts[:3]]
    except Exception:
        return []


def _hostname():
    try:
        import socket as _s
        return _s.gethostname()
    except Exception:
        return 'ttbox'


def _lan_ipv4():
    try:
        import subprocess as _sp
        out = _sp.check_output(['hostname', '-I'], text=True, timeout=2).split()
        for ip in out:
            if ip and not ip.startswith('127.'):
                return ip
    except Exception:
        pass
    return ''


def _uptime_seconds():
    try:
        with open('/proc/uptime') as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0


def collect_system_stats() -> dict:
    return {
        'hostname': _hostname(),
        'uptime_seconds': _uptime_seconds(),
        'cpu_percent': _cpu_percent(),
        'load_average': _load_average(),
        'memory': _memory(),
        'temperature': _temperature(),
        'storage': _storage(),
        'lan_ipv4': _lan_ipv4(),
        'lan_url': '',
        'mdns_url': '',
        'web_port': LISTEN_PORT,
        'os': 'Orange Pi 1.2.0',
        'version': '',
        'app_version': 'ttbox-0.1.0',
    }


# ====================================================================
# IPC 通信
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
# 映射依据：YU 前端 collectConfig()（web/static/app.js:5663）+ YU daemon
# 二进制字段名实测。predict_x/y 是 Pid1Controller 的 I 通道增益（无量纲），
# rate_x/y 是 kp_gain_rate，smooth_x/y 是 soft-limit 宽度——三者全部直通。
# ====================================================================
HOTKEY_BITS = {'left': 1, 'right': 2, 'middle': 4, 'back': 8, 'forward': 16}
BIT_HOTKEYS = {v: k for k, v in HOTKEY_BITS.items()}


def _hotkey_to_bits(v, default=0):
    """YU 热键字符串（'left'/'right'/''）→ 位掩码。"""
    if isinstance(v, str):
        return HOTKEY_BITS.get(v.strip().lower(), default)
    if isinstance(v, (int, float)):
        return int(v)
    return default


def _bits_to_hotkey(v):
    """位掩码 → YU 热键字符串（0 → ''）。"""
    try:
        return BIT_HOTKEYS.get(int(v), '')
    except (TypeError, ValueError):
        return ''


# controller 内的数值/布尔直通字段（YU key → mouse key）
CONTROLLER_NUMS = {
    'kp_x': 'kp_x', 'kp_y': 'kp_y',
    'ki_x': 'ki_x', 'ki_y': 'ki_y',
    'kd_x': 'kd_x', 'kd_y': 'kd_y',
    'predict_x': 'predict_x', 'predict_y': 'predict_y',
    'rate_x': 'rate_x', 'rate_y': 'rate_y',
    'smooth_x': 'smooth_x', 'smooth_y': 'smooth_y',
    'output_deadzone': 'output_deadzone',
    'selector_lost_grace_ms': 'lost_grace_ms',
    'aim_reference_offset_x': 'aim_offset_x',
    'aim_reference_offset_y': 'aim_offset_y',
    'y_axis_fire_release_delay_sec': 'y_axis_fire_release_delay_sec',
}
# controller 内的布尔直通字段
CONTROLLER_BOOLS = {
    'aim_fire_lock_y': 'aim_fire_lock_y',
    'block_physical_mouse_x_while_aiming': 'block_physical_x',
    'block_physical_mouse_y_while_aiming': 'block_physical_y',
    'continuous_lead_enabled': '_cl_enabled',
    'pull_curve_enabled': '_pc_enabled',
    'humanize_enabled': '_hz_enabled',
}


def yu_body_to_profile(body: dict) -> dict:
    """YU 前端保存的配置格式（collectConfig 扁平结构）→ RuntimeProfile。"""
    ctrl = (body.get('ai') or {}).get('controller') or {}
    mouse: dict = {}

    # 1) controller 数值/布尔直通
    for yk, tk in CONTROLLER_NUMS.items():
        if ctrl.get(yk) is not None:
            mouse[tk] = ctrl[yk]
    for yk, tk in CONTROLLER_BOOLS.items():
        if ctrl.get(yk) is not None:
            if tk.startswith('_'):
                continue  # 嵌套结构开关，下面统一处理
            mouse[tk] = bool(ctrl[yk])
    # 热键：字符串 → 位掩码
    if ctrl.get('y_axis_fire_hotkey') is not None:
        mouse['y_axis_fire_hotkey'] = _hotkey_to_bits(ctrl['y_axis_fire_hotkey'], 1)

    # 2) 插件结构（pull_curve / continuous_lead / humanize）
    pull_curve: dict = {}
    if ctrl.get('pull_curve_enabled') is not None:
        pull_curve['enabled'] = bool(ctrl['pull_curve_enabled'])
    for yk, tk in [('pull_curve_strength', 'strength'),
                   ('pull_curve_jitter_px', 'jitter_px'),
                   ('pull_curve_min_distance', 'min_distance')]:
        if ctrl.get(yk) is not None:
            pull_curve[tk] = ctrl[yk]
    if pull_curve:
        mouse['pull_curve'] = pull_curve

    continuous_lead: dict = {}
    if ctrl.get('continuous_lead_enabled') is not None:
        continuous_lead['enabled'] = bool(ctrl['continuous_lead_enabled'])
    for yk, tk in [('continuous_lead_enter_distance', 'enter_distance'),
                   ('continuous_lead_scale', 'scale'),
                   ('continuous_lead_fade_in_ms', 'fade_in_ms'),
                   ('continuous_lead_fade_out_ms', 'fade_out_ms'),
                   ('continuous_lead_near_disable_ratio', 'near_disable_ratio')]:
        if ctrl.get(yk) is not None:
            continuous_lead[tk] = ctrl[yk]
    if continuous_lead:
        mouse['continuous_lead'] = continuous_lead

    # 3) 目标选择
    if ctrl.get('selector_lost_grace_ms') is not None:
        mouse['lost_grace_ms'] = ctrl['selector_lost_grace_ms']

    # 4) aim_profiles[0]：热键 / 瞄准点 / profile 灵敏度
    profiles = body.get('aim_profiles') or []
    p0 = profiles[0] if profiles else {}
    if p0.get('hotkey') is not None:
        mouse['aim_hotkey'] = _hotkey_to_bits(p0['hotkey'], 2) or 2
    if p0.get('hotkey2') is not None:
        mouse['aim_hotkey2'] = _hotkey_to_bits(p0['hotkey2'], 0)
    if p0.get('hotkey_mode') is not None:
        mouse['aim_hotkey_mode'] = 1 if p0['hotkey_mode'] == 'all' else 0
    aim_point_vals: dict = {}
    if p0.get('offset_x') is not None:
        aim_point_vals['offset_x'] = p0['offset_x']
    if p0.get('offset_y') is not None:
        aim_point_vals['offset_y'] = p0['offset_y']

    # 5) 全局量：sens / pos / range_factor
    #    sens → sensitivity（输出全局缩放）；pos → aim_point.offset_y（瞄准高度）
    if body.get('sens') is not None:
        mouse['sensitivity'] = body['sens']
    if body.get('pos') is not None:
        aim_point_vals['offset_y'] = body['pos']
    # RuntimeProfile::from_json 读平铺的 offset_x/offset_y（mouse.aim_point 是内部结构，
    # JSON 层平铺为 mouse.offset_x/mouse.offset_y），此处按 Core 契约平铺写入。
    for k, v in aim_point_vals.items():
        mouse[k] = v

    # 6) 推理参数
    inference: dict = {}
    if body.get('video_detection_confidence') is not None:
        inference['confidence'] = body['video_detection_confidence']
    if body.get('video_detection_iou') is not None:
        inference['iou'] = body['video_detection_iou']
    if inference:
        pass  # confidence/iou 语义：0 = 用模型默认（Core 已处理）

    # 7) 采集
    capture: dict = {}
    cap = body.get('capture') or {}
    if cap.get('crop_size') is not None:
        capture['width'] = cap['crop_size']
        capture['height'] = cap['crop_size']
    if cap.get('crop_offset_x') is not None:
        capture['offset_x'] = cap['crop_offset_x']
    if cap.get('crop_offset_y') is not None:
        capture['offset_y'] = cap['crop_offset_y']

    # 8) FOV（range_factor <1 = 启用圆形选择区）
    fov: dict = {}
    try:
        prev = _get_runtime_profile()
        prev_fov = prev.get('fov') or {}
    except Exception:
        prev_fov = {}
    fov['shape'] = prev_fov.get('shape', 0)
    fov['center_x'] = prev_fov.get('center_x', 0.5)
    fov['center_y'] = prev_fov.get('center_y', 0.5)
    if body.get('range_factor') is not None:
        fov['radius'] = body['range_factor']
        fov['enabled'] = body['range_factor'] < 1.0
    else:
        fov['enabled'] = prev_fov.get('enabled', False)
        fov['radius'] = prev_fov.get('radius', 0.5)

    # 9) 预览帧率
    preview: dict = {}
    lat = body.get('latency') or {}
    if lat.get('preview_interval_ms') is not None:
        iv = int(lat['preview_interval_ms'])
        if iv > 0:
            preview['fps'] = max(1, min(60, int(1000 / iv)))

    prof: dict = {
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
    """RuntimeProfile → YU 前端需要的格式（populate 回读完整字段）。"""
    mouse = prof.get('mouse') or {}
    # aim_point 在 Core JSON 层是平铺的 mouse.offset_x/mouse.offset_y
    # （RuntimeProfile::to_json 平铺输出，from_json 平铺读取）；
    # mouse.aim_point 子对象只在 C++ 结构体内部存在，JSON 层没有。
    ap = {
        'offset_x': mouse.get('offset_x', 0.5),
        'offset_y': mouse.get('offset_y', 0.5),
    }
    pc = mouse.get('pull_curve') or {}
    cl = mouse.get('continuous_lead') or {}
    hz = mouse.get('humanize') or {}
    fov_p = prof.get('fov') or {}
    prev_p = prof.get('preview') or {}
    inf = prof.get('inference') or {}
    cap = prof.get('capture') or {}

    ctrl = {
        'kp_x': mouse.get('kp_x'), 'kp_y': mouse.get('kp_y'),
        'ki_x': mouse.get('ki_x'), 'ki_y': mouse.get('ki_y'),
        'kd_x': mouse.get('kd_x'), 'kd_y': mouse.get('kd_y'),
        'predict_x': mouse.get('predict_x'), 'predict_y': mouse.get('predict_y'),
        'rate_x': mouse.get('rate_x'), 'rate_y': mouse.get('rate_y'),
        'smooth_x': mouse.get('smooth_x'), 'smooth_y': mouse.get('smooth_y'),
        'output_deadzone': mouse.get('output_deadzone'),
        'selector_lost_grace_ms': mouse.get('lost_grace_ms'),
        'aim_reference_offset_x': mouse.get('aim_offset_x'),
        'aim_reference_offset_y': mouse.get('aim_offset_y'),
        'aim_fire_lock_y': mouse.get('aim_fire_lock_y', False),
        'block_physical_mouse_x_while_aiming': mouse.get('block_physical_x', False),
        'block_physical_mouse_y_while_aiming': mouse.get('block_physical_y', False),
        'y_axis_fire_hotkey': _bits_to_hotkey(mouse.get('y_axis_fire_hotkey', 1)) or 'left',
        'y_axis_fire_release_delay_sec': mouse.get('y_axis_fire_release_delay_sec', 0.3),
        'pull_curve_enabled': pc.get('enabled', True),
        'pull_curve_strength': pc.get('strength', 0.8),
        'pull_curve_jitter_px': pc.get('jitter_px', 3.0),
        'pull_curve_min_distance': pc.get('min_distance', 80),
        'continuous_lead_enabled': cl.get('enabled', False),
        'continuous_lead_enter_distance': cl.get('enter_distance', 150),
        'continuous_lead_scale': cl.get('scale', 0.5),
        'continuous_lead_fade_in_ms': cl.get('fade_in_ms', 300),
        'continuous_lead_fade_out_ms': cl.get('fade_out_ms', 300),
        'continuous_lead_near_disable_ratio': cl.get('near_disable_ratio', 0.66),
        'humanize_enabled': hz.get('enabled', True),
        'humanize_curve_strength': hz.get('curve_strength', 0.45),
        'humanize_jitter_px': hz.get('jitter_px', 0.25),
        'humanize_jitter_frequency': hz.get('jitter_frequency', 8),
        'selector_search_radius': mouse.get('selector_search_radius', 170),
    }

    lat = {}
    if prev_p.get('fps') not in (None, 0):
        try:
            lat['preview_interval_ms'] = max(1, int(1000 / int(prev_p['fps'])))
        except (TypeError, ValueError, ZeroDivisionError):
            lat['preview_interval_ms'] = 66
    else:
        lat['preview_interval_ms'] = 66

    return {
        'model_id': prof.get('model_id', ''),
        'video_detection_confidence': inf.get('confidence'),
        'video_detection_iou': inf.get('iou'),
        'capture': {
            'device': '/dev/video0',
            'crop_size': cap.get('width'),
            'crop_offset_x': cap.get('offset_x'),
            'crop_offset_y': cap.get('offset_y'),
        },
        'range_factor': fov_p.get('radius', 1.0) if fov_p.get('enabled') else 1.0,
        'sens': mouse.get('sensitivity', 1.0),
        'pos': ap.get('offset_y', 0.5),
        'ai': {'controller': ctrl},
        'aim_profiles': [{
            'hotkey': _bits_to_hotkey(mouse.get('aim_hotkey', 2)) or 'right',
            'hotkey2': _bits_to_hotkey(mouse.get('aim_hotkey2', 0)),
            'hotkey_mode': 'all' if mouse.get('aim_hotkey_mode') == 1 else 'any',
            'sensitivity': mouse.get('sensitivity', 1.0),
            'offset_x': ap.get('offset_x', 0.5),
            'offset_y': ap.get('offset_y', 0.5),
            'alternate_offset_x': 0.5, 'alternate_offset_y': 0.5,
            'class_filter_mask': 0, 'fov_scale': 1.0,
            'class_offsets': [],
            'offset_switch_enabled': False, 'offset_switch_hotkey': '',
        }],
        'recoil': {}, 'rapid_fire': {}, 'auto_back_flick': {}, 'crosshair': {},
        'auto_trigger': {'enabled': False, 'profiles': []},
        'hotkey_guard': {'enabled': False, 'toggle_hotkey': 'middle'},
        'mouse_output': {'mode': 'passthrough'},
        'latency': lat, 'fan_control': {}, 'loopout_overlay': {},
    }


def collect_yu_state() -> dict:
    """合成 /api/state 的完整数据。"""
    st = _get_status()
    prof = _get_runtime_profile()
    ml = ipc_request('MODEL_LIST')
    ml_data = (ml.get('data', {}) or {}) if ml.get('status') == 0 else {}
    # YU 同构：state.models = 数组，字段对齐前端模型卡片（id/display_name/backend/enabled/尺寸）
    models = []
    for mm in ml_data.get('models', []):
        models.append({
            'id': mm.get('model_id'),
            'model_id': mm.get('model_id'),
            'name': mm.get('label') or mm.get('model_id'),
            'display_name': mm.get('label') or mm.get('model_id'),
            'label': mm.get('label'),
            'version': mm.get('version'),
            'status': mm.get('status_name') or ('installed' if mm.get('status') == 2 else 'staging'),
            'origin': mm.get('origin'),
            'backend': 'rknn',
            'enabled': True,
            'imported': True,
        })
    active_model = ml_data.get('active', '')

    m = st.get('metrics', {})
    # config 回读直接复用 profile_to_yu（单一真源，避免两处翻译漂移）
    config_yu = profile_to_yu(prof)
    running = bool(st.get('running')) and bool(st.get('runtime_running'))

    return {
        'ok': True,
        'data': {
            'app_version': 'ttbox-' + str(st.get('version', '')),
            'version': str(st.get('version', '')),
            'config': config_yu,
            'models': models,  # YU 同构：数组
            'selected_model_id': active_model,
            'presets': {'presets': []},
            'state': {
                'aim': {'active': m.get('aim_active', False), 'last_error': ''},
                'capture': {
                    'input_width': 0, 'input_height': 0,
                    'capture_fps': m.get('capture_fps', 0),
                    'buffer_age_ms': m.get('buffer_age_ms', 0),
                    'last_dequeued_count': m.get('last_dequeued_count', 0),
                    'buffer_count': m.get('buffer_count', 0),
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
                'latency': {'capture_to_mouse_send_ms': m.get('e2e_ms', 0), 'preprocess_to_track_ms': m.get('e2e_ms', 0)},
                'license': DEFAULT_LICENSE,
                'mouse_output': {},
                # MJPEG 流（动态预览）：img 标签原生支持 multipart/x-mixed-replace，
                # 前端 previewImage 直接消费；不能用 /api/preview.jpg（静态单帧，加载一次就冻结）
                'preview_path': '/api/preview.mjpg',
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
                'ui_brand': 'ttbox',
                'default_theme': 'dark',
                'allow_theme_switch': True,
            },
            'ui_brand': 'ttbox',
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
        ui_brand='ttbox',
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
        app_title='TTBOX 控制台', ui_brand='ttbox',
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
        app_title='TTBOX 控制台', ui_brand='ttbox',
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
    return jsonify({'ok': True, 'data': collect_system_stats()})


@app.get('/api/system/storage')
def get_storage_status():
    s = _storage()
    return jsonify({
        'ok': True,
        'data': {
            'total': s['total'], 'used': s['used'], 'free': s['free'],
            'percent': s['percent'],
            'rootfs': {'ok': True, 'percent': s['percent'],
                       'total': s['total'], 'used': s['used'], 'free': s['free']},
        },
    })


@app.post('/api/system/storage/expand')
def expand_storage():
    try:
        out = subprocess.run(['lsblk', '-b', '-n', '-o', 'NAME,SIZE', '/dev/mmcblk0'], capture_output=True, text=True, timeout=5)
        return jsonify({'ok': True, 'data': {'message': '根分区在线检测完成，扩容需重启进恢复流程', 'detail': out.stdout[:300]}})
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'检测失败: {exc}'})


@app.put('/api/system/hostname')
def update_system_hostname():
    body = request.get_json(silent=True) or {}
    hostname = str(body.get('hostname', '')).strip()
    if not hostname or len(hostname) > 63:
        return jsonify({'ok': False, 'error': '主机名无效'})
    r = subprocess.run(['hostnamectl', 'set-hostname', hostname], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return jsonify({'ok': False, 'error': r.stderr or '设置失败'})
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
    threading.Thread(target=lambda: (time.sleep(1.5), os.system('systemctl reboot')), daemon=True).start()


@app.post('/api/system/poweroff')
def poweroff_system():
    threading.Thread(target=lambda: (time.sleep(1.5), os.system('systemctl poweroff')), daemon=True).start()


@app.get('/api/events')
def events():
    return jsonify({'ok': True, 'data': {'events': []}})


# -- 配置 --
def _deep_merge_profile(base: dict, patch: dict) -> dict:
    """RuntimeProfile 深合并：子对象（capture/fov/mouse/...）按键级合并而非整体替换。

    YU 前端每次 PUT 都是全量 collectConfig，但翻译层只产出非空子集；
    若浅合并，未提交的子对象（如 geometry_filter）会被 partial dict 整体顶掉，
    导致"保存一个字段 → 其它字段全丢"的参数失效问题。
    """
    merged = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_profile(merged[k], v)
        else:
            merged[k] = v
    return merged


@app.put('/api/config')
def update_config():
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({'ok': False, 'error': '非法请求体'}), 400
    translated = yu_body_to_profile(body)
    base = _get_runtime_profile()
    prof = _deep_merge_profile(base, translated)
    r = ipc_request('SET_CONFIG', {'profile': prof})
    if r.get('status') != 0:
        return jsonify({'ok': False, 'error': r.get('error', '配置保存失败')}), 502
    # 回读 canonical（Core 是唯一真源，UI 永远不领先 Core）
    rr = _get_runtime_profile()
    return jsonify({'ok': True, 'data': profile_to_yu(rr)})


@app.get('/api/config')
def get_config_yu():
    prof = _get_runtime_profile()
    return jsonify({'ok': True, 'data': profile_to_yu(prof)})


def _auto_start_enabled() -> bool:
    try:
        out = subprocess.check_output(['systemctl', 'is-enabled', 'ttbox-core'],
                                      text=True, timeout=3).strip()
        return out == 'enabled'
    except Exception:
        return False


@app.get('/api/settings/auto-start')
def get_auto_start_setting():
    return jsonify({'ok': True, 'data': {'enabled': _auto_start_enabled(),
                                         'initial_delay': 0,
                                         'message': '开机自动启动采集和推理'}})


@app.put('/api/settings/auto-start')
def update_auto_start_setting():
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get('enabled'))
    action = 'enable' if enabled else 'disable'
    try:
        subprocess.run(['systemctl', action, 'ttbox-core'], check=True, timeout=5)
        subprocess.run(['systemctl', action, 'ttbox-web'], check=True, timeout=5)
        return jsonify({'ok': True, 'data': {'enabled': enabled,
                                             'message': '下次开机将自动启动' if enabled else '开机后保持停止'}})
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'设置失败: {exc}'})


# -- 模型 --
@app.get('/api/models')
def list_models():
    r = ipc_request('MODEL_LIST')
    if r.get('status') != 0:
        return jsonify({'ok': True, 'data': {'models': [], 'active': '', 'ok': True}})
    d = r.get('data', {}) or {}
    out = []
    for m in d.get('models', []):
        out.append({
            'id': m.get('model_id'),
            'model_id': m.get('model_id'),
            'name': m.get('label') or m.get('model_id'),
            'label': m.get('label'),
            'version': m.get('version'),
            'status': m.get('status_name') or ('installed' if m.get('status') == 2 else 'staging'),
            'origin': m.get('origin'),
            'created_at': m.get('created_at'),
        })
    return jsonify({'ok': True, 'data': {'models': out, 'active': d.get('active', ''), 'ok': True}})


@app.get('/api/models/device-code')
def model_device_code():
    return jsonify({'ok': True, 'data': {'device_code': 'TTBOX-' + os.uname().nodename}})


@app.post('/api/models/cloud-encrypted')
def add_cloud_encrypted_model():
    return jsonify({'ok': True, 'data': {'message': '开发中'}})


@app.post('/api/models/import')
def import_model():
    f = request.files.get('file')
    if f is None or not f.filename:
        return jsonify({'ok': False, 'error': '缺少模型文件'})
    fname = f.filename
    if not fname.lower().endswith('.rknn'):
        return jsonify({'ok': False, 'error': '仅支持 .rknn 模型文件'})
    stem = re.sub(r'\.rknn$', '', fname, flags=re.I)
    model_id = re.sub(r'[^A-Za-z0-9_\-]', '_', stem)[:64].strip('_') or 'model'
    # label 保留原始文件名主干（含中文），供前端显示；model_id 是净化后的内部标识
    label = stem.strip() or model_id
    incoming = Path('/opt/ttbox/models/_incoming')
    incoming.mkdir(parents=True, exist_ok=True)
    dst = incoming / f'{model_id}.rknn'
    f.save(str(dst))
    r1 = ipc_request('MODEL_IMPORT', {'src_path': str(dst), 'model_id': model_id, 'label': label})
    if r1.get('status') != 0:
        dst.unlink(missing_ok=True)
        return jsonify({'ok': False, 'error': r1.get('error', '导入失败')})
    r2 = ipc_request('MODEL_VALIDATE', {'model_id': model_id})
    if r2.get('status') != 0:
        return jsonify({'ok': False, 'error': r2.get('error', '校验失败')})
    r3 = ipc_request('MODEL_INSTALL', {'model_id': model_id})
    if r3.get('status') != 0:
        return jsonify({'ok': False, 'error': r3.get('error', '安装失败')})
    return jsonify({'ok': True, 'data': {'message': '导入成功', 'model_id': model_id}})


@app.post('/api/models/delete')
def delete_model():
    body = request.get_json(silent=True) or {}
    model_id = body.get('model_id', '')
    if not model_id:
        return jsonify({'ok': False, 'error': '缺少 model_id'})
    r = ipc_request('MODEL_REMOVE', {'model_id': model_id})
    if r.get('status') != 0:
        return jsonify({'ok': False, 'error': r.get('error', '删除失败')})
    return jsonify({'ok': True, 'data': {'message': '已删除'}})


@app.post('/api/models/select')
def select_model():
    body = request.get_json(force=True)
    model_id = body.get('model_id', '')
    if not model_id:
        return jsonify({'ok': False, 'error': '缺少 model_id'})
    ra = ipc_request('MODEL_ACTIVATE', {'model_id': model_id})
    if ra.get('status') != 0:
        return jsonify({'ok': False, 'error': ra.get('error', '激活失败')})
    prof = _get_runtime_profile()
    prof['model_id'] = model_id
    ipc_request('SET_CONFIG', {'profile': prof})
    # 同步 config 的 model_path/model_label 到已安装模型（runtime 重启后加载新模型）
    inst = f'/opt/ttbox/models/installed/{model_id}/model.rknn'
    cpath = os.environ.get('TTBOX_CONFIG', '/opt/ttbox/config/default.json')
    try:
        cfg = json.load(open(cpath))
        if os.path.exists(inst):
            cfg['model_path'] = inst
            cfg['model_label'] = model_id
        else:
            cfg['model_label'] = model_id
            cfg.pop('model_path', None)
        json.dump(cfg, open(cpath, 'w'), indent=2, ensure_ascii=False)
    except Exception:
        pass
    return jsonify({'ok': True, 'data': {'message': '模型已切换，重启 AI 后生效'}})


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
    body = request.get_json(silent=True) or {}
    model_id = body.get('model_id', '')
    conc = body.get('rknn_concurrency')
    if not model_id or conc is None:
        return jsonify({'ok': False, 'error': '缺少 model_id / rknn_concurrency'})
    try:
        conc = int(conc)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'rknn_concurrency 必须是数字'})
    conc = max(1, min(3, conc))
    # YU 语义：并发数 = NPU worker 数。映射到 worker_cores（1→单核, 2→双核, 3→三核并行）
    cores_map = {1: '1', 2: '1,2', 3: '1,2,4'}
    cpath = os.environ.get('TTBOX_CONFIG', '/opt/ttbox/config/default.json')
    try:
        cfg = json.load(open(cpath))
        cfg['worker_cores'] = cores_map[conc]
        json.dump(cfg, open(cpath, 'w'), indent=2, ensure_ascii=False)
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'写配置失败: {exc}'})
    # 同时记进模型 manifest（前端显示用）
    manifest_path = f'/opt/ttbox/models/installed/{model_id}/manifest.json'
    if os.path.exists(manifest_path):
        try:
            manifest = json.load(open(manifest_path))
            manifest['rknn_concurrency'] = conc
            json.dump(manifest, open(manifest_path, 'w'), indent=2, ensure_ascii=False)
        except Exception:
            pass
    return jsonify({'ok': True, 'data': {'message': f'并发已设为 {conc}，重启 AI 后生效', 'rknn_concurrency': conc}})


@app.post('/api/models/hailo-pipeline-depth')
def update_model_hailo_pipeline_depth():
    return jsonify({'ok': True, 'data': {'message': '已更新'}})


@app.post('/api/models/class-names')
def update_model_class_names():
    body = request.get_json(silent=True) or {}
    model_id = body.get('model_id', '')
    names = body.get('class_names')
    if not model_id:
        return jsonify({'ok': False, 'error': '缺少 model_id'})
    if not isinstance(names, list):
        return jsonify({'ok': False, 'error': 'class_names 必须是字符串数组'})
    names = [str(x).strip() for x in names if str(x).strip()]
    manifest_path = f'/opt/ttbox/models/installed/{model_id}/manifest.json'
    if not os.path.exists(manifest_path):
        return jsonify({'ok': False, 'error': f'模型不存在: {model_id}'})
    try:
        manifest = json.load(open(manifest_path))
        manifest['class_names'] = names
        manifest['class_count'] = len(names)
        json.dump(manifest, open(manifest_path, 'w'), indent=2, ensure_ascii=False)
        return jsonify({'ok': True, 'data': {'message': '类别已更新', 'class_names': names}})
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'写入失败: {exc}'})


# -- 预设 --
@app.get('/api/presets')
def list_presets():
    d = Path(PRESETS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    names = sorted(p.stem for p in d.glob('*.json'))
    return jsonify({'ok': True, 'data': {'presets': names}})


@app.post('/api/presets')
def save_or_delete_preset():
    body = request.get_json(silent=True) or {}
    name = str(body.get('name', '')).strip()
    action = body.get('action', 'save')
    if not name:
        return jsonify({'ok': False, 'error': '缺少预设名'})
    d = Path(PRESETS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub('[^\\w\\-]', '_', name)[:64]
    pf = d / (safe + '.json')
    if action == 'delete':
        pf.unlink(missing_ok=True)
        return jsonify({'ok': True, 'data': {'message': '已删除'}})
    if action == 'rename':
        new_name = str(body.get('new_name', '')).strip()
        safe2 = re.sub('[^\\w\\-]', '_', new_name)[:64]
        pf2 = d / (safe2 + '.json')
        pf2.write_text(pf.read_text() if pf.exists() else '{}')
        pf.unlink(missing_ok=True)
        return jsonify({'ok': True, 'data': {'message': '已重命名'}})
    config = body.get('config')
    if config is None:
        return jsonify({'ok': False, 'error': '缺少 config'})
    pf.write_text(json.dumps(config, ensure_ascii=False, indent=2))
    return jsonify({'ok': True, 'data': {'message': '已保存'}})


@app.post('/api/presets/load')
def load_preset():
    body = request.get_json(silent=True) or {}
    name = str(body.get('name', '')).strip()
    safe = re.sub('[^\\w\\-]', '_', name)[:64]
    pf = Path(PRESETS_DIR) / (safe + '.json')
    if not pf.exists():
        return jsonify({'ok': False, 'error': '预设不存在'})
    try:
        config = json.loads(pf.read_text())
    except Exception as exc:
        return jsonify({'ok': False, 'error': f'预设损坏: {exc}'})
    if not isinstance(config, dict) or not config:
        return jsonify({'ok': False, 'error': '预设内容为空'})
    translated = yu_body_to_profile(config)
    prof = _deep_merge_profile(_get_runtime_profile(), translated)
    r = ipc_request('SET_CONFIG', {'profile': prof})
    if r.get('status') != 0:
        return jsonify({'ok': False, 'error': r.get('error', '应用失败')})
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
    # 真实探测：HID gadget 设备 + 核心端注入开关
    import glob
    hidg = sorted(glob.glob('/dev/hidg*'))
    prof = _get_runtime_profile()
    mouse = prof.get('mouse') or {}
    return jsonify({
        'ok': True,
        'data': {
            'mode': 'proxy',
            'device': hidg[0] if hidg else '',
            'enabled': bool(mouse.get('enabled', False)),
            'connected': bool(hidg),
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
    if wifi_manager is None:
        return jsonify({'ok': True, 'data': {'available': False, 'error': 'wifi_manager 未部署'}})
    return jsonify({'ok': True, 'data': wifi_manager.wifi_status(force_scan=False)})


@app.post('/api/network/wifi/scan')
def scan_wifi_networks():
    if wifi_manager is None:
        return jsonify({'ok': True, 'data': {'available': False, 'networks': [], 'error': 'wifi_manager 未部署'}})
    return jsonify({'ok': True, 'data': wifi_manager.wifi_status(force_scan=True)})


@app.post('/api/network/wifi/connect')
def connect_wifi_network():
    body = request.get_json(silent=True) or {}
    ssid = body.get('ssid', '')
    password = body.get('password', '')
    if not ssid:
        return jsonify({'ok': False, 'error': '缺少 SSID'})
    if wifi_manager is None:
        return jsonify({'ok': False, 'error': 'wifi_manager 未部署'})
    try:
        return jsonify({'ok': True, 'data': wifi_manager.connect_wifi(ssid, password)})
    except wifi_manager.WifiError as exc:
        return jsonify({'ok': False, 'error': str(exc)})


@app.post('/api/network/wifi/fallback')
def fallback_wifi_network():
    if wifi_manager is None:
        return jsonify({'ok': False, 'error': 'wifi_manager 未部署'})
    try:
        return jsonify({'ok': True, 'data': wifi_manager.reset_to_default_wifi()})
    except wifi_manager.WifiError as exc:
        return jsonify({'ok': False, 'error': str(exc)})


@app.post('/api/network/wifi/ap/apply')
def apply_wifi_ap_hotspot():
    body = request.get_json(silent=True) or {}
    if wifi_manager is None:
        return jsonify({'ok': False, 'error': 'wifi_manager 未部署'})
    try:
        return jsonify({'ok': True, 'data': wifi_manager.apply_ap_hotspot(
            ssid=body.get('ssid'), password=body.get('password'))})
    except wifi_manager.WifiError as exc:
        return jsonify({'ok': False, 'error': str(exc)})


@app.post('/api/network/wifi/client/activate')
def activate_wifi_client_mode():
    if wifi_manager is None:
        return jsonify({'ok': False, 'error': 'wifi_manager 未部署'})
    try:
        return jsonify({'ok': True, 'data': wifi_manager.activate_client_wifi()})
    except wifi_manager.WifiError as exc:
        return jsonify({'ok': False, 'error': str(exc)})


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
    # MJPEG 流：读 Core PreviewModule 缓存（Core 端 10~15fps 生成），
    # 无帧时短暂等待而非密集空转；Core 是唯一生产节拍，本端只做搬运。
    def generate():
        # YU 同款防糊策略：只在核心端缓存更新（seq 变化）时推新帧，
        # 不重复推同一帧（浏览器 img 绘制跟不上会导致 multipart 积压 → 半帧横线花屏）。
        last_seq = -1
        last_push = time.time()
        while True:
            r = ipc_request('GET_PREVIEW', timeout=2)
            now = time.time()
            if r.get('status') == 0:
                d = r.get('data', {})
                b64 = d.get('jpeg_base64')
                seq = d.get('seq', 0)
                # seq 变化 = 核心端编码了新帧 → 推；兜底：>1s 没推也推一次（防浏览器黑屏）
                if b64 and (seq != last_seq or now - last_push > 1.0):
                    px = base64.b64decode(b64)
                    if px:
                        last_seq = seq
                        last_push = now
                        yield b'--ttboxframe\r\n'
                        yield b'Content-Type: image/jpeg\r\n'
                        yield f'Content-Length: {len(px)}\r\n\r\n'.encode()
                        yield px
                        yield b'\r\n'
            time.sleep(0.03)  # 轮询节奏 33ms（Core 端 fps 决定实际帧率）
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
