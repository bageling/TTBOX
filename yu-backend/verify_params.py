#!/usr/bin/env python3
"""端到端参数链路验证：Web PUT → Core SET_CONFIG → 回读比对"""
import json, urllib.request

BASE = 'http://127.0.0.1:8081'

def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header('Content-Type', 'application/json')
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=10) as r:
        return json.loads(r.read())

# 1. 保存一组 YU 格式的特征值（每个字段给独特值便于比对）
test_body = {
    'model_id': '',
    'video_detection_confidence': 0.37,
    'video_detection_iou': 0.52,
    'sens': 1.7,
    'pos': 0.35,
    'range_factor': 0.55,
    'capture': {'crop_size': 320, 'crop_offset_x': 10, 'crop_offset_y': -20},
    'ai': {'controller': {
        'kp_x': 21.5, 'kp_y': 13.7,
        'ki_x': 0.11, 'ki_y': 0.22,
        'kd_x': 0.33, 'kd_y': 0.44,
        'predict_x': 0.61, 'predict_y': 0.48,
        'rate_x': 0.42, 'rate_y': 0.31,
        'smooth_x': 9900, 'smooth_y': 9900,
        'output_deadzone': 2.5,
        'selector_lost_grace_ms': 45,
        'aim_reference_offset_x': 5, 'aim_reference_offset_y': -3,
        'aim_fire_lock_y': True,
        'block_physical_mouse_x_while_aiming': True,
        'block_physical_mouse_y_while_aiming': False,
        'y_axis_fire_hotkey': 'right',
        'y_axis_fire_release_delay_sec': 0.25,
        'pull_curve_enabled': True,
        'pull_curve_strength': 0.9,
        'pull_curve_jitter_px': 4.5,
        'pull_curve_min_distance': 95,
        'continuous_lead_enabled': True,
        'continuous_lead_enter_distance': 160,
        'continuous_lead_scale': 0.6,
        'continuous_lead_fade_in_ms': 320,
        'continuous_lead_fade_out_ms': 340,
        'continuous_lead_near_disable_ratio': 0.7,
    }},
    'aim_profiles': [{
        'hotkey': 'left', 'hotkey2': 'right', 'hotkey_mode': 'any',
        'sensitivity': 1.7, 'offset_x': 0.48, 'offset_y': 0.35,
    }],
}

print('=== 1. PUT /api/config（YU格式保存）===')
r = api('PUT', '/api/config', test_body)
print('ok:', r.get('ok'))

print('\n=== 2. GET /api/config（回读比对）===')
back = api('GET', '/api/config')
d = back.get('data', {})
ctrl = (d.get('ai') or {}).get('controller', {})
p0 = (d.get('aim_profiles') or [{}])[0]

checks = [
    ('sens', d.get('sens'), 1.7),
    ('pos', d.get('pos'), 0.35),
    ('range_factor', d.get('range_factor'), 0.55),
    ('conf', d.get('video_detection_confidence'), 0.37),
    ('iou', d.get('video_detection_iou'), 0.52),
    ('crop', d.get('capture', {}).get('crop_size'), 320),
    ('crop_ox', d.get('capture', {}).get('crop_offset_x'), 10),
    ('kp_x', ctrl.get('kp_x'), 21.5),
    ('kp_y', ctrl.get('kp_y'), 13.7),
    ('ki_x', ctrl.get('ki_x'), 0.11),
    ('kd_x', ctrl.get('kd_x'), 0.33),
    ('predict_x', ctrl.get('predict_x'), 0.61),
    ('predict_y', ctrl.get('predict_y'), 0.48),
    ('rate_x', ctrl.get('rate_x'), 0.42),
    ('smooth_x', ctrl.get('smooth_x'), 9900),
    ('output_deadzone', ctrl.get('output_deadzone'), 2.5),
    ('lost_grace', ctrl.get('selector_lost_grace_ms'), 45),
    ('ref_ox', ctrl.get('aim_reference_offset_x'), 5),
    ('ref_oy', ctrl.get('aim_reference_offset_y'), -3),
    ('fire_lock_y', ctrl.get('aim_fire_lock_y'), True),
    ('block_x', ctrl.get('block_physical_mouse_x_while_aiming'), True),
    ('block_y', ctrl.get('block_physical_mouse_y_while_aiming'), False),
    ('y_fire', ctrl.get('y_axis_fire_hotkey'), 'right'),
    ('y_delay', ctrl.get('y_axis_fire_release_delay_sec'), 0.25),
    ('pc_en', ctrl.get('pull_curve_enabled'), True),
    ('pc_str', ctrl.get('pull_curve_strength'), 0.9),
    ('pc_jit', ctrl.get('pull_curve_jitter_px'), 4.5),
    ('pc_min', ctrl.get('pull_curve_min_distance'), 95),
    ('cl_en', ctrl.get('continuous_lead_enabled'), True),
    ('cl_enter', ctrl.get('continuous_lead_enter_distance'), 160),
    ('cl_scale', ctrl.get('continuous_lead_scale'), 0.6),
    ('cl_fade_in', ctrl.get('continuous_lead_fade_in_ms'), 320),
    ('cl_fade_out', ctrl.get('continuous_lead_fade_out_ms'), 340),
    ('cl_near', ctrl.get('continuous_lead_near_disable_ratio'), 0.7),
    ('p_hotkey', p0.get('hotkey'), 'left'),
    ('p_hotkey2', p0.get('hotkey2'), 'right'),
    ('p_mode', p0.get('hotkey_mode'), 'any'),
    ('p_sens', p0.get('sensitivity'), 1.7),
    ('p_ox', p0.get('offset_x'), 0.48),
    ('p_oy', p0.get('offset_y'), 0.35),
]
passed = 0
failed = 0
for name, got, want in checks:
    if isinstance(want, float):
        ok = got is not None and abs(float(got) - want) < 1e-4
    else:
        ok = got == want
    mark = 'PASS' if ok else 'FAIL'
    if ok: passed += 1
    else: failed += 1
    print(f'  [{mark}] {name}: got={got!r} want={want!r}')

