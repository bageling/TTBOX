#!/usr/bin/env python3
import json, socket, time

def ipc_cmd(req_type, params=None, timeout=3):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
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

# 采样两次（间隔2秒），看 capture_fps 是否稳定在正常值
print('=== 采样1（服务重启后 6s）===')
r = ipc_cmd('GET_STATUS')
d = r.get('data', {}) or {}
m = d.get('metrics', {}) or {}
print('runtime_running:', d.get('runtime_running'))
print('capture_fps:', round(m.get('capture_fps', 0), 2))
print('frames_total:', m.get('frames_total'))
print('infer_total:', m.get('infer_total'))
print('fps(推理):', round(m.get('fps', 0), 2))
print('preview_fps:', round(m.get('preview_fps', 0), 2))
print('preview_frames:', m.get('preview_frames'))
print('preview_bytes:', m.get('preview_bytes'))

time.sleep(2)

print('\n=== 采样2（+2s）===')
r = ipc_cmd('GET_STATUS')
d = r.get('data', {}) or {}
m = d.get('metrics', {}) or {}
print('runtime_running:', d.get('runtime_running'))
print('capture_fps:', round(m.get('capture_fps', 0), 2))
print('frames_total:', m.get('frames_total'))
print('infer_total:', m.get('infer_total'))
print('fps(推理):', round(m.get('fps', 0), 2))
print('preview_fps:', round(m.get('preview_fps', 0), 2))
print('preview_frames:', m.get('preview_frames'))
print('preview_bytes:', m.get('preview_bytes'))

# 预览验证
r = ipc_cmd('GET_PREVIEW')
d2 = r.get('data', {}) or {}
print('\n=== 预览 ===')
print('status:', r.get('status'))
print('has_jpeg:', 'jpeg_base64' in d2)
print('jpeg_len:', len(d2.get('jpeg_base64', '')))