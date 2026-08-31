#!/usr/bin/env python3
import json, socket, time

def ipc_cmd(req_type, params=None):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect('/tmp/ttbox_core.sock')
        payload = {'type': req_type}
        if params: payload['params'] = params
        s.sendall(json.dumps(payload).encode() + b'\n')
        buf = b''
        while b'\n' not in buf:
            chunk = s.recv(65536)
            if not chunk: break
            buf += chunk
        if buf:
            return json.loads(buf.decode())
    except Exception as e:
        return {'status': -1, 'error': str(e)}
    finally:
        s.close()
    return {'status': -1, 'error': 'no response'}

# 先看当前状态
r = ipc_cmd('GET_STATUS')
d = r.get('data', {}) or {}
print('启动前: runtime_running=' + str(d.get('runtime_running')) + ' capture_fps=' + str(d.get('metrics',{}).get('capture_fps')) + ' fps=' + str(d.get('metrics',{}).get('fps')))

# 启动runtime
r = ipc_cmd('RUNTIME_CONTROL', {'action': 'start'})
print('启动结果: status=' + str(r.get('status')) + ' err=' + str(r.get('error','')))

# 等2秒
time.sleep(2)

# 再看状态
r = ipc_cmd('GET_STATUS')
d = r.get('data', {}) or {}
m = d.get('metrics', {}) or {}
print('启动后: runtime_running=' + str(d.get('runtime_running')))
print('  capture_fps=' + str(m.get('capture_fps')))
print('  fps=' + str(m.get('fps')))
print('  frames_total=' + str(m.get('frames_total')))
print('  infer_total=' + str(m.get('infer_total')))
print('  preview_fps=' + str(m.get('preview_fps')))
print('  preview_frames=' + str(m.get('preview_frames')))
print('  preview_bytes=' + str(m.get('preview_bytes')))
print('  e2e_ms=' + str(m.get('e2e_ms')))
print('  aim_active=' + str(m.get('aim_active')))
print('  detect_count=' + str(m.get('detect_count')))

# 测试预览
r = ipc_cmd('GET_PREVIEW')
d2 = r.get('data', {}) or {}
has_jpeg = 'jpeg_base64' in d2
jpeg_len = len(d2.get('jpeg_base64', ''))
print('\\n预览: status=' + str(r.get('status')) + ' has_jpeg=' + str(has_jpeg) + ' len=' + str(jpeg_len))