print(f'\n=== 结果: {passed} PASS / {failed} FAIL ===')

# 3. 直查 Core 的 RuntimeProfile（确认 Core 真收到）
print('\n=== 3. Core RuntimeProfile 直查（IPC 层）===')
import socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(3)
s.connect('/tmp/ttbox_core.sock')
s.sendall(json.dumps({'type': 'GET_CONFIG'}).encode() + b'\n')
buf = b''
while b'\n' not in buf:
    chunk = s.recv(65536)
    if not chunk: break
    buf += chunk
resp = json.loads(buf.decode())
core_prof = resp.get('data', {}).get('runtime_profile', {})
if isinstance(core_prof, str): core_prof = json.loads(core_prof)
cm = core_prof.get('mouse', {})
cap = core_prof.get('capture', {})
fov = core_prof.get('fov', {})
# aim_point 在 Core JSON 层平铺为 mouse.offset_x/mouse.offset_y
ap = {'offset_x': cm.get('offset_x'), 'offset_y': cm.get('offset_y')}
pc = cm.get('pull_curve', {})
cl = cm.get('continuous_lead', {})

core_checks = [
    ('sensitivity', cm.get('sensitivity'), 1.7),
    ('output_deadzone', cm.get('output_deadzone'), 2.5),
    ('kp_x', cm.get('kp_x'), 21.5),
    ('predict_x', cm.get('predict_x'), 0.61),
    ('rate_x', cm.get('rate_x'), 0.42),
    ('smooth_x', cm.get('smooth_x'), 9900),
    ('lost_grace_ms', cm.get('lost_grace_ms'), 45),
    ('aim_offset_x', cm.get('aim_offset_x'), 5),
    ('aim_offset_y', cm.get('aim_offset_y'), -3),
    ('aim_fire_lock_y', cm.get('aim_fire_lock_y'), True),
    ('block_physical_x', cm.get('block_physical_x'), True),
    ('y_axis_fire_hotkey', cm.get('y_axis_fire_hotkey'), 2),
    ('ap.offset_x', ap.get('offset_x'), 0.48),
    ('ap.offset_y', ap.get('offset_y'), 0.35),
    ('pc.enabled', pc.get('enabled'), True),
    ('pc.strength', pc.get('strength'), 0.9),
    ('cl.enabled', cl.get('enabled'), True),
    ('cl.enter_distance', cl.get('enter_distance'), 160),
    ('cap.width', cap.get('width'), 320),
    ('cap.offset_x', cap.get('offset_x'), 10),
    ('fov.enabled', fov.get('enabled'), True),
    ('fov.radius', fov.get('radius'), 0.55),
]
cp = 0; cf = 0
for name, got, want in core_checks:
    if isinstance(want, float):
        ok = got is not None and abs(float(got) - want) < 1e-3
    else:
        ok = got == want
    mark = 'PASS' if ok else 'FAIL'
    if ok: cp += 1
    else: cf += 1
    print(f'  [{mark}] {name}: got={got!r} want={want!r}')
print(f'\n=== Core 层结果: {cp} PASS / {cf} FAIL ===